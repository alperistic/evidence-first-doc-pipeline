"""
tests/test_new_fix6_llm_eckdaten_amount_dedup.py

FIX 6: Wenn LLM-Eckdaten einen Betrag korrekt labeln und derselbe Betrag mit anderem Label
nochmals vorkommt, erscheint er nur einmal (LLM-Reihenfolge: erster Eintrag gewinnt).
"""

import unittest

from core.export_render import _filter_llm_eckdaten


class TestLlmEckdatenAmountDedup(unittest.TestCase):

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
