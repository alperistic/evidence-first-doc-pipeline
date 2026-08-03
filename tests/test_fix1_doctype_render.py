"""
tests/test_fix1_doctype_render.py

FIX 1: extract_document_type_for_render soll bei vorhandenem document_type_extracted
den LLM-Wert zeigen statt den Heuristik-Wert aus dokumenttyp_parteien.
"""
import unittest

from core.export_render import extract_document_type_for_render


class DoctypeRenderFix1Tests(unittest.TestCase):
    def test_document_type_extracted_takes_priority_over_heuristic(self):
        """LLM-extracted doctype beats dokumenttyp_parteien heuristic."""
        parsed = {
            "document_type_extracted": "Scheidungsfolgenvereinbarung",
            "dokumenttyp_parteien": "Dokumenttyp: Mietvertrag\nVermieter: ...",
        }
        result = extract_document_type_for_render(parsed)
        self.assertEqual(result, "Scheidungsfolgenvereinbarung")

    def test_document_type_extracted_beats_document_type_field(self):
        """document_type_extracted has priority over plain document_type."""
        parsed = {
            "document_type_extracted": "Testament",
            "document_type": "Unklar",
        }
        result = extract_document_type_for_render(parsed)
        self.assertEqual(result, "Testament")

    def test_unklar_extracted_falls_through_to_document_type(self):
        """'Unklar' in document_type_extracted is not used; falls back to document_type."""
        parsed = {
            "document_type_extracted": "Unklar",
            "document_type": "Klageschrift",
        }
        result = extract_document_type_for_render(parsed)
        self.assertEqual(result, "Klageschrift")

    def test_missing_extracted_falls_through_to_heuristic(self):
        """Empty document_type_extracted falls back to dokumenttyp_parteien."""
        parsed = {
            "document_type_extracted": "",
            "document_type": "",
            "dokumenttyp_parteien": "Dokumenttyp: Mietvertrag",
        }
        result = extract_document_type_for_render(parsed)
        self.assertEqual(result, "Mietvertrag")

    def test_no_llm_fields_returns_missing(self):
        """No LLM fields and no heuristic → 'missing'."""
        result = extract_document_type_for_render({})
        self.assertEqual(result, "missing")
