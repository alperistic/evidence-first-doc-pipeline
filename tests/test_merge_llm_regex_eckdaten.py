"""
tests/test_merge_llm_regex_eckdaten.py

Tests für _merge_llm_regex_eckdaten (core/export_render.py).

Kern-Vertrag: ein generisches Label ("Betrag") trägt keine Information mehr,
sobald ein spezifisches Label denselben Betrag abdeckt ("Kaution: EUR 2.550,00").
In der gerenderten Liste liest sich das als Dublette. Umgekehrt darf ein
generischer Eintrag OHNE spezifisches Gegenstück nicht verschwinden — sonst
geht ein Betrag ganz verloren.

Die Suppression muss quellenunabhängig sein: es darf keine Rolle spielen, ob
das spezifische Label aus den LLM-Items oder den Regex-Evidence-Items stammt.
"""

import unittest

from core.export_render import _merge_llm_regex_eckdaten


DOC_TYPE = "Mietvertrag"


class TestGenerischeDublettenUnterdrueckt(unittest.TestCase):
    """Generische Betrag-Zeilen verschwinden, wenn ein spezifisches Label greift."""

    def setUp(self):
        # Nachstellung des realen Renderer-Outputs für samples/01_mietvertrag_synthetic.pdf
        self.llm = [
            {"label": "Betrag", "wert": "EUR 850,00"},
            {"label": "Betrag", "wert": "EUR 180,00"},
            {"label": "Betrag", "wert": "EUR 2.550,00"},
            {"label": "Betrag", "wert": "EUR 100,00"},
            {"label": "Nebenkosten", "wert": "EUR 180,00"},
            {"label": "Kaution", "wert": "EUR 2.550,00"},
        ]

    def _paare(self, items):
        return {(i.get("label"), i.get("wert")) for i in items}

    def test_generisch_mit_spezifischem_gegenstueck_entfernt(self):
        out = _merge_llm_regex_eckdaten(self.llm, [], DOC_TYPE)
        paare = self._paare(out)
        self.assertNotIn(("Betrag", "EUR 180,00"), paare)
        self.assertNotIn(("Betrag", "EUR 2.550,00"), paare)

    def test_spezifische_labels_bleiben(self):
        out = _merge_llm_regex_eckdaten(self.llm, [], DOC_TYPE)
        paare = self._paare(out)
        self.assertIn(("Nebenkosten", "EUR 180,00"), paare)
        self.assertIn(("Kaution", "EUR 2.550,00"), paare)

    def test_generisch_ohne_gegenstueck_bleibt_erhalten(self):
        """Kein spezifisches Label → Betrag muss bleiben, sonst Informationsverlust."""
        out = _merge_llm_regex_eckdaten(self.llm, [], DOC_TYPE)
        werte = {i.get("wert") for i in out}
        self.assertIn("EUR 850,00", werte)
        self.assertIn("EUR 100,00", werte)

    def test_kein_betrag_geht_verloren(self):
        """Jeder Betrag der Eingabe erscheint noch unter irgendeinem Label."""
        out = _merge_llm_regex_eckdaten(self.llm, [], DOC_TYPE)
        vorher = {i["wert"] for i in self.llm}
        nachher = {i["wert"] for i in out}
        self.assertEqual(vorher, nachher)


class TestQuellenunabhaengigkeit(unittest.TestCase):
    """Das spezifische Label darf auch aus den Regex-Items kommen."""

    def test_spezifisch_nur_aus_regex_unterdrueckt_generisch(self):
        out = _merge_llm_regex_eckdaten(
            [{"label": "Betrag", "wert": "EUR 2.550,00"}],
            [{
                "field": "kaution",
                "value": "EUR 2.550,00",
                "quote": "Die Kaution beträgt EUR 2.550,00",
                "page": 1,
            }],
            DOC_TYPE,
        )
        self.assertFalse(
            [i for i in out if i.get("label") == "Betrag"],
            "generisches Label überlebt, wenn das spezifische erst aus den "
            "Regex-Items kommt → Ergebnis wäre reihenfolgeabhängig",
        )


class TestBestehendeVertraege(unittest.TestCase):
    """Verhalten, das der Fix nicht verändern darf."""

    def test_leere_llm_items_geben_leere_liste(self):
        """Caller unterscheidet daran den Regex-only-Fallback."""
        out = _merge_llm_regex_eckdaten(
            [], [{"field": "kaution", "value": "EUR 1,00", "quote": "", "page": 1}], DOC_TYPE
        )
        self.assertEqual(out, [])

    def test_mietbeginn_relabeling_bleibt_intakt(self):
        out = _merge_llm_regex_eckdaten(
            [{"label": "Mietbeginn", "wert": "01.03.2026"}], [], "Kaufvertrag"
        )
        self.assertEqual([i["label"] for i in out], ["Beginn"])

    def test_mietbeginn_bleibt_in_mietdokument(self):
        out = _merge_llm_regex_eckdaten(
            [{"label": "Mietbeginn", "wert": "01.03.2026"}], [], DOC_TYPE
        )
        self.assertEqual([i["label"] for i in out], ["Mietbeginn"])

    def test_sachlabel_wird_nicht_als_dublette_entfernt(self):
        """"Miete" benennt einen Sachverhalt und darf nie unterdrückt werden.

        Miete 4.200 und Rückstand 4.200 sind ein Monat Mietrückstand — zwei
        Fakten mit zufällig gleichem Betrag, keine Dublette.
        """
        out = _merge_llm_regex_eckdaten(
            [{"label": "Miete", "wert": "EUR 4.200,00"}],
            [{
                "field": "rueckstand",
                "value": "EUR 4.200,00",
                "quote": "Mietrückstand EUR 4.200,00",
                "page": 1,
            }],
            DOC_TYPE,
        )
        labels = [i["label"] for i in out]
        self.assertIn("Miete", labels)
        self.assertIn("Rückstand", labels)

    def test_datumswerte_nicht_von_suppression_betroffen(self):
        """Suppression greift über kanonische Beträge - Datumsangaben bleiben unberührt."""
        out = _merge_llm_regex_eckdaten(
            [
                {"label": "Betrag", "wert": "01.03.2026"},
                {"label": "Kaution", "wert": "01.03.2026"},
            ],
            [],
            DOC_TYPE,
        )
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
