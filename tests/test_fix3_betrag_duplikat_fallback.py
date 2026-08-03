"""
tests/test_fix3_betrag_duplikat_fallback.py

FIX 3: _postprocess_eckdaten_items soll generische Labels (Miete, Betrag, …)
unterdrücken, wenn ein spezifisches Label (Gutachtenkosten, Rückstand, …)
denselben kanonischen Betrag abdeckt.

Bug war im Regex-Fallback-Pfad (llm_eckdaten leer): betraege-Item mit
Gutachten-Kontext → "Gutachtenkosten" und miete-Item mit gleichem Betrag →
"Miete" wurden beide gerendert, weil Dedup-Schlüssel unterschiedlich.
"""

import unittest

from core.labels import _postprocess_eckdaten_items


def _ev(field: str, value: str, quote: str = "") -> dict:
    return {"field": field, "value": value, "page": 1, "quote": quote}


class TestPostprocessEckdatenDedup(unittest.TestCase):

    def test_specific_label_suppresses_generic_same_amount(self):
        """betraege(Gutachtenkontext) EUR 1200 + miete EUR 1200 → nur Gutachtenkosten."""
        items = [
            _ev("betraege", "EUR 1.200,00", "Gutachtenkosten EUR 1.200,00"),
            _ev("miete", "EUR 1.200,00", "monatliche Miete EUR 1.200,00"),
        ]
        result = _postprocess_eckdaten_items(items, doc_type="generic")
        labels = []
        from core.labels import _human_label_for_evidence
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
        from core.labels import _human_label_for_evidence
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
        from core.labels import _human_label_for_evidence
        labels = [
            _human_label_for_evidence(
                str(it.get("field", "")), str(it.get("value", "")),
                str(it.get("quote", "")), "mietvertrag"
            )
            for it in result
        ]
        self.assertIn("Rückstand", labels, f"Specific label must be kept. Got: {labels}")
        self.assertNotIn("Miete", labels, f"Generic label must be suppressed. Got: {labels}")


if __name__ == "__main__":
    unittest.main()
