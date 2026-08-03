from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from core.config import reset_config
from governance.policy import LoadedPolicy, PolicyConfig
from workflows.doc_summary import DocSummaryWorkflow


@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config()
    yield
    reset_config()


def _loaded_policy(**overrides) -> LoadedPolicy:
    defaults = PolicyConfig(
        profile="balanced",
        dev_mode=False,
        allow_ui_override=False,
        classify_max_chars=2000,
        segment_max_pages=12,
        segment_chunk_max_chars=12000,
        segment_min_pages=2,
        segment_max_segments=25,
        segment_boundary_threshold=2.4,
        segment_boundary_cooldown_pages=1,
        segment_adaptive_threshold=True,
        redact_required=True,
        fail_closed=True,
        redact_company_names=False,
        retention_days=None,
        retain_documents=None,
    )
    config = PolicyConfig(**(defaults.__dict__ | overrides))
    return LoadedPolicy(
        config=config,
        policy_hash="policy-hash-123",
        source_path=Path("policy.yaml"),
    )


def test_runtime_config_fingerprint_tracks_runtime_toggles(monkeypatch):
    monkeypatch.setenv("DOCPIPE_CITATION_VERIFY", "1")
    monkeypatch.setenv("DOCPIPE_MAX_CONCURRENT_SEGMENTS", "4")
    reset_config()

    loaded_policy = _loaded_policy()
    base_runtime = DocSummaryWorkflow.build_runtime_config(
        loaded_policy=loaded_policy,
        auto_classify=False,
        dictionary_entities=[],
        dictionary_fuzzy=False,
        dictionary_threshold=92,
    )
    changed_runtime = DocSummaryWorkflow.build_runtime_config(
        loaded_policy=loaded_policy,
        auto_classify=True,
        dictionary_entities=["Max Mustermann"],
        dictionary_fuzzy=True,
        dictionary_threshold=90,
    )

    assert base_runtime.cache_scope_key != changed_runtime.cache_scope_key
    assert base_runtime.citation_verify_mode == "enabled"
    assert changed_runtime.max_concurrent_segments == 4
    assert changed_runtime.must_use_redacted is True


def test_build_segments_passes_effective_policy_settings(monkeypatch):
    monkeypatch.setenv("DOCPIPE_MAX_CONCURRENT_SEGMENTS", "2")
    reset_config()

    runtime_config = DocSummaryWorkflow.build_runtime_config(
        loaded_policy=_loaded_policy(
            segment_max_pages=9,
            segment_max_segments=7,
            segment_boundary_threshold=3.1,
            segment_boundary_cooldown_pages=2,
            segment_adaptive_threshold=False,
        ),
        auto_classify=False,
        dictionary_entities=[],
        dictionary_fuzzy=False,
        dictionary_threshold=92,
    )
    pages = [{"page_number": 1, "text": "Seite 1"}]

    with patch("workflows.doc_summary._segment_pages", return_value=[{"segment_id": 1}]) as mock_segment_pages:
        segments = DocSummaryWorkflow.build_segments(pages, runtime_config=runtime_config)

    assert segments == [{"segment_id": 1}]
    mock_segment_pages.assert_called_once_with(
        pages,
        max_pages_per_segment=9,
        max_segments_cap=7,
        boundary_threshold=3.1,
        adaptive_threshold=False,
        boundary_cooldown_pages=2,
    )
