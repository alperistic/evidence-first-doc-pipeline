"""
tests/test_fix3_de_dedupe.py

FIX 3: render_segmented_aktenvermerk_text darf in Sektion E (Rückfragen)
keine Punkte wiederholen die schon in Sektion D (Unklarheiten) stehen.
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
