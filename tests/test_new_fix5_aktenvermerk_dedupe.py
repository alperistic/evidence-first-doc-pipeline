"""
tests/test_new_fix5_aktenvermerk_dedupe.py

FIX 5: Fristen-Dedupe wird auch im Aktenvermerk-Pfad (render_segmented_aktenvermerk_text)
angewandt. "15.12.2024" und "15.12.24" dürfen nicht doppelt erscheinen.
"""

import unittest

from core.export_render import render_segmented_aktenvermerk_text


def _make_seg(fristen_items: list[dict]) -> dict:
    return {
        "segment_meta": {"title_guess": "Testdokument", "page_start": 1, "page_end": 2},
        "analysis": {"dokumenttyp_parteien": "Dokumenttyp: Testdokument", "kernaussagen": []},
        "evidence_map": fristen_items,
    }


class TestAktenvermerkFristenDedupe(unittest.TestCase):

    def test_2digit_and_4digit_year_deduplicated(self):
        """15.12.24 und 15.12.2024 dürfen nur einmal im Aktenvermerk erscheinen."""
        seg = _make_seg([
            {"field": "fristen_termine", "value": "15.12.2024", "page": 1, "quote": "Frist A"},
            {"field": "fristen_termine", "value": "15.12.24", "page": 1, "quote": "Frist B"},
        ])
        text = render_segmented_aktenvermerk_text([seg])
        # Count how many Frist/Termin bullet entries appear (not occurrences in quotes)
        entry_count = text.count("– Frist/Termin:")
        self.assertEqual(entry_count, 1, f"Expected 1 entry, got {entry_count} in:\n{text}")

    def test_different_dates_both_appear(self):
        """Unterschiedliche Fristen dürfen beide erscheinen."""
        seg = _make_seg([
            {"field": "fristen_termine", "value": "15.12.2024", "page": 1, "quote": "Frist bis 15.12.2024"},
            {"field": "fristen_termine", "value": "31.03.2025", "page": 2, "quote": "Frist bis 31.03.2025"},
        ])
        text = render_segmented_aktenvermerk_text([seg])
        self.assertIn("15.12.2024", text)
        self.assertIn("31.03.2025", text)

    def test_exact_duplicate_deduplicated(self):
        """Exakte Duplikate (gleicher Wert, gleiche Seite) werden auf einen reduziert."""
        seg = _make_seg([
            {"field": "fristen_termine", "value": "15.12.2024", "page": 1, "quote": "Frist A"},
            {"field": "fristen_termine", "value": "15.12.2024", "page": 1, "quote": "Frist B"},
        ])
        text = render_segmented_aktenvermerk_text([seg])
        entry_count = text.count("– Frist/Termin:")
        self.assertEqual(entry_count, 1)


if __name__ == "__main__":
    unittest.main()
