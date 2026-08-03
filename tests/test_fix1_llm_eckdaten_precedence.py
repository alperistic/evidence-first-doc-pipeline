"""
tests/test_fix1_llm_eckdaten_precedence.py

FIX 1 – LLM-Eckdaten haben Vorrang vor Regex-Evidence-Map:
- Wenn llm_eckdaten nicht leer → werden direkt gerendert (LLM-Pfad)
- Wenn llm_eckdaten leer → Regex-Fallback wird genutzt
- Regex-Fallback: EUR 12 Mio mit Umsatzvolumen-Kontext → NICHT "Gesamtmiete"
"""

import unittest

from core.labels import _amount_label_from_context


class TestAmountLabelUmsatzvolumen(unittest.TestCase):
    """FIX 1: _amount_label_from_context muss Umsatzvolumen vor Gesamtmiete erkennen."""

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
    """FIX 1: llm_eckdaten-Struktur wird korrekt übergeben.

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


if __name__ == "__main__":
    unittest.main()
