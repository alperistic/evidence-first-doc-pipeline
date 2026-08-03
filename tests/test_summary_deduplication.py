"""
Deduplication in the rendered case-memo (Aktenvermerk) output: suppressing
"Rückfragen an Mandant" (section E) items that already appear as
"Unklarheiten" (section D) items — exact and fuzzy-similar matches — and
deduplicating deadline/date entries that only differ by 2-digit vs. 4-digit
year formatting so the same deadline is not listed twice.
"""

import unittest

from core.export_render import render_segmented_aktenvermerk_text


def _make_seg(auffaelligkeiten, missing_info):
    return {
        "segment_meta": {
            "title_guess": "Testdokument",
            "page_start": 1,
            "page_end": 3,
            "doc_type_guess": "Unklar",
        },
        "analysis": {
            "document_type_extracted": "Testvertrag",
            "auffaelligkeiten": [{"text": t, "citations": []} for t in auffaelligkeiten],
            "missing_info": missing_info,
            "kernaussagen": [],
            "parties": [],
            "todos": [],
            "prueffragen": [],
        },
        "evidence_map": [],
    }


class DeDupeDE_Fix3Tests(unittest.TestCase):
    """render_segmented_aktenvermerk_text darf in Sektion E (Rückfragen)
    keine Punkte wiederholen die schon in Sektion D (Unklarheiten) stehen."""

    def test_identical_item_not_repeated_in_e(self):
        """A string identical in D must not appear in E."""
        dup = "Unterschrift fehlt im Dokument."
        seg = _make_seg(
            auffaelligkeiten=[dup],
            missing_info=[dup],
        )
        output = render_segmented_aktenvermerk_text([seg])
        # Count occurrences – must appear exactly once
        count = output.count(dup)
        self.assertEqual(count, 1, f"Expected 1 occurrence, got {count}:\n{output}")

    def test_fuzzy_similar_item_not_repeated_in_e(self):
        """A nearly identical string (high similarity) must not appear in E."""
        text_d = "Angabe zum Streitwert fehlt im Dokument"
        text_e = "Angabe zum Streitwert fehlt im Dokument."  # trailing dot
        seg = _make_seg(
            auffaelligkeiten=[text_d],
            missing_info=[text_e],
        )
        output = render_segmented_aktenvermerk_text([seg])
        # text_e should be suppressed because it is very similar to text_d
        self.assertNotIn(text_e, output)

    def test_distinct_item_in_e_is_kept(self):
        """An E-item that is genuinely different from all D-items must remain."""
        seg = _make_seg(
            auffaelligkeiten=["Datum fehlt."],
            missing_info=["Bitte Kontonummer des Mandanten nachreichen."],
        )
        output = render_segmented_aktenvermerk_text([seg])
        self.assertIn("Bitte Kontonummer des Mandanten nachreichen.", output)

    def test_empty_d_keeps_all_e(self):
        """If D is empty, all E-items must still be rendered."""
        seg = _make_seg(
            auffaelligkeiten=[],
            missing_info=["Fehlende Information A", "Fehlende Information B"],
        )
        output = render_segmented_aktenvermerk_text([seg])
        self.assertIn("Fehlende Information A", output)
        self.assertIn("Fehlende Information B", output)

    def test_empty_e_after_dedupe_shows_keine(self):
        """If all E-items are deduped, the section shows '– keine'."""
        dup = "Vollmacht liegt nicht vor"
        seg = _make_seg(
            auffaelligkeiten=[dup],
            missing_info=[dup],
        )
        output = render_segmented_aktenvermerk_text([seg])
        # E section must show "– keine"
        e_idx = output.find("E RÜCKFRAGEN AN MANDANT")
        self.assertGreater(e_idx, -1)
        e_section = output[e_idx:]
        self.assertIn("– keine", e_section)


def _make_frist_seg(fristen_items: list[dict]) -> dict:
    return {
        "segment_meta": {"title_guess": "Testdokument", "page_start": 1, "page_end": 2},
        "analysis": {"dokumenttyp_parteien": "Dokumenttyp: Testdokument", "kernaussagen": []},
        "evidence_map": fristen_items,
    }


class TestAktenvermerkFristenDedupe(unittest.TestCase):
    """Fristen-Dedupe wird auch im Aktenvermerk-Pfad
    (render_segmented_aktenvermerk_text) angewandt. "15.12.2024" und
    "15.12.24" dürfen nicht doppelt erscheinen."""

    def test_2digit_and_4digit_year_deduplicated(self):
        """15.12.24 und 15.12.2024 dürfen nur einmal im Aktenvermerk erscheinen."""
        seg = _make_frist_seg([
            {"field": "fristen_termine", "value": "15.12.2024", "page": 1, "quote": "Frist A"},
            {"field": "fristen_termine", "value": "15.12.24", "page": 1, "quote": "Frist B"},
        ])
        text = render_segmented_aktenvermerk_text([seg])
        # Count how many Frist/Termin bullet entries appear (not occurrences in quotes)
        entry_count = text.count("– Frist/Termin:")
        self.assertEqual(entry_count, 1, f"Expected 1 entry, got {entry_count} in:\n{text}")

    def test_different_dates_both_appear(self):
        """Unterschiedliche Fristen dürfen beide erscheinen."""
        seg = _make_frist_seg([
            {"field": "fristen_termine", "value": "15.12.2024", "page": 1, "quote": "Frist bis 15.12.2024"},
            {"field": "fristen_termine", "value": "31.03.2025", "page": 2, "quote": "Frist bis 31.03.2025"},
        ])
        text = render_segmented_aktenvermerk_text([seg])
        self.assertIn("15.12.2024", text)
        self.assertIn("31.03.2025", text)

    def test_exact_duplicate_deduplicated(self):
        """Exakte Duplikate (gleicher Wert, gleiche Seite) werden auf einen reduziert."""
        seg = _make_frist_seg([
            {"field": "fristen_termine", "value": "15.12.2024", "page": 1, "quote": "Frist A"},
            {"field": "fristen_termine", "value": "15.12.2024", "page": 1, "quote": "Frist B"},
        ])
        text = render_segmented_aktenvermerk_text([seg])
        entry_count = text.count("– Frist/Termin:")
        self.assertEqual(entry_count, 1)


if __name__ == "__main__":
    unittest.main()
