"""
Workflow 1: Document summary.

Adapter over core/pipeline._process_segment. Contains no analysis logic of its
own – all processing happens in core/. This workflow is the entry point
used by app.py and future workflow-2/3 counterparts.

The governance layer (governance/redaction.py) is run by app.py before this workflow.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from core.config import get_config
from core.pipeline import _dictionary_state_key, _process_segment
from core.segmenter import segment_by_document as _segment_by_document
from core.segmenter import segment_pages as _segment_pages
from core.utils import _build_marker_segments, _stitch_pages_with_tags
from governance.policy import LoadedPolicy
from workflows.base import BaseWorkflow


@dataclass(frozen=True)
class DocSummaryRuntimeConfig:
    policy_hash: str
    policy_source_path: Path
    effective_profile: str
    redact_required: bool
    fail_closed: bool
    allow_plaintext_preview: bool
    citation_verify_enabled: bool
    citation_verify_mode: str
    auto_classify: bool
    classify_max_chars_legacy: int
    dictionary_entities: tuple[str, ...]
    dictionary_fuzzy: bool
    dictionary_threshold: int
    segment_max_pages_legacy: int
    segment_max_segments: int
    segment_boundary_threshold: float
    segment_boundary_cooldown_pages: int
    segment_adaptive_threshold: bool
    max_concurrent_segments: int
    analysis_model: str
    cache_scope_key: str

    @property
    def must_use_redacted(self) -> bool:
        return self.effective_profile in {"strict", "balanced"}

    @property
    def use_redaction(self) -> bool:
        return self.must_use_redacted


class DocSummaryWorkflow(BaseWorkflow):
    """
    Workflow 1: Structured document summary.

    Processes the current main flow through a stable entry point:
    resolve runtime config, segment, analyze.
    """

    @property
    def doc_class(self) -> str:
        return "alle"  # Workflow 1 is doc-class-agnostic

    def process(self, pages: list[Any], policy: Any, redaction_context: Any) -> dict:
        """
        Not intended for direct use from app.py.
        app.py uses build_segments() + run_analysis() for the official flow.

        This method satisfies the BaseWorkflow interface and enables
        future unit tests without a Streamlit context.
        """
        raise NotImplementedError(
            "DocSummaryWorkflow.process() is not intended for direct use. "
            "Use build_segments() and run_analysis() for the main path."
        )

    @staticmethod
    def resolve_effective_profile(
        loaded_policy: LoadedPolicy,
        profile_override: str | None = None,
    ) -> str:
        policy = loaded_policy.config
        base_profile = str(policy.profile or "balanced").strip().lower()
        if base_profile == "fast" and not policy.dev_mode:
            base_profile = "balanced"

        can_override_profile = bool(policy.dev_mode and policy.allow_ui_override)
        if can_override_profile and profile_override in {"balanced", "strict"}:
            return profile_override
        return base_profile

    @staticmethod
    def build_runtime_config(
        *,
        loaded_policy: LoadedPolicy,
        profile_override: str | None = None,
        auto_classify: bool,
        dictionary_entities: list[str],
        dictionary_fuzzy: bool,
        dictionary_threshold: int,
    ) -> DocSummaryRuntimeConfig:
        policy = loaded_policy.config
        cfg = get_config()
        effective_profile = DocSummaryWorkflow.resolve_effective_profile(
            loaded_policy,
            profile_override=profile_override,
        )
        normalized_dictionary_entities = tuple(
            str(entity).strip()
            for entity in dictionary_entities
            if str(entity).strip()
        )
        runtime_scope_payload = {
            "policy_hash": loaded_policy.policy_hash,
            "effective_profile": effective_profile,
            "auto_classify": bool(auto_classify),
            "citation_verify_mode": "enabled" if cfg.citation_verify else "disabled",
            "dictionary": _dictionary_state_key(
                list(normalized_dictionary_entities),
                dictionary_fuzzy,
                dictionary_threshold,
            ),
            "segment_boundary_threshold": float(policy.segment_boundary_threshold),
            "segment_boundary_cooldown_pages": int(policy.segment_boundary_cooldown_pages),
            "segment_adaptive_threshold": bool(policy.segment_adaptive_threshold),
            "segment_max_segments": int(policy.segment_max_segments),
            "redact_required": bool(policy.redact_required),
            "fail_closed": bool(policy.fail_closed),
        }
        cache_scope_key = sha256(
            json.dumps(
                runtime_scope_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return DocSummaryRuntimeConfig(
            policy_hash=loaded_policy.policy_hash,
            policy_source_path=loaded_policy.source_path,
            effective_profile=effective_profile,
            redact_required=bool(policy.redact_required),
            fail_closed=bool(policy.fail_closed),
            allow_plaintext_preview=bool(cfg.allow_plaintext_preview),
            citation_verify_enabled=bool(cfg.citation_verify),
            citation_verify_mode="enabled" if cfg.citation_verify else "disabled",
            auto_classify=bool(auto_classify),
            classify_max_chars_legacy=int(policy.classify_max_chars),
            dictionary_entities=normalized_dictionary_entities,
            dictionary_fuzzy=bool(dictionary_fuzzy),
            dictionary_threshold=int(dictionary_threshold),
            segment_max_pages_legacy=int(policy.segment_max_pages),
            segment_max_segments=int(policy.segment_max_segments),
            segment_boundary_threshold=float(policy.segment_boundary_threshold),
            segment_boundary_cooldown_pages=int(policy.segment_boundary_cooldown_pages),
            segment_adaptive_threshold=bool(policy.segment_adaptive_threshold),
            max_concurrent_segments=int(cfg.max_concurrent_segments),
            analysis_model=str(cfg.azure_openai_deployment),
            cache_scope_key=cache_scope_key,
        )

    @staticmethod
    def build_segments(
        pages: list[dict[str, Any]],
        *,
        runtime_config: DocSummaryRuntimeConfig,
    ) -> list[dict]:
        segments = _segment_pages(
            pages,
            max_pages_per_segment=runtime_config.segment_max_pages_legacy,
            max_segments_cap=runtime_config.segment_max_segments,
            boundary_threshold=runtime_config.segment_boundary_threshold,
            adaptive_threshold=runtime_config.segment_adaptive_threshold,
            boundary_cooldown_pages=runtime_config.segment_boundary_cooldown_pages,
        )
        if segments:
            return segments

        full_text = _stitch_pages_with_tags(pages)
        marker_blocks = _segment_by_document(full_text)
        return _build_marker_segments(marker_blocks, pages)

    @staticmethod
    def run_analysis(
        segments: list[dict],
        *,
        doc_hash: str,
        runtime_config: DocSummaryRuntimeConfig,
        reverse_mapping: list[tuple[str, str]],
        context_mapping: dict[str, str],
        source_pages_by_no: dict[int, dict],
        redaction_applied: bool,
        pseudonymization_applied: bool,
        classify_used_chars: int,
        extract_ms: int,
        segment_ms: int,
    ) -> dict:
        from services.analysis_service import run_analysis_with_cache

        return run_analysis_with_cache(
            segments,
            doc_hash=doc_hash,
            policy_hash=runtime_config.policy_hash,
            model=runtime_config.analysis_model,
            cache_scope_key=runtime_config.cache_scope_key,
            reverse_mapping=reverse_mapping,
            context_mapping=context_mapping,
            auto_classify=runtime_config.auto_classify,
            citation_verify_enabled=runtime_config.citation_verify_enabled,
            citation_verify_mode=runtime_config.citation_verify_mode,
            source_pages_by_no=source_pages_by_no,
            max_workers=runtime_config.max_concurrent_segments,
            effective_profile=runtime_config.effective_profile,
            redaction_applied=redaction_applied,
            pseudonymization_applied=pseudonymization_applied,
            classify_used_chars=classify_used_chars,
            extract_ms=extract_ms,
            segment_ms=segment_ms,
        )

    @staticmethod
    def process_segment(
        seg: dict,
        *,
        reverse_mapping: list[tuple[str, str]],
        context_mapping: dict[str, str],
        auto_classify: bool,
        citation_verify_enabled: bool,
        citation_verify_mode: str,
        source_pages_by_no: dict[int, dict],
    ) -> dict:
        """
        Processes a single segment and returns the analysis dict.

        Delegates entirely to core/pipeline._process_segment.
        Services use this method instead of calling _process_segment directly.

        Args:
            seg: Segment dict from segmenter.py (pages, doc_type_guess, etc.)
            reverse_mapping: Pseudonym -> plaintext mapping from redaction.py
            context_mapping: Context mapping for evidence enrichment
            auto_classify: Whether the LLM is allowed to determine the document class itself
            citation_verify_enabled: Whether citation verification is active
            citation_verify_mode: Citation verification mode
            source_pages_by_no: Original (unredacted) pages keyed by page number

        Returns:
            Analysis dict with analysis_redacted, doc_class_fields, token_count, etc.
        """
        return _process_segment(
            seg,
            reverse_mapping=reverse_mapping,
            context_mapping=context_mapping,
            auto_classify=auto_classify,
            citation_verify_enabled=citation_verify_enabled,
            citation_verify_mode=citation_verify_mode,
            source_pages_by_no=source_pages_by_no,
        )
