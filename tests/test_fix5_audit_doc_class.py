"""
tests/test_fix5_audit_doc_class.py

FIX 5: Dokumentklasse im Audit-Block muss aus den tatsächlichen LLM-analysierten
doc_type-Werten der Segmente kommen, nicht aus dem Segmenter-Guess.
"""

import unittest


def _build_doc_class_from_segment_results(segment_results):
    """Repliziert die doc_class-Aggregation aus services/analysis_service.py."""
    from governance.doc_class_policy import normalize_doc_class
    return ", ".join(
        sorted(
            {
                normalize_doc_class(
                    (item.get("doc_class_fields", {}) or {}).get("llm_doc_class_guess")
                    or (item.get("doc_class_fields", {}) or {}).get("segment_doc_class")
                )
                for item in segment_results
            }
        )
    ) or "Unklar"


class TestAuditDocClass(unittest.TestCase):
    """FIX 5: llm_doc_class_guess hat Vorrang über segment_doc_class."""

    def test_llm_guess_overrides_segmenter(self):
        """LLM hat 'Notarielle Beurkundung' → Audit-Block zeigt das, nicht Segmenter-Guess."""
        segment_results = [
            {
                "doc_class_fields": {
                    "segment_doc_class": "Arbeitsvertrag",  # Segmenter-Guess (falsch)
                    "llm_doc_class_guess": "Notarielle Beurkundung",  # LLM (korrekt)
                }
            }
        ]
        doc_class = _build_doc_class_from_segment_results(segment_results)
        self.assertEqual(doc_class, "Notarielle Beurkundung")
        self.assertNotIn("Arbeitsvertrag", doc_class)

    def test_fallback_to_segment_doc_class_when_no_llm_guess(self):
        """Kein LLM-Guess → Segmenter-Klasse wird verwendet."""
        segment_results = [
            {
                "doc_class_fields": {
                    "segment_doc_class": "Mietvertrag",
                    # llm_doc_class_guess not set
                }
            }
        ]
        doc_class = _build_doc_class_from_segment_results(segment_results)
        self.assertEqual(doc_class, "Mietvertrag")

    def test_multiple_segments_aggregated(self):
        """Mehrere Segmente: LLM-Guess für jedes Segment wird aggregiert."""
        segment_results = [
            {
                "doc_class_fields": {
                    "segment_doc_class": "Kaufvertrag",  # wrong
                    "llm_doc_class_guess": "Notarielle Beurkundung",
                }
            },
            {
                "doc_class_fields": {
                    "segment_doc_class": "Mietvertrag",  # wrong
                    "llm_doc_class_guess": "Geschäftsanteilsabtretung",
                }
            },
        ]
        doc_class = _build_doc_class_from_segment_results(segment_results)
        self.assertIn("Notarielle Beurkundung", doc_class)
        self.assertIn("Geschäftsanteilsabtretung", doc_class)
        self.assertNotIn("Kaufvertrag", doc_class)
        self.assertNotIn("Mietvertrag", doc_class)

    def test_unklar_when_empty(self):
        """Keine Segmente → 'Unklar'."""
        doc_class = _build_doc_class_from_segment_results([])
        self.assertEqual(doc_class, "Unklar")

    def test_mixed_llm_and_segmenter(self):
        """Segment 1 hat LLM-Guess, Segment 2 nur Segmenter → beide erscheinen."""
        segment_results = [
            {
                "doc_class_fields": {
                    "segment_doc_class": "Kaufvertrag",
                    "llm_doc_class_guess": "Notarielle Beurkundung",
                }
            },
            {
                "doc_class_fields": {
                    "segment_doc_class": "Scheidungsfolgenvereinbarung",
                    # no llm_doc_class_guess
                }
            },
        ]
        doc_class = _build_doc_class_from_segment_results(segment_results)
        self.assertIn("Notarielle Beurkundung", doc_class)
        self.assertIn("Scheidungsfolgenvereinbarung", doc_class)
        self.assertNotIn("Kaufvertrag", doc_class)


if __name__ == "__main__":
    unittest.main()
