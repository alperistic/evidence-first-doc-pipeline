"""services/analysis_service.py – Analyse-Orchestrierung und Audit-Logging.

Enthält keine Streamlit-Abhängigkeiten. Gibt reine Daten zurück.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TypedDict

from core.citation import summarize_citation_verification
from core.config import get_config
from core.doc_cache import build_cache_key, get_cached_result, put_cached_result
from core.logging_config import get_logger
from core.utils import _sum_present_tokens
from governance.audit import log_request
from governance.doc_class_policy import normalize_doc_class
from workflows.doc_summary import DocSummaryWorkflow

_log = get_logger(__name__)


class AnalysisResult(TypedDict):
    result_bundle: dict
    audit_entry: dict
    cache_hit: bool


# ---------------------------------------------------------------------------
# Async Segment-Analyse mit Concurrency-Limit (Semaphore)
# ---------------------------------------------------------------------------


async def _run_segment_async(
    seg: dict,
    *,
    semaphore: asyncio.Semaphore,
    reverse_mapping: list[tuple[str, str]],
    context_mapping: dict[str, str],
    auto_classify: bool,
    citation_verify_enabled: bool,
    citation_verify_mode: str,
    source_pages_by_no: dict[int, dict],
) -> tuple[int, dict]:
    """Führt den offiziellen Workflow-Segmentpfad in einem Thread aus."""
    segment_id = seg.get("segment_id", 0)
    async with semaphore:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: DocSummaryWorkflow.process_segment(
                seg,
                reverse_mapping=reverse_mapping,
                context_mapping=context_mapping,
                auto_classify=auto_classify,
                citation_verify_enabled=citation_verify_enabled,
                citation_verify_mode=citation_verify_mode,
                source_pages_by_no=source_pages_by_no,
            ),
        )
    return segment_id, result


async def _run_segments_async(
    segments: list[dict],
    *,
    max_concurrent: int,
    reverse_mapping: list[tuple[str, str]],
    context_mapping: dict[str, str],
    auto_classify: bool,
    citation_verify_enabled: bool,
    citation_verify_mode: str,
    source_pages_by_no: dict[int, dict],
) -> list[dict]:
    """Async-Orchestrierung aller Segmente mit asyncio.Semaphore."""
    semaphore = asyncio.Semaphore(max_concurrent)
    tasks = [
        _run_segment_async(
            seg,
            semaphore=semaphore,
            reverse_mapping=reverse_mapping,
            context_mapping=context_mapping,
            auto_classify=auto_classify,
            citation_verify_enabled=citation_verify_enabled,
            citation_verify_mode=citation_verify_mode,
            source_pages_by_no=source_pages_by_no,
        )
        for seg in segments
    ]
    completed = await asyncio.gather(*tasks)
    ordered = sorted(completed, key=lambda x: x[0])
    return [item for _, item in ordered]


def run_segment_analysis(
    segments: list[dict],
    *,
    reverse_mapping: list[tuple[str, str]],
    context_mapping: dict[str, str],
    auto_classify: bool,
    citation_verify_enabled: bool,
    citation_verify_mode: str,
    source_pages_by_no: dict[int, dict],
    max_workers: int,
) -> list[dict]:
    """Führt die Segment-Analyse parallel aus und gibt geordnete Ergebnisse zurück.

    Verwendet asyncio mit Semaphore-basiertem Concurrency-Limit
    (AppConfig.max_concurrent_segments). Die synchronen LLM-Calls in
    _process_segment werden via run_in_executor in Threads ausgeführt.

    Raises:
        EnvironmentError: Bei fehlender Azure-Konfiguration.
    """
    cfg = get_config()
    max_concurrent = max(1, int(max_workers or cfg.max_concurrent_segments))

    def _run_segment(seg: dict) -> dict:
        return DocSummaryWorkflow.process_segment(
            seg,
            reverse_mapping=reverse_mapping,
            context_mapping=context_mapping,
            auto_classify=auto_classify,
            citation_verify_enabled=citation_verify_enabled,
            citation_verify_mode=citation_verify_mode,
            source_pages_by_no=source_pages_by_no,
        )

    _log.info(
        "segment_analysis_start",
        stage="analysis_service",
        segment_count=len(segments),
        max_workers=max_workers,
        max_concurrent=max_concurrent,
        citation_verify_mode=citation_verify_mode,
    )
    if len(segments) > 1 and max_concurrent > 1:
        results = asyncio.run(
            _run_segments_async(
                segments,
                max_concurrent=max_concurrent,
                reverse_mapping=reverse_mapping,
                context_mapping=context_mapping,
                auto_classify=auto_classify,
                citation_verify_enabled=citation_verify_enabled,
                citation_verify_mode=citation_verify_mode,
                source_pages_by_no=source_pages_by_no,
            )
        )
    else:
        results = [_run_segment(seg) for seg in segments]
    _log.info(
        "segment_analysis_done",
        stage="analysis_service",
        segment_count=len(results),
    )
    return results


def log_and_aggregate_results(
    segment_results: list[dict],
    *,
    doc_hash: str,
    effective_profile: str,
    policy_hash: str,
    redaction_applied: bool,
    pseudonymization_applied: bool,
    classify_used_chars: int,
    auto_classify: bool,
    citation_verify_mode: str,
    extract_ms: int,
    segment_ms: int,
) -> tuple[dict, dict]:
    """Loggt jedes Segment ins Audit-Log und aggregiert zu result_bundle + audit_entry.

    Returns:
        (result_bundle, audit_entry) – Beide Dicts sind direkt für st.session_state geeignet.
    """
    ts = datetime.now(timezone.utc).isoformat()

    for item in segment_results:
        analysis = item.get("analysis_redacted", {})
        doc_class_fields = item.get("doc_class_fields", {})
        segment_doc_class = normalize_doc_class(
            (doc_class_fields or {}).get("segment_doc_class")
        )
        seg_citation_summary = summarize_citation_verification(
            analysis if isinstance(analysis, dict) else {},
            ("kernaussagen", "auffaelligkeiten", "top3_findings"),
        )
        seg_check_ran = bool(
            analysis.get("citation_check_ran", False)
            if isinstance(analysis, dict)
            else False
        )
        seg_verify_mode = str(
            analysis.get("citation_verify_mode", citation_verify_mode)
            if isinstance(analysis, dict)
            else citation_verify_mode
        )
        seg_status = str(seg_citation_summary["citation_status"])
        seg_total_tokens = (
            int(item.get("token_count", 0))
            if bool(item.get("has_token_usage"))
            else None
        )
        output_json = {
            "analysis_run_id": ts,
            "timings_ms": {
                "extract": extract_ms,
                "segment": segment_ms,
                **(item.get("timings_ms", {}) or {}),
            },
            "citation_metrics": seg_citation_summary["citation_metrics"],
            "citation_check_ran": seg_check_ran,
            "citation_verify_mode": seg_verify_mode,
            "chunks_sent": (
                int(analysis.get("chunks_sent", 0))
                if isinstance(analysis, dict)
                else 0
            ),
            "document_analysis": (
                item.get("analysis_raw_redacted")
                if isinstance(item.get("analysis_raw_redacted"), dict)
                else {}
            ),
            "token_usage": {
                "has_usage": bool(item.get("has_token_usage")),
                "total_tokens": seg_total_tokens,
                "prompt_tokens": item.get("prompt_tokens"),
                "completion_tokens": item.get("completion_tokens"),
            },
            "doc_class_source": str(
                (doc_class_fields or {}).get(
                    "doc_class_source",
                    "llm" if auto_classify else "segmenter",
                )
            ),
            "segment_doc_class": segment_doc_class,
        }
        document_analysis_raw = (
            item.get("analysis_raw_redacted")
            if isinstance(item.get("analysis_raw_redacted"), dict)
            else {}
        )
        document_type_source = str(document_analysis_raw.get("document_type_source", "") or "").strip()
        if document_type_source:
            output_json["document_type_source"] = document_type_source
        llm_doc_class_guess = str((doc_class_fields or {}).get("llm_doc_class_guess", "")).strip()
        if auto_classify and llm_doc_class_guess:
            output_json["llm_doc_class_guess"] = llm_doc_class_guess
        log_request(
            doc_hash=doc_hash,
            doc_class=segment_doc_class,
            token_count=int(item.get("token_count", 0)),
            total_tokens=seg_total_tokens,
            prompt_tokens=item.get("prompt_tokens"),
            completion_tokens=item.get("completion_tokens"),
            model=str(item.get("model", "")),
            policy_profile=effective_profile,
            policy_hash=policy_hash,
            redaction_applied=redaction_applied,
            pseudonymization_applied=pseudonymization_applied,
            classify_used_chars=classify_used_chars,
            output_type=str(item.get("analysis_output_type", "json_ok")),
            citation_status=seg_status,
            output_json=output_json,
        )

    # -- Aggregation --------------------------------------------------------
    result_bundle = {
        "segment_results": segment_results,
        "segment_count": len(segment_results),
        "timings_ms": {
            "extract": extract_ms,
            "segment": segment_ms,
        },
    }
    total_tokens = _sum_present_tokens(
        [
            int(item.get("token_count", 0))
            for item in segment_results
            if bool(item.get("has_token_usage"))
        ]
    )
    audit_entry = {
        "timestamp": ts,
        "doc_hash": doc_hash,
        "model": ", ".join(
            sorted(
                {
                    str(item.get("model", "")).strip()
                    for item in segment_results
                    if str(item.get("model", "")).strip()
                }
            )
        ),
        "token_count": total_tokens if total_tokens is not None else 0,
        "total_tokens": total_tokens,
        "prompt_tokens": _sum_present_tokens(
            [item.get("prompt_tokens") for item in segment_results]
        ),
        "completion_tokens": _sum_present_tokens(
            [item.get("completion_tokens") for item in segment_results]
        ),
        "doc_class": ", ".join(
            sorted(
                {
                    normalize_doc_class(
                        # FIX 5: Prefer LLM's actual analyzed doc type over segmenter's guess.
                        # llm_doc_class_guess is set from analysis_payload.document_type when
                        # auto_classify is True; falls back to segmenter's segment_doc_class.
                        (item.get("doc_class_fields", {}) or {}).get("llm_doc_class_guess")
                        or (item.get("doc_class_fields", {}) or {}).get("segment_doc_class")
                    )
                    for item in segment_results
                }
            )
        )
        or "Unklar",
        "policy_profile": effective_profile,
        "redaction_applied": redaction_applied,
        "pseudonymization_applied": pseudonymization_applied,
    }

    _log.info(
        "audit_aggregation_done",
        stage="analysis_service",
        doc_hash=doc_hash,
        segment_count=len(segment_results),
        total_tokens=total_tokens,
        extract_ms=extract_ms,
        segment_ms=segment_ms,
        redaction_applied=redaction_applied,
    )
    return result_bundle, audit_entry


def run_analysis_with_cache(
    segments: list[dict],
    *,
    doc_hash: str,
    policy_hash: str,
    model: str,
    cache_scope_key: str = "",
    reverse_mapping: list[tuple[str, str]],
    context_mapping: dict[str, str],
    auto_classify: bool,
    citation_verify_enabled: bool,
    citation_verify_mode: str,
    source_pages_by_no: dict[int, dict],
    max_workers: int,
    effective_profile: str,
    redaction_applied: bool,
    pseudonymization_applied: bool,
    classify_used_chars: int,
    extract_ms: int,
    segment_ms: int,
) -> AnalysisResult:
    """Führt die Analyse durch – mit optionalem Cache-Lookup.

    Wenn AppConfig.doc_cache_enabled=True und ein gültiger Cache-Eintrag
    (doc_hash + policy_hash + model + runtime-scope, TTL nicht abgelaufen) existiert,
    wird das gecachte Ergebnis zurückgegeben ohne einen LLM-Call.

    Bei Cache-Miss läuft die normale Analyse und das Ergebnis wird
    anschließend gecacht.

    §203-Konformität: Es wird ausschließlich das anonymisierte result_bundle
    gespeichert. Klartext-Dokumentinhalte sind zu keinem Zeitpunkt Teil
    des gecachten Ergebnisses.

    Returns:
        AnalysisResult mit result_bundle, audit_entry und cache_hit-Flag.
    """
    cfg = get_config()

    if cfg.doc_cache_enabled:
        cache_key = build_cache_key(doc_hash, policy_hash, model, cache_scope_key)
        hit = get_cached_result(cache_key, ttl_seconds=cfg.doc_cache_ttl_seconds)
        if hit is not None:
            _log.info(
                "analysis_cache_hit",
                stage="analysis_service",
                cache_key=cache_key[:16],
                doc_hash=doc_hash[:16],
            )
            return AnalysisResult(
                result_bundle=hit["result_bundle"],
                audit_entry=hit["audit_entry"],
                cache_hit=True,
            )
        _log.info(
            "analysis_cache_miss",
            stage="analysis_service",
            cache_key=cache_key[:16],
            doc_hash=doc_hash[:16],
        )

    segment_results = run_segment_analysis(
        segments,
        reverse_mapping=reverse_mapping,
        context_mapping=context_mapping,
        auto_classify=auto_classify,
        citation_verify_enabled=citation_verify_enabled,
        citation_verify_mode=citation_verify_mode,
        source_pages_by_no=source_pages_by_no,
        max_workers=max_workers,
    )
    result_bundle, audit_entry = log_and_aggregate_results(
        segment_results,
        doc_hash=doc_hash,
        effective_profile=effective_profile,
        policy_hash=policy_hash,
        redaction_applied=redaction_applied,
        pseudonymization_applied=pseudonymization_applied,
        classify_used_chars=classify_used_chars,
        auto_classify=auto_classify,
        citation_verify_mode=citation_verify_mode,
        extract_ms=extract_ms,
        segment_ms=segment_ms,
    )

    if cfg.doc_cache_enabled:
        put_cached_result(cache_key, result_bundle, audit_entry)

    return AnalysisResult(
        result_bundle=result_bundle,
        audit_entry=audit_entry,
        cache_hit=False,
    )
