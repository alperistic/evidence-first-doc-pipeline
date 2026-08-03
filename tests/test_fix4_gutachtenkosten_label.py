"""
tests/test_fix4_gutachtenkosten_label.py

FIX 4: EUR 1.200,00 in Gutachten-Kontext → Label "Gutachtenkosten", NICHT "Miete".
"""

import unittest

from core.labels import _amount_label_from_context


class TestGutachtenkosten(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
