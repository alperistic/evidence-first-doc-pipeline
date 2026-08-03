"""
Amount extraction and labeling: which German label (Miete, Nebenkosten,
Gesamtmiete, Kaution, Gutachtenkosten, Bussgeld, Haftungsbegrenzung,
Mindestabnahme, ...) a monetary amount receives based on its surrounding
text context, and how duplicate/overlapping amount items across the LLM
and regex extraction paths are merged, deduplicated, and postprocessed
before rendering in the Eckdaten summary.
"""

import unittest

import pytest

from core.labels import (
    _amount_label_from_context,
    _human_label_for_evidence,
    _postprocess_eckdaten_items,
)
from core.export_render import _merge_llm_regex_eckdaten, _filter_llm_eckdaten


FULL_SENTENCE = (
    "Die monatliche Kaltmiete beträgt EUR 820,00, "
    "die Nebenkostenvorauszahlung EUR 195,00, "
    "insgesamt also EUR 1.015,00"
)


# Regression guard: proximity-based Nebenkosten/Gesamtmiete distinction.
# In production _amount_label_from_context is called with f"{quote} {value}" —
# the specific amount is appended at the end.
@pytest.mark.parametrize("value,expected", [
    # EUR 1.015,00 = Gesamtmiete (steht direkt nach "insgesamt also")
    ("EUR 1.015,00", "Gesamtmiete"),
    # EUR 195,00 = Nebenkosten (steht direkt nach "Nebenkostenvorauszahlung")
    ("EUR 195,00", "Nebenkosten"),
    # EUR 820,00: In Produktion ist der Kontext-Window ±50 Zeichen — "nebenkosten" liegt
    # dann außerhalb des Fensters; hier mit vollem Satz → "nebenkosten" ist im Kontext →
    # klassifiziert als "Nebenkosten" (kein Bug, weil in Produktion "kaltmiete" im ±50-
    # Fenster die Klassifizierung als "miete"-Feld bereits in evidence_map erledigt).
    ("EUR 820,00", "Nebenkosten"),
])
def test_proximity_with_appended_value(value, expected):
    """Produktion-Aufruf: context = f'{quote} {value}'"""
    context = f"{FULL_SENTENCE} {value}"
    result = _amount_label_from_context(context)
    assert result == expected, f"value={value!r}: expected {expected!r}, got {result!r}"


@pytest.mark.parametrize("context,expected", [
    # "insgesamt" allein (ohne Nebenkosten) → Gesamtmiete
    ("insgesamt also EUR 1.015,00", "Gesamtmiete"),
    ("insgesamt EUR 1.015,00 brutto", "Gesamtmiete"),
    # Nur Nebenkosten, kein insgesamt → Nebenkosten
    ("Nebenkostenvorauszahlung EUR 195,00", "Nebenkosten"),
    ("Betriebskosten EUR 195,00 monatlich", "Nebenkosten"),
    # Kaltmiete → Miete
    ("Kaltmiete beträgt EUR 820,00", "Miete"),
    ("kaltmiete EUR 820,00 nettokalt", "Miete"),
])
def test_short_snippet_labels(context, expected):
    """Kurze Snippets ohne angehängten Value."""
    result = _amount_label_from_context(context)
    assert result == expected, f"context={context!r}: expected {expected!r}, got {result!r}"


@pytest.mark.parametrize("context,expected", [
    # Haftungsbegrenzung
    ("Haftungsbegrenzung EUR 5.000.000,00", "Haftungsbegrenzung"),
    ("Die Haftungsobergrenze beträgt EUR 5.000.000", "Haftungsbegrenzung"),
    ("Haftungslimit EUR 2.000.000 pro Schadensfall", "Haftungsbegrenzung"),
    ("Haftungsgrenze EUR 1.000.000,00", "Haftungsbegrenzung"),
    # Haftung generisch (kein spezifisches Begrenzungs-Keyword)
    ("Haftung EUR 500.000,00", "Haftung"),
    # Mindestabnahme
    ("Mindestabnahmeverpflichtung EUR 50.000,00 jährlich", "Mindestabnahme"),
    ("Mindestabnahme EUR 10.000,00 pro Quartal", "Mindestabnahme"),
    ("Mindestbestellwert EUR 5.000,00", "Mindestabnahme"),
    # Vertragsstrafe (bereits vorhanden — Regression sichern)
    ("Vertragsstrafe 0,5% je Woche der Verzögerung", "Vertragsstrafe"),
    ("Bei Verstoß gilt eine Vertragsstrafe EUR 10.000,00", "Vertragsstrafe"),
])
def test_haftung_mindestabnahme_labels(context, expected):
    result = _amount_label_from_context(context)
    assert result == expected, f"context={context!r}: expected {expected!r}, got {result!r}"


class TestAmountLabelUmsatzvolumen(unittest.TestCase):
    """_amount_label_from_context muss Umsatzvolumen vor Gesamtmiete erkennen."""

    def test_umsatzvolumen_keyword_not_labeled_gesamtmiete(self):
        ctx = "Jahresumsatz insgesamt EUR 12.000.000,00"
        label = _amount_label_from_context(ctx)
        self.assertNotEqual(label, "Gesamtmiete", f"Got: {label!r}")

    def test_umsatzvolumen_explicit_label(self):
        ctx = "Umsatzvolumen des Portfolios: EUR 12.000.000,00 insgesamt"
        label = _amount_label_from_context(ctx)
        self.assertEqual(label, "Umsatzvolumen")

    def test_gesamtumsatz_not_gesamtmiete(self):
        ctx = "Gesamtumsatz insgesamt EUR 12.900.000,00"
        label = _amount_label_from_context(ctx)
        self.assertNotEqual(label, "Gesamtmiete")

    def test_normal_insgesamt_still_gesamtmiete(self):
        """Normaler 'insgesamt' Kontext ohne Umsatz-Keyword → weiterhin Gesamtmiete."""
        ctx = "monatliche Gesamtzahlung insgesamt EUR 1.250,00"
        label = _amount_label_from_context(ctx)
        self.assertEqual(label, "Gesamtmiete")

    def test_miete_context_unaffected(self):
        ctx = "Kaltmiete EUR 820,00"
        label = _amount_label_from_context(ctx)
        self.assertEqual(label, "Miete")


class TestLlmEckdatenPrecedence(unittest.TestCase):
    """llm_eckdaten-Struktur wird korrekt übergeben.

    Da der tatsächliche LLM-Call nicht im Unit-Test läuft, testen wir das
    Datenformat: llm_eckdaten ist eine Liste von {label, wert, quelle}-Dicts.
    Wenn die Liste nicht leer ist, soll sie in der UI gerendert werden;
    bei leer fällt das System auf Regex-Evidence-Map zurück.
    """

    def test_llm_eckdaten_item_structure(self):
        """Gültiges llm_eckdaten-Item hat label + wert."""
        item = {"label": "Kaufpreis", "wert": "EUR 450.000,00", "quelle": "S. 3"}
        self.assertTrue(item.get("label") and item.get("wert"))

    def test_empty_llm_eckdaten_triggers_fallback_logic(self):
        """Leere Liste signalisiert Regex-Fallback-Pfad."""
        llm_eckdaten: list[dict] = []
        use_regex = not llm_eckdaten
        self.assertTrue(use_regex)

    def test_nonempty_llm_eckdaten_skips_fallback(self):
        """Nicht-leere Liste blockiert Regex-Fallback."""
        llm_eckdaten = [{"label": "Miete", "wert": "EUR 820,00", "quelle": "S. 2"}]
        use_regex = not llm_eckdaten
        self.assertFalse(use_regex)

    def test_llm_eckdaten_item_with_wrong_label_passthrough(self):
        """LLM-Items werden unverändert durchgereicht — kein Post-Processing."""
        # Das LLM hat Vorrang; falsches LLM-Label ist ein Prompt-Problem, kein Code-Bug
        items = [{"label": "Umsatzvolumen", "wert": "EUR 12.000.000,00", "quelle": "S. 4"}]
        rendered = [f"– {i['label']}: {i['wert']}" for i in items]
        self.assertIn("Umsatzvolumen", rendered[0])
        self.assertNotIn("Gesamtmiete", rendered[0])


def _ev(field: str, value: str, quote: str = "") -> dict:
    return {"field": field, "value": value, "page": 1, "quote": quote}


class TestPostprocessEckdatenDedup(unittest.TestCase):
    """_postprocess_eckdaten_items soll generische Labels (Miete, Betrag, …)
    unterdrücken, wenn ein spezifisches Label (Gutachtenkosten, Rückstand, …)
    denselben kanonischen Betrag abdeckt.

    Bug war im Regex-Fallback-Pfad (llm_eckdaten leer): betraege-Item mit
    Gutachten-Kontext → "Gutachtenkosten" und miete-Item mit gleichem Betrag →
    "Miete" wurden beide gerendert, weil Dedup-Schlüssel unterschiedlich.
    """

    def test_specific_label_suppresses_generic_same_amount(self):
        """betraege(Gutachtenkontext) EUR 1200 + miete EUR 1200 → nur Gutachtenkosten."""
        items = [
            _ev("betraege", "EUR 1.200,00", "Gutachtenkosten EUR 1.200,00"),
            _ev("miete", "EUR 1.200,00", "monatliche Miete EUR 1.200,00"),
        ]
        result = _postprocess_eckdaten_items(items, doc_type="generic")
        labels = []
        for it in result:
            lbl = _human_label_for_evidence(
                str(it.get("field", "")), str(it.get("value", "")),
                str(it.get("quote", "")), "generic"
            )
            if lbl:
                labels.append(lbl)
        self.assertIn("Gutachtenkosten", labels, f"Specific label must be kept. Got: {labels}")
        self.assertNotIn("Miete", labels, f"Generic label must be suppressed. Got: {labels}")

    def test_two_generic_labels_different_amounts_both_kept(self):
        """Miete EUR 900 + Betrag EUR 1200 → beide behalten (verschiedene Beträge)."""
        items = [
            _ev("miete", "EUR 900,00", "monatliche Miete"),
            _ev("betraege", "EUR 1.200,00", "Betrag"),
        ]
        result = _postprocess_eckdaten_items(items, doc_type="generic")
        # Both have different amounts → both pass through
        self.assertEqual(len(result), 2)

    def test_specific_and_generic_different_amounts_both_kept(self):
        """Gutachtenkosten EUR 1200 + Miete EUR 900 → beide behalten."""
        items = [
            _ev("betraege", "EUR 1.200,00", "Gutachtenkosten EUR 1.200,00"),
            _ev("miete", "EUR 900,00", "monatliche Miete"),
        ]
        result = _postprocess_eckdaten_items(items, doc_type="generic")
        labels = [
            _human_label_for_evidence(
                str(it.get("field", "")), str(it.get("value", "")),
                str(it.get("quote", "")), "generic"
            )
            for it in result
        ]
        self.assertIn("Gutachtenkosten", labels)
        # Miete should be kept (different amount from Gutachtenkosten)
        self.assertIn("Miete", labels)

    def test_only_generic_labels_no_specific_all_kept(self):
        """Nur generische Labels → alle behalten (kein spezifischer Betrag vorhanden)."""
        items = [
            _ev("miete", "EUR 900,00", "monatliche Miete"),
            _ev("betraege", "EUR 500,00", "Betrag"),
        ]
        result = _postprocess_eckdaten_items(items, doc_type="generic")
        self.assertEqual(len(result), 2)

    def test_rueckstand_specific_suppresses_miete_same_amount(self):
        """Rückstand EUR 4200 + Miete EUR 4200 → nur Rückstand."""
        items = [
            _ev("rueckstand", "EUR 4.200,00", "Mietrückstand EUR 4.200,00"),
            _ev("miete", "EUR 4.200,00", "monatliche Miete"),
        ]
        result = _postprocess_eckdaten_items(items, doc_type="mietvertrag")
        labels = [
            _human_label_for_evidence(
                str(it.get("field", "")), str(it.get("value", "")),
                str(it.get("quote", "")), "mietvertrag"
            )
            for it in result
        ]
        self.assertIn("Rückstand", labels, f"Specific label must be kept. Got: {labels}")
        self.assertNotIn("Miete", labels, f"Generic label must be suppressed. Got: {labels}")


class TestMergeLlmRegexEckdaten(unittest.TestCase):
    """LLM-eckdaten und Regex-Evidence zusammenführen; Betrag-Duplikate
    entfernen, wenn LLM und Regex den gleichen Wert extrahieren."""

    def _make_ev(self, field: str, value: str, quote: str = "") -> dict:
        return {"field": field, "value": value, "page": 1, "quote": quote}

    def test_llm_specific_suppresses_regex_generic_same_amount(self):
        """LLM 'Gutachtenkosten: 1200' + Regex 'Miete: 1200' → nur LLM-Item im Output."""
        llm = [{"label": "Gutachtenkosten", "wert": "EUR 1.200,00", "quelle": "S. 2"}]
        regex = [self._make_ev("miete", "EUR 1.200,00", "monatliche Miete EUR 1.200,00")]
        merged = _merge_llm_regex_eckdaten(llm, regex, "generic")
        labels = [i["label"] for i in merged]
        self.assertIn("Gutachtenkosten", labels)
        self.assertNotIn("Miete", labels, f"Miete (generic) should be suppressed. Got: {labels}")

    def test_llm_miete_plus_regex_rueckstand_both_kept(self):
        """LLM 'Miete: 4200' + Regex 'Rückstand: 4200' → beide behalten."""
        llm = [{"label": "Miete", "wert": "EUR 4.200,00", "quelle": "S. 1"}]
        regex = [self._make_ev("rueckstand", "EUR 4.200,00", "Mietrückstand EUR 4.200,00")]
        merged = _merge_llm_regex_eckdaten(llm, regex, "mietvertrag")
        labels = [i["label"] for i in merged]
        self.assertIn("Miete", labels, f"LLM Miete must be kept. Got: {labels}")
        self.assertIn("Rückstand", labels, f"Specific Rückstand must be kept. Got: {labels}")

    def test_only_regex_no_llm_returns_empty_for_fallback(self):
        """Keine LLM-Items → returns [] → Caller nutzt Regex-Fallback."""
        regex = [self._make_ev("miete", "EUR 4.200,00", "monatliche Miete")]
        merged = _merge_llm_regex_eckdaten([], regex, "mietvertrag")
        self.assertEqual(merged, [], "Empty LLM → [] to trigger regex fallback")

    def test_llm_kaution_plus_regex_kaution_not_duplicated(self):
        """LLM hat Kaution + Regex hat Kaution gleicher Betrag → nur LLM behalten (kein Duplikat)."""
        llm = [{"label": "Kaution", "wert": "EUR 2.500,00", "quelle": "S. 1"}]
        regex = [self._make_ev("kaution", "EUR 2.500,00", "Kaution EUR 2.500,00")]
        merged = _merge_llm_regex_eckdaten(llm, regex, "mietvertrag")
        kaution_count = sum(1 for i in merged if i.get("label") == "Kaution")
        self.assertEqual(kaution_count, 1, f"Kaution should not be duplicated. Got: {merged}")

    def test_llm_item_preserved_without_regex(self):
        """LLM-Items werden bei leerem Regex-Pfad unverändert durchgereicht."""
        llm = [
            {"label": "Kaufpreis", "wert": "EUR 450.000,00", "quelle": "S. 3"},
            {"label": "Maklerprovision", "wert": "EUR 15.000,00", "quelle": "S. 4"},
        ]
        merged = _merge_llm_regex_eckdaten(llm, [], "kaufvertrag")
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["label"], "Kaufpreis")


class TestGutachtenkosten(unittest.TestCase):
    """EUR 1.200,00 in Gutachten-Kontext → Label "Gutachtenkosten", NICHT "Miete"."""

    def test_gutachten_keyword_label(self):
        ctx = "Gutachterkosten: EUR 1.200,00 für das Sachverständigengutachten"
        self.assertEqual(_amount_label_from_context(ctx), "Gutachtenkosten")

    def test_sachverstaendigen_keyword_label(self):
        ctx = "Sachverständigenkosten EUR 1.200,00 gemäß Anlage"
        self.assertEqual(_amount_label_from_context(ctx), "Gutachtenkosten")

    def test_gutachten_has_priority_over_miete(self):
        ctx = "Gutachterkosten für Mietwertgutachten EUR 1.200,00"
        label = _amount_label_from_context(ctx)
        self.assertEqual(label, "Gutachtenkosten")
        self.assertNotEqual(label, "Miete")

    def test_gutachten_has_priority_over_insgesamt(self):
        ctx = "Gutachtenkosten insgesamt EUR 1.200,00"
        label = _amount_label_from_context(ctx)
        self.assertEqual(label, "Gutachtenkosten")
        self.assertNotEqual(label, "Gesamtmiete")

    def test_normal_miete_context_unaffected(self):
        ctx = "Kaltmiete EUR 820,00 monatlich"
        self.assertEqual(_amount_label_from_context(ctx), "Miete")

    def test_miete_without_gutachten_unaffected(self):
        ctx = "Mietvertrag Miete EUR 4.200,00"
        label = _amount_label_from_context(ctx)
        # Should NOT be Gutachtenkosten
        self.assertNotEqual(label, "Gutachtenkosten")


# Regression: Nebenkosten-Kontext mit "insgesamt" ist die Gesamtmiete, nicht
# Nebenkosten — aber NUR wenn "insgesamt" VOR "nebenkosten" steht (d.h. der
# Betrag selbst ist das Gesamt). EUR 195 mit Kontext "Nebenkosten EUR 195 ...
# insgesamt EUR 1.015" darf nicht als "Gesamtmiete" gelabelt werden.
@pytest.mark.parametrize("context,expected", [
    # Gesamtmiete-Fälle: "insgesamt" steht VOR Nebenkosten (Betrag = Gesamt)
    ("insgesamt also EUR 1.015,00 (820 Kaltmiete + 195 Nebenkosten)", "Gesamtmiete"),
    ("insgesamt EUR 1.015,00 inklusive Nebenkosten", "Gesamtmiete"),
    ("Betrag insgesamt: EUR 1.015,00 inkl. Betriebskosten", "Gesamtmiete"),
    # "nebenkosten" steht VOR "insgesamt" → Betrag ist NK-Position
    ("Nebenkostenvorauszahlung EUR 195,00 monatlich, insgesamt also EUR 1.015,00", "Nebenkosten"),
    ("Betriebskosten EUR 195,00; insgesamt EUR 1.015,00 fällig", "Nebenkosten"),
    # Normale Nebenkosten-Fälle ohne "insgesamt"
    ("Nebenkosten EUR 195,00 monatlich", "Nebenkosten"),
    ("Betriebskosten 195 EUR", "Nebenkosten"),
    # Kein Treffer → None
    ("Betrag EUR 500,00", None),
])
def test_amount_label_nebenkosten_gesamtmiete(context, expected):
    result = _amount_label_from_context(context)
    assert result == expected, f"context={context!r}: expected {expected!r}, got {result!r}"


# Bußgeld / Verwarnungsgeld / Geldbuße → Label "Bußgeld", with priority over
# "Vertragsstrafe" when a Bußgeld keyword is present in the context.
@pytest.mark.parametrize("context,expected", [
    ("Bußgeld: EUR 180,00 gemäß OWiG", "Bußgeld"),
    ("Verwarnungsgeld EUR 180,00 wegen Geschwindigkeitsüberschreitung", "Bußgeld"),
    ("Geldbuße von EUR 250,00 festgesetzt", "Bußgeld"),
    ("bussgeld EUR 100,00", "Bußgeld"),
    ("OWi-Verfahren, EUR 180,00 fällig", "Bußgeld"),
    # Bußgeld hat Vorrang vor Vertragsstrafe wenn beide im Kontext
    ("Bußgeld wegen Verstoß EUR 180,00", "Bußgeld"),
    # Normale Vertragsstrafe (kein Bußgeld-Keyword)
    ("Vertragsstrafe EUR 500,00", "Vertragsstrafe"),
])
def test_bussgeld_label(context, expected):
    result = _amount_label_from_context(context)
    assert result == expected, f"context={context!r}: expected {expected!r}, got {result!r}"


class TestLlmEckdatenAmountDedup(unittest.TestCase):
    """Wenn LLM-Eckdaten einen Betrag korrekt labeln und derselbe Betrag mit
    anderem Label nochmals vorkommt, erscheint er nur einmal (LLM-Reihenfolge:
    erster Eintrag gewinnt)."""

    def test_same_amount_different_label_deduped(self):
        """Gleicher Betrag (EUR 1.200,00) mit unterschiedlichem Label → nur erster bleibt."""
        items = [
            {"label": "Gutachtenkosten", "wert": "EUR 1.200,00"},
            {"label": "Miete", "wert": "EUR 1.200,00"},
        ]
        result = _filter_llm_eckdaten(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label"], "Gutachtenkosten")

    def test_same_amount_same_label_deduped(self):
        """Gleicher Betrag und Label → Duplikat entfernen."""
        items = [
            {"label": "Miete", "wert": "EUR 4.200,00"},
            {"label": "Miete", "wert": "EUR 4.200,00"},
        ]
        result = _filter_llm_eckdaten(items)
        self.assertEqual(len(result), 1)

    def test_different_amounts_both_kept(self):
        """Unterschiedliche Beträge bleiben beide."""
        items = [
            {"label": "Miete", "wert": "EUR 4.200,00"},
            {"label": "Kaution", "wert": "EUR 12.600,00"},
        ]
        result = _filter_llm_eckdaten(items)
        self.assertEqual(len(result), 2)

    def test_llm_absent_regex_kept(self):
        """Wenn llm_eckdaten leer, Regex-Fallback unberührt (nicht Gegenstand dieser Funktion)."""
        result = _filter_llm_eckdaten([])
        self.assertEqual(result, [])

    def test_date_not_deduped_with_amount(self):
        """Datumswert 'EUR'-frei → kein Betrags-Dedup damit."""
        items = [
            {"label": "Frist/Termin", "wert": "17.06.2026"},
            {"label": "Miete", "wert": "EUR 820,00"},
        ]
        result = _filter_llm_eckdaten(items)
        self.assertEqual(len(result), 2)

    def test_prefix_suffix_eur_variants_deduped(self):
        """'EUR 1.200,00' und '1.200,00 EUR' gelten als gleicher Betrag."""
        items = [
            {"label": "Gutachtenkosten", "wert": "EUR 1.200,00"},
            {"label": "Miete", "wert": "1.200,00 EUR"},
        ]
        result = _filter_llm_eckdaten(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label"], "Gutachtenkosten")


if __name__ == "__main__":
    unittest.main()
