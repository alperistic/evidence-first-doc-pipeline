"""
tests/test_fix4_betrag_duplikat.py

FIX 4: Betrag-Duplikat wenn LLM und Regex gleichen Wert extrahieren.
"""

import unittest

from core.export_render import _merge_llm_regex_eckdaten


class TestMergeLlmRegexEckdaten(unittest.TestCase):
    """FIX 4: LLM-eckdaten und Regex-Evidence zusammenführen."""

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


if __name__ == "__main__":
    unittest.main()
