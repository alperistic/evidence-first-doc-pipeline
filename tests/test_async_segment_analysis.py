"""Tests für die asyncio-basierte Segment-Analyse in analysis_service.py."""

from __future__ import annotations

import threading
from unittest.mock import patch, MagicMock

import pytest

from core.config import reset_config


@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config()
    yield
    reset_config()


def _make_segment(segment_id: int) -> dict:
    return {
        "segment_id": segment_id,
        "pages": [{"text": f"Seite {segment_id}", "page_number": segment_id}],
        "doc_class": "Vertrag",
    }


def _fake_process_segment(seg, **kwargs):
    """Simulates _process_segment with minimal return dict."""
    return {
        "segment_id": seg.get("segment_id", 0),
        "analysis_redacted": {},
        "thread_id": threading.current_thread().ident,
    }


_COMMON_KWARGS = dict(
    reverse_mapping=[],
    context_mapping={},
    auto_classify=False,
    citation_verify_enabled=False,
    citation_verify_mode="off",
    source_pages_by_no={},
    max_workers=2,
)


class TestRunSegmentAnalysis:
    """Prüft run_segment_analysis mit asyncio-Backend."""

    @patch("services.analysis_service.DocSummaryWorkflow.process_segment", side_effect=_fake_process_segment)
    def test_single_segment_runs_sequentially(self, mock_ps, monkeypatch):
        monkeypatch.setenv("LATE_MAX_CONCURRENT_SEGMENTS", "3")
        reset_config()
        from services.analysis_service import run_segment_analysis

        segments = [_make_segment(0)]
        results = run_segment_analysis(segments, **_COMMON_KWARGS)
        assert len(results) == 1
        assert results[0]["segment_id"] == 0
        assert mock_ps.call_count == 1

    @patch("services.analysis_service.DocSummaryWorkflow.process_segment", side_effect=_fake_process_segment)
    def test_multiple_segments_use_async_path(self, mock_ps, monkeypatch):
        monkeypatch.setenv("LATE_MAX_CONCURRENT_SEGMENTS", "3")
        reset_config()
        from services.analysis_service import run_segment_analysis

        segments = [_make_segment(i) for i in range(4)]
        results = run_segment_analysis(segments, **_COMMON_KWARGS)
        assert len(results) == 4
        # Ergebnisse nach segment_id sortiert
        assert [r["segment_id"] for r in results] == [0, 1, 2, 3]
        assert mock_ps.call_count == 4

    @patch("services.analysis_service.DocSummaryWorkflow.process_segment", side_effect=_fake_process_segment)
    def test_concurrency_limit_1_runs_sequentially(self, mock_ps, monkeypatch):
        """Bei max_concurrent_segments=1 wird der sequentielle Pfad genommen."""
        monkeypatch.setenv("LATE_MAX_CONCURRENT_SEGMENTS", "1")
        reset_config()
        from services.analysis_service import run_segment_analysis

        segments = [_make_segment(i) for i in range(3)]
        results = run_segment_analysis(segments, **_COMMON_KWARGS)
        assert len(results) == 3
        assert mock_ps.call_count == 3

    @patch("services.analysis_service.DocSummaryWorkflow.process_segment", side_effect=_fake_process_segment)
    def test_segments_ordered_by_id(self, mock_ps, monkeypatch):
        """Auch bei umgekehrter Reihenfolge kommen Ergebnisse sortiert."""
        monkeypatch.setenv("LATE_MAX_CONCURRENT_SEGMENTS", "2")
        reset_config()
        from services.analysis_service import run_segment_analysis

        segments = [_make_segment(i) for i in reversed(range(5))]
        results = run_segment_analysis(segments, **_COMMON_KWARGS)
        assert [r["segment_id"] for r in results] == [0, 1, 2, 3, 4]

    @patch("services.analysis_service.DocSummaryWorkflow.process_segment")
    def test_exception_propagates(self, mock_ps, monkeypatch):
        """EnvironmentError aus _process_segment wird nicht geschluckt."""
        monkeypatch.setenv("LATE_MAX_CONCURRENT_SEGMENTS", "2")
        reset_config()
        mock_ps.side_effect = EnvironmentError("Azure config missing")
        from services.analysis_service import run_segment_analysis

        segments = [_make_segment(i) for i in range(2)]
        with pytest.raises(EnvironmentError, match="Azure config missing"):
            run_segment_analysis(segments, **_COMMON_KWARGS)

    @patch("services.analysis_service.DocSummaryWorkflow.process_segment", side_effect=_fake_process_segment)
    def test_semaphore_limits_concurrency(self, mock_ps, monkeypatch):
        """Prüft dass maximal N Segmente gleichzeitig laufen."""
        monkeypatch.setenv("LATE_MAX_CONCURRENT_SEGMENTS", "2")
        reset_config()

        import asyncio
        active = {"current": 0, "peak": 0}
        lock = threading.Lock()
        original_fake = _fake_process_segment

        def _tracking_process(seg, **kwargs):
            with lock:
                active["current"] += 1
                active["peak"] = max(active["peak"], active["current"])
            import time
            time.sleep(0.02)  # kurzer Sleep um Overlap zu erzwingen
            result = original_fake(seg, **kwargs)
            with lock:
                active["current"] -= 1
            return result

        mock_ps.side_effect = _tracking_process
        from services.analysis_service import run_segment_analysis

        segments = [_make_segment(i) for i in range(6)]
        results = run_segment_analysis(segments, **_COMMON_KWARGS)
        assert len(results) == 6
        # Peak-Concurrency darf max_concurrent_segments (2) nicht überschreiten
        assert active["peak"] <= 2, f"Peak concurrency {active['peak']} > 2"

    @patch("services.analysis_service.DocSummaryWorkflow.process_segment", side_effect=_fake_process_segment)
    def test_max_workers_argument_is_effective(self, mock_ps, monkeypatch):
        """Der Call-Parameter darf die Env-basierte Default-Concurrency überschreiben."""
        monkeypatch.setenv("LATE_MAX_CONCURRENT_SEGMENTS", "5")
        reset_config()

        import time
        active = {"current": 0, "peak": 0}
        lock = threading.Lock()

        def _tracking_process(seg, **kwargs):
            with lock:
                active["current"] += 1
                active["peak"] = max(active["peak"], active["current"])
            time.sleep(0.02)
            result = _fake_process_segment(seg, **kwargs)
            with lock:
                active["current"] -= 1
            return result

        mock_ps.side_effect = _tracking_process
        from services.analysis_service import run_segment_analysis

        segments = [_make_segment(i) for i in range(6)]
        results = run_segment_analysis(segments, **(_COMMON_KWARGS | {"max_workers": 1}))
        assert len(results) == 6
        assert active["peak"] == 1
