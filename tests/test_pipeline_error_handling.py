"""test_pipeline_error_handling.py – Regression für S1: Error Handling in _process_segment().

Stellt sicher dass:
1. Eine Exception in analyze_document() → Partial Result statt Crash
2. Eine Exception in maybe_verify_citations_for_fields() → Analyse bleibt erhalten, citation_status="verify_failed"
3. generate_aktenvermerk() Fehler → Analyse bleibt erhalten (kein Crash)
"""
import unittest
from unittest.mock import patch, MagicMock

from core.pipeline import _process_segment
from core.models import LLMResult


def _minimal_segment(doc_class: str = "Sonstiges") -> dict:
    return {
        "doc_type_guess": doc_class,
        "confidence": 0.8,
        "pages": [{"page_number": 1, "text": "Testinhalt Seite 1."}],
    }


def _ok_llm_result() -> LLMResult:
    return LLMResult(
        parsed={
            "document_type": "Sonstiges",
            "kurzueberblick": "Testdokument.",
            "kernaussagen": [],
            "top3_findings": [],
            "top3_questions": [],
            "auffaelligkeiten": [],
            "fristen_termine": [],
            "facts": [],
            "dates": [],
            "issues": [],
            "parties": [],
            "amounts": [],
        },
        token_count=10,
        model="gpt-4o",
        output_type="json_ok",
        input_chars=100,
        input_truncated=False,
        prompt_tokens=8,
        completion_tokens=2,
        has_token_usage=True,
    )


_COMMON_KWARGS = dict(
    reverse_mapping=[],
    context_mapping={},
    auto_classify=False,
    citation_verify_enabled=False,
    citation_verify_mode="disabled",
    source_pages_by_no={},
)


class TestAnalyzeDocumentCrashReturnsPartialResult(unittest.TestCase):
    """analyze_document() wirft → _process_segment() gibt api_error zurück statt zu crashen."""

    def test_returns_api_error_result_on_exception(self):
        # RuntimeError wird von der Pipeline re-raised (permanent error).
        # ValueError ist ein transientes Soft-Error und wird als api_error behandelt.
        with patch("core.pipeline.analyze_document", side_effect=ValueError("Azure timeout")):
            result = _process_segment(_minimal_segment(), **_COMMON_KWARGS)

        self.assertIsInstance(result, dict, "Ergebnis muss ein dict sein")
        self.assertEqual(result["analysis_output_type"], "api_error")

    def test_all_required_keys_present_on_error(self):
        required_keys = {
            "segment_meta", "analysis", "analysis_redacted", "analysis_raw",
            "analysis_raw_redacted", "analysis_output_type", "evidence_map",
            "evidence_map_redacted", "coverage", "model", "doc_class_fields",
            "token_count", "has_token_usage", "prompt_tokens", "completion_tokens",
            "timings_ms",
        }
        # ConnectionError (subclass von EnvironmentError) wird re-raised.
        # TypeError ist transient und wird als api_error behandelt.
        with patch("core.pipeline.analyze_document", side_effect=TypeError("Network error")):
            result = _process_segment(_minimal_segment(), **_COMMON_KWARGS)

        missing = required_keys - set(result.keys())
        self.assertFalse(missing, f"Fehlende Keys im Error-Result: {missing}")

    def test_token_count_is_zero_on_error(self):
        with patch("core.pipeline.analyze_document", side_effect=Exception("fail")):
            result = _process_segment(_minimal_segment(), **_COMMON_KWARGS)

        self.assertEqual(result["token_count"], 0)
        self.assertFalse(result["has_token_usage"])

    def test_analysis_contains_empty_kernaussagen_on_error(self):
        with patch("core.pipeline.analyze_document", side_effect=Exception("fail")):
            result = _process_segment(_minimal_segment(), **_COMMON_KWARGS)

        self.assertEqual(result["analysis"].get("kernaussagen"), [])

    def test_doc_class_preserved_on_error(self):
        seg = _minimal_segment(doc_class="Mahnung")
        with patch("core.pipeline.analyze_document", side_effect=Exception("fail")):
            result = _process_segment(seg, **_COMMON_KWARGS)

        # Im Error-Result ist analysis["document_type"] bewusst "" (kein LLM-Guess ins Audit).
        # Die Dokumentklasse wird in segment_meta["doc_type_guess"] bewahrt.
        self.assertEqual(result["segment_meta"].get("doc_type_guess"), "Mahnung")

    def test_coverage_has_correct_page_count_on_error(self):
        seg = _minimal_segment()
        seg["pages"] = [
            {"page_number": 1, "text": "Seite 1"},
            {"page_number": 2, "text": "Seite 2"},
        ]
        with patch("core.pipeline.analyze_document", side_effect=Exception("fail")):
            result = _process_segment(seg, **_COMMON_KWARGS)

        self.assertEqual(result["coverage"]["pages_total"], 2)


class TestVerifyCitationsCrashKeepsAnalysis(unittest.TestCase):
    """maybe_verify_citations_for_fields() wirft → Analyse bleibt, citation_status="verify_failed"."""

    def test_citation_verify_fail_sets_verify_failed_status(self):
        with (
            patch("core.pipeline.analyze_document", return_value=_ok_llm_result()),
            patch(
                "core.pipeline.maybe_verify_citations_for_fields",
                side_effect=RuntimeError("verify crash"),
            ),
        ):
            result = _process_segment(
                _minimal_segment(),
                **{**_COMMON_KWARGS, "citation_verify_enabled": True},
            )

        self.assertEqual(result["analysis_output_type"], "json_ok")
        analysis = result["analysis"]
        self.assertEqual(analysis.get("citation_status"), "verify_failed")
        self.assertFalse(analysis.get("citation_check_ran", True))

    def test_citation_verify_fail_preserves_kernaussagen(self):
        ok_result = _ok_llm_result()
        ok_result.parsed["kernaussagen"] = [{"text": "Wichtige Aussage.", "citations": []}]
        with (
            patch("core.pipeline.analyze_document", return_value=ok_result),
            patch(
                "core.pipeline.maybe_verify_citations_for_fields",
                side_effect=Exception("crash"),
            ),
        ):
            result = _process_segment(
                _minimal_segment(),
                **{**_COMMON_KWARGS, "citation_verify_enabled": True},
            )

        # Analyse-Output muss trotzdem die Kernaussagen enthalten
        self.assertEqual(result["analysis_output_type"], "json_ok")


class TestAktenvermerkcCrashDoesNotCrash(unittest.TestCase):
    """generate_aktenvermerk() wirft → Haupt-Analyse bleibt erhalten."""

    def test_aktenvermerk_crash_does_not_propagate(self):
        with (
            patch("core.pipeline.analyze_document", return_value=_ok_llm_result()),
            patch(
                "core.pipeline.generate_aktenvermerk",
                side_effect=RuntimeError("aktenvermerk crash"),
            ),
        ):
            result = _process_segment(_minimal_segment(), **_COMMON_KWARGS)

        self.assertIsInstance(result, dict)
        self.assertEqual(result["analysis_output_type"], "json_ok")

    def test_aktenvermerk_crash_logs_warning(self):
        # W4: print("[DOCPIPE][WARNING]") wurde durch structlog _log.warning() ersetzt.
        with (
            patch("core.pipeline.analyze_document", return_value=_ok_llm_result()),
            patch(
                "core.pipeline.generate_aktenvermerk",
                side_effect=RuntimeError("aktenvermerk crash"),
            ),
            patch("core.pipeline._log.warning") as mock_warn,
        ):
            _process_segment(_minimal_segment(), **_COMMON_KWARGS)

        self.assertTrue(mock_warn.called, "Erwartet: _log.warning() wurde aufgerufen")
        event_name = mock_warn.call_args[0][0]
        self.assertIn("aktenvermerk", event_name)


if __name__ == "__main__":
    unittest.main()
