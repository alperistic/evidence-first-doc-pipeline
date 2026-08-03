"""
tests/test_fix3_deadline_dedup.py

FIX 3: Kündigungsfrist-Dedupe — "15.12.24" und "15.12.2024" dürfen nicht
als separate Einträge erscheinen.
"""

import unittest

from core.labels import _dedupe_summary_items, _normalize_date_value_for_dedup


class TestNormalizeDateValueForDedup(unittest.TestCase):
    def test_2digit_year_expanded(self):
        self.assertEqual(_normalize_date_value_for_dedup("15.12.24"), "15.12.2024")

    def test_4digit_year_unchanged(self):
        self.assertEqual(_normalize_date_value_for_dedup("15.12.2024"), "15.12.2024")

    def test_year_70_boundary(self):
        """Year 70 → 1970, year 69 → 2069."""
        self.assertEqual(_normalize_date_value_for_dedup("01.01.70"), "01.01.1970")
        self.assertEqual(_normalize_date_value_for_dedup("01.01.69"), "01.01.2069")

    def test_non_date_string_unchanged(self):
        self.assertEqual(_normalize_date_value_for_dedup("EUR 1.250,00"), "EUR 1.250,00")


class TestDedupeSummaryItemsDateNormalization(unittest.TestCase):
    """FIX 3: _dedupe_summary_items treats 2- and 4-digit year variants as duplicates."""

    def test_15_12_24_and_15_12_2024_deduped(self):
        items = [
            {"field": "fristen_termine", "value": "15.12.24", "page": "3"},
            {"field": "fristen_termine", "value": "15.12.2024", "page": "3"},
        ]
        result = _dedupe_summary_items(items)
        self.assertEqual(len(result), 1)
        # First item wins
        self.assertEqual(result[0]["value"], "15.12.24")

    def test_different_dates_not_deduped(self):
        items = [
            {"field": "fristen_termine", "value": "15.12.2024", "page": "3"},
            {"field": "fristen_termine", "value": "31.12.2024", "page": "3"},
        ]
        result = _dedupe_summary_items(items)
        self.assertEqual(len(result), 2)

    def test_same_date_different_page_not_deduped(self):
        items = [
            {"field": "fristen_termine", "value": "15.12.2024", "page": "3"},
            {"field": "fristen_termine", "value": "15.12.2024", "page": "5"},
        ]
        result = _dedupe_summary_items(items)
        self.assertEqual(len(result), 2)

    def test_amount_values_unaffected(self):
        items = [
            {"field": "betraege", "value": "EUR 1.250,00", "page": "2"},
            {"field": "betraege", "value": "EUR 1.250,00", "page": "2"},
        ]
        result = _dedupe_summary_items(items)
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
