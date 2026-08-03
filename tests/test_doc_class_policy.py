import unittest

from governance.doc_class_policy import (
    extracted_doc_class_from_payload,
    resolve_display_document_type,
    resolve_doc_class_fields,
    resolve_segment_header_document_type,
    segment_doc_class_from_segment,
)


class DocClassPolicyTests(unittest.TestCase):
    def test_segment_class_stays_authoritative_when_llm_guess_is_disabled(self):
        segment = {"doc_type_guess": "Mahnung"}
        segment_doc_class = segment_doc_class_from_segment(segment)
        analysis_payload = {"document_type": "Klageschrift"}

        fields = resolve_doc_class_fields(
            segment_doc_class=segment_doc_class,
            allow_llm_doc_class_guess=False,
            analysis_payload=analysis_payload,
        )

        self.assertEqual(fields["audit_doc_class"], "Mahnung")
        self.assertEqual(fields["segment_doc_class"], "Mahnung")
        self.assertEqual(fields["doc_class_source"], "segmenter")
        self.assertNotIn("llm_doc_class_guess", fields)

    def test_llm_guess_is_logged_separately_when_enabled(self):
        fields = resolve_doc_class_fields(
            segment_doc_class="Arbeitsvertrag",
            allow_llm_doc_class_guess=True,
            analysis_payload={"document_type": "Klageschrift"},
        )

        self.assertEqual(fields["audit_doc_class"], "Arbeitsvertrag")
        self.assertEqual(fields["segment_doc_class"], "Arbeitsvertrag")
        self.assertEqual(fields["doc_class_source"], "llm")
        self.assertEqual(fields["llm_doc_class_guess"], "Klageschrift")

    def test_display_doc_type_prefers_confident_segmenter_over_llm_unklar(self):
        doc_type, source = resolve_display_document_type(
            segment_doc_class="Klageschrift",
            allow_llm_doc_class_guess=True,
            analysis_payload={"document_type": "Unklar"},
            segment_confidence=0.95,
        )
        self.assertEqual(doc_type, "Klageschrift")
        self.assertEqual(source, "segmenter")

    def test_display_doc_type_can_use_llm_guess_when_segmenter_is_unclear(self):
        doc_type, source = resolve_display_document_type(
            segment_doc_class="Unklar",
            allow_llm_doc_class_guess=True,
            analysis_payload={"document_type": "Mahnung"},
            segment_confidence=0.1,
        )
        self.assertEqual(doc_type, "Mahnung")
        self.assertEqual(source, "llm")

    def test_segment_class_can_be_inferred_from_page_text_when_guess_is_unclear(self):
        segment = {
            "doc_type_guess": "Unklar",
            "title_guess": "Dokument 1",
            "pages": [
                {
                    "page_number": 1,
                    "text": "Arbeitsvertrag zwischen Arbeitgeber und Arbeitnehmer. Beginn 01.05.2026.",
                }
            ],
        }
        self.assertEqual(segment_doc_class_from_segment(segment), "Arbeitsvertrag")

    def test_header_prefers_extracted_doc_type_over_unclear_segment(self):
        doc_type, source = resolve_segment_header_document_type(
            segment={"doc_type_guess": "Unklar", "title_guess": "Dokumentsegment"},
            analysis_payload={"document_type": "Mietvertrag"},
        )
        self.assertEqual(doc_type, "Mietvertrag")
        self.assertEqual(source, "analysis")
        header = f"Dokument 1 (Seite 1-1): {doc_type}"
        self.assertNotIn("Unklar", header)
        self.assertIn("Mietvertrag", header)

    def test_header_uses_segment_mapping_when_analysis_doc_type_missing(self):
        doc_type, source = resolve_segment_header_document_type(
            segment={"doc_type_guess": "Unklar", "title_guess": "Mietvertrag - Entwurf"},
            analysis_payload={},
        )
        self.assertEqual(doc_type, "Mietvertrag")
        self.assertEqual(source, "segmenter")

    def test_extracted_doc_type_prefers_explicit_extracted_field(self):
        value = extracted_doc_class_from_payload(
            {"document_type_extracted": "Mietvertrag", "document_type": "Unklar"}
        )
        self.assertEqual(value, "Mietvertrag")

    def test_segment_override_prefers_ratenzahlungsvereinbarung_over_mahnung(self):
        segment = {
            "doc_type_guess": "Mahnung",
            "title_guess": "Ratenzahlungsvereinbarung",
            "pages": [
                {
                    "page_number": 1,
                    "text": "Ratenzahlungsvereinbarung zwischen den Parteien. Vergleich über offenen Saldo.",
                }
            ],
        }
        self.assertEqual(segment_doc_class_from_segment(segment), "Ratenzahlungsvereinbarung")


if __name__ == "__main__":
    unittest.main()
