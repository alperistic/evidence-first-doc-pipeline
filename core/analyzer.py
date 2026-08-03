"""
core/analyzer.py – LLM-Analyse-Funktionen.

Enthält Chunking und alle öffentlichen Analyse-Funktionen
(analyze_document, classify, generate_aktenvermerk,
summarize_*, verify_claim_mappings, …).

Rubrum-Logik lebt in core/rubrum.py.
Citation-Matching lebt in core/citation.py.
Infrastruktur (_invoke_json_task, _get_client, …) lebt in core/llm_client.py.
Datenmodelle leben in core/models.py.
Evidence-Logik lebt in core/evidence.py.
"""

import json
import os
from collections import defaultdict
from hashlib import sha256
from typing import Any

from core.models import (
    LLMResult,
    ValidationError,
    SummaryPayloadModel,
    AktenvermerkPayloadModel,
    ChunkPayloadModel,
    SegmentSummaryPayloadModel,
    DocumentAnalysisPayloadModel,
    VerifyMappingPayloadModel,
)
from core.config import get_config
from core.evidence import (
    _get_page_no,
    _dedupe_keep_order,
    _neutralize_legal_tone,
)
from core.snippet import (
    _normalize_for_match,
)
from core.citation import (
    _normalize_item,
    summarize_citation_verification,
    _build_page_text_index,
    # Re-exported for backward compat via core.analyzer
    _coerce_non_negative_int,  # noqa: F401
    _normalize_token,  # noqa: F401
    _find_token_window_slice,  # noqa: F401
    _find_quote_slice,  # noqa: F401
    _iter_windows,  # noqa: F401
    _partial_ratio,  # noqa: F401
    _match_citation_candidate,  # noqa: F401
    _normalized_citation_dict,  # noqa: F401
    _citation_usage_key,  # noqa: F401
    _heuristic_citation_for_item_text,  # noqa: F401
    verify_citations_for_fields,  # noqa: F401
    maybe_verify_citations_for_fields,  # noqa: F401
)
from core.llm_client import _invoke_json_task, _get_client
from core.rubrum import (  # noqa: F401 – re-exported for backward compat
    _PAGE_TAG_RE,
    _RUBRUM_PLAINTIFF_MARKER_RE,
    _RUBRUM_DEFENDANT_MARKER_RE,
    _RUBRUM_COUNSEL_MARKER_RE,
    _RUBRUM_MARKER_RE,
    _RUBRUM_SECTION_BREAK_RE,
    _RUBRUM_ADDR_OR_META_SPLIT_RE,
    _RUBRUM_PSEUDONYM_RE,
    _RUBRUM_LAWYER_PREFIX_RE,
    _normalize_rubrum_value,
    _looks_like_klageschrift_or_rubrum,
    _rubrum_header_zone,
    _rubrum_page_for_offset,
    _trim_rubrum_block_value,
    _apply_reverse_mapping_to_text,
    _apply_reverse_mapping_to_parties,
    _extract_rubrum_parties_from_text,
    apply_rubrum_parties_override,
    _apply_rubrum_parties_extraction,
)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def build_chunks_from_pages(
    pages: list[dict[str, Any]],
    max_chars_per_chunk: int | None = None,
) -> list[dict[str, Any]]:
    max_chars = max_chars_per_chunk or get_config().chunk_max_chars
    chunks: list[dict[str, Any]] = []
    chunk_pages: list[int] = []
    chunk_parts: list[str] = []
    current_chars = 0

    for page in pages:
        page_no = _get_page_no(page)
        page_text = str(page.get("text", "") or "")
        page_block = f"[Seite {page_no}]\n{page_text}\n"

        if chunk_parts and current_chars + len(page_block) > max_chars:
            chunks.append(
                {
                    "chunk_index": len(chunks) + 1,
                    "pages": list(chunk_pages),
                    "text": "\n".join(chunk_parts).strip(),
                }
            )
            chunk_pages = []
            chunk_parts = []
            current_chars = 0

        chunk_parts.append(page_block)
        chunk_pages.append(page_no)
        current_chars += len(page_block)

    if chunk_parts:
        chunks.append(
            {
                "chunk_index": len(chunks) + 1,
                "pages": list(chunk_pages),
                "text": "\n".join(chunk_parts).strip(),
            }
        )
    return chunks


def _attach_candidates_to_items(items: list[str], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        item_norm = _normalize_for_match(item)
        citations: list[dict[str, Any]] = []
        for c in candidates:
            quote = str(c.get("quote_snippet", "") or "")
            quote_norm = _normalize_for_match(quote)
            if not quote_norm:
                continue
            overlap = set(item_norm.split()) & set(quote_norm.split())
            if len(overlap) >= 2:
                citations.append(
                    {
                        "page_number": c.get("page_number"),
                        "quote_snippet": quote,
                        "section_hint": c.get("section_hint"),
                    }
                )
        if not citations and candidates:
            citations = [
                {
                    "page_number": candidates[0].get("page_number"),
                    "quote_snippet": str(candidates[0].get("quote_snippet", "")),
                    "section_hint": candidates[0].get("section_hint"),
                }
            ]
        result.append({"text": item, "citations": citations})
    return result


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------


def summarize_pages_chunked(
    pages: list[dict[str, Any]],
    summary_prompt: str,
    chunk_prompt: str,
) -> LLMResult:
    chunks = build_chunks_from_pages(pages)
    if not chunks:
        return LLMResult(
            parsed={"parse_error": True, "raw_text": "", "message": "Keine Chunks aus Dokument erzeugt."},
            token_count=0,
            model=get_config().azure_openai_deployment,
            output_type="json_fallback",
            input_chars=0,
            input_truncated=False,
            has_token_usage=False,
        )

    if len(chunks) == 1:
        single = summarize(chunks[0]["text"], summary_prompt)
        if single.output_type == "json_ok":
            single.parsed["chunks_sent"] = 1
        return single

    merged: dict[str, list[str]] = defaultdict(list)
    merged_candidates: list[dict[str, Any]] = []
    total_tokens = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    has_token_usage = False
    model_name = ""
    any_ok = False
    input_chars = 0
    input_truncated = False

    chunk_max_tokens = get_config().chunk_max_output_tokens
    for chunk in chunks:
        chunk_res = _invoke_json_task(
            text=chunk["text"],
            system_prompt=chunk_prompt,
            model_schema=ChunkPayloadModel,
            max_output_tokens=chunk_max_tokens,
        )
        if chunk_res.has_token_usage:
            total_tokens += chunk_res.token_count
            if chunk_res.prompt_tokens is not None:
                total_prompt_tokens += chunk_res.prompt_tokens
            if chunk_res.completion_tokens is not None:
                total_completion_tokens += chunk_res.completion_tokens
            has_token_usage = True
        model_name = chunk_res.model or model_name
        input_chars += chunk_res.input_chars
        input_truncated = input_truncated or chunk_res.input_truncated

        if chunk_res.output_type != "json_ok":
            continue
        any_ok = True
        parsed = chunk_res.parsed
        for key in ("key_points", "issues", "questions", "dates", "missing_info", "todos"):
            merged[key].extend(parsed.get(key, []))
        merged_candidates.extend(parsed.get("citations_candidates", []))

    if not any_ok:
        return LLMResult(
            parsed={"parse_error": True, "raw_text": "", "message": "Chunk-Verarbeitung fehlgeschlagen."},
            token_count=total_tokens,
            model=model_name or get_config().azure_openai_deployment,
            output_type="json_fallback",
            input_chars=input_chars,
            input_truncated=input_truncated,
            prompt_tokens=total_prompt_tokens if has_token_usage else None,
            completion_tokens=total_completion_tokens if has_token_usage else None,
            has_token_usage=has_token_usage,
        )

    key_points = _dedupe_keep_order(merged["key_points"])
    issues = _dedupe_keep_order(merged["issues"])
    questions = _dedupe_keep_order(merged["questions"])
    dates = _dedupe_keep_order(merged["dates"])
    missing_info = _dedupe_keep_order(merged["missing_info"])
    todos = _dedupe_keep_order(merged["todos"])

    parsed_final = {
        "kurzueberblick": key_points[0] if key_points else "Im Dokument steht: mehrere Inhalte, Details siehe Kernaussagen.",
        "top3_findings": [
            {
                "title": issue[:140],
                "why_relevant": "Hinweis aus dem Dokument, zu prüfen.",
                "question": questions[idx] if idx < len(questions) else "Rückfrage: zu prüfen.",
                "confidence": "mittel",
                "citations": _attach_candidates_to_items([issue], merged_candidates)[0]["citations"],
            }
            for idx, issue in enumerate(issues[:3])
        ],
        "top3_questions": questions[:3],
        "dokumenttyp_parteien": key_points[0] if key_points else "fehlende Angabe",
        "kernaussagen": _attach_candidates_to_items(key_points[:40], merged_candidates),
        "fristen_termine": dates[:40],
        "auffaelligkeiten": _attach_candidates_to_items(issues[:30], merged_candidates),
        "prueffragen": _dedupe_keep_order(questions + missing_info)[:30],
        "chunks_sent": len(chunks),
    }

    try:
        model = SummaryPayloadModel.model_validate(parsed_final)
        parsed = _neutralize_legal_tone(model.model_dump())
        parsed["chunks_sent"] = len(chunks)
        return LLMResult(
            parsed=parsed,
            token_count=total_tokens,
            model=model_name or get_config().azure_openai_deployment,
            output_type="json_ok",
            input_chars=input_chars,
            input_truncated=input_truncated,
            prompt_tokens=total_prompt_tokens if has_token_usage else None,
            completion_tokens=total_completion_tokens if has_token_usage else None,
            has_token_usage=has_token_usage,
        )
    except ValidationError as exc:
        return LLMResult(
            parsed={
                "parse_error": True,
                "raw_text": json.dumps(parsed_final, ensure_ascii=False),
                "message": f"Aggregation-Schemafehler: {exc.errors()[0].get('msg', 'ValidationError')}",
            },
            token_count=total_tokens,
            model=model_name or get_config().azure_openai_deployment,
            output_type="json_fallback",
            input_chars=input_chars,
            input_truncated=input_truncated,
            prompt_tokens=total_prompt_tokens if has_token_usage else None,
            completion_tokens=total_completion_tokens if has_token_usage else None,
            has_token_usage=has_token_usage,
        )


def summarize(text: str, system_prompt: str) -> LLMResult:
    max_output_tokens = get_config().max_output_tokens
    return _invoke_json_task(
        text=text,
        system_prompt=system_prompt,
        model_schema=SummaryPayloadModel,
        max_output_tokens=max_output_tokens,
    )


def summarize_segment_structured(text: str, system_prompt: str) -> LLMResult:
    max_output_tokens = get_config().struct_max_output_tokens
    return _invoke_json_task(
        text=text,
        system_prompt=system_prompt,
        model_schema=SegmentSummaryPayloadModel,
        max_output_tokens=max_output_tokens,
    )


def analyze_document(
    text: str,
    system_prompt: str,
    *,
    rubrum_source_text: str | None = None,
    reverse_mapping: list[tuple[str, str]] | None = None,
) -> LLMResult:
    max_output_tokens = get_config().doc_analysis_max_output_tokens
    result = _invoke_json_task(
        text=text,
        system_prompt=system_prompt,
        model_schema=DocumentAnalysisPayloadModel,
        max_output_tokens=max_output_tokens,
    )
    if result.output_type == "json_ok" and isinstance(result.parsed, dict):
        source_text = str(rubrum_source_text or text or "")
        result.parsed = _apply_rubrum_parties_extraction(
            result.parsed,
            source_text,
            reverse_mapping=reverse_mapping,
        )
    return result


def _extract_claims_for_verifier(parsed: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "kernaussagen": [str((item or {}).get("text", "")).strip() for item in parsed.get("kernaussagen", []) if isinstance(item, dict)],
        "auffaelligkeiten": [str((item or {}).get("text", "")).strip() for item in parsed.get("auffaelligkeiten", []) if isinstance(item, dict)],
        "top3_findings": [str((item or {}).get("title", "")).strip() for item in parsed.get("top3_findings", []) if isinstance(item, dict)],
    }


def _normalize_verify_lengths(
    verify_payload: dict[str, Any],
    claims: dict[str, list[str]],
) -> dict[str, Any]:
    normalized = dict(verify_payload or {})
    for field in ("kernaussagen", "auffaelligkeiten", "top3_findings"):
        items = normalized.get(field, [])
        if not isinstance(items, list):
            items = []
        target_len = len(claims.get(field, []))
        while len(items) < target_len:
            items.append({"citations": []})
        if len(items) > target_len:
            items = items[:target_len]
        normalized[field] = items
    return normalized


def verify_claim_mappings(
    parsed: dict[str, Any],
    pages: list[dict[str, Any]],
    system_prompt: str,
) -> LLMResult:
    claims = _extract_claims_for_verifier(parsed)
    page_text_index = _build_page_text_index(pages)
    payload = {
        "claims": claims,
        "segment_pages": sorted({_get_page_no(p) for p in pages}),
        "segment_text": page_text_index,
    }
    result = _invoke_json_task(
        text=json.dumps(payload, ensure_ascii=False),
        system_prompt=system_prompt,
        model_schema=VerifyMappingPayloadModel,
        max_output_tokens=get_config().verify_max_output_tokens,
    )
    if result.output_type != "json_ok":
        return result

    mapped = _normalize_verify_lengths(result.parsed, claims)
    out = dict(parsed)
    for field in ("kernaussagen", "auffaelligkeiten", "top3_findings"):
        items = out.get(field, [])
        verify_items = mapped.get(field, [])
        if not isinstance(items, list):
            continue
        updated_items: list[dict[str, Any]] = []
        for idx, raw in enumerate(items):
            item = _normalize_item(raw)
            verify_entry = verify_items[idx] if idx < len(verify_items) else {"citations": []}
            item["citations"] = verify_entry.get("citations", []) if isinstance(verify_entry, dict) else []
            updated_items.append(item)
        out[field] = updated_items
    result.parsed = out
    return result


def build_llm_cache_key(
    *,
    doc_hash: str,
    segment_id: int,
    prompt_version: str,
    model: str,
    doc_class: str,
    stage: str,
) -> str:
    raw = f"{doc_hash}|seg:{segment_id}|{stage}|{prompt_version}|{model}|{doc_class}"
    return sha256(raw.encode("utf-8")).hexdigest()


def generate_aktenvermerk(text: str, system_prompt: str) -> LLMResult:
    max_output_tokens = get_config().aktenvermerk_max_tokens
    return _invoke_json_task(
        text=text,
        system_prompt=system_prompt,
        model_schema=AktenvermerkPayloadModel,
        max_output_tokens=max_output_tokens,
    )


def _heuristic_classify(text: str) -> str | None:
    t = text.lower()
    if any(k in t for k in ("mietvertrag", "arbeitsvertrag", "kaufvertrag", "gesellschaftsvertrag", "vertrag")):
        return "Vertrag"
    if "klageschrift" in t:
        return "Klageschrift"
    if "abmahnung" in t:
        return "Abmahnung"
    if any(k in t for k in ("mahnung", "zahlungserinnerung")):
        return "Mahnung"
    if any(k in t for k in ("klageerwiderung", "berufungsbegründung", "schriftsatz", "stellungnahme")):
        return "Schriftsatz"
    return None


def classify(text: str, max_chars: int | None = None) -> str:
    """
    Klassifiziert ein Dokument automatisch in eine der definierten Klassen.
    Gibt einen der Strings aus DOC_CLASSES zurück, Fallback: 'Sonstiges'.
    """
    from core.prompts import CLASSIFICATION_PROMPT, DOC_CLASSES

    missing = [
        v for v in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")
        if not os.getenv(v)
    ]
    if missing:
        return "Sonstiges"

    heuristic = _heuristic_classify(text)
    if heuristic:
        return heuristic

    client, deployment = _get_client()
    _cfg = get_config()
    classifier_deployment = _cfg.azure_openai_classifier_deployment or deployment
    preview_chars = (
        int(max_chars)
        if isinstance(max_chars, int) and max_chars > 0
        else _cfg.classify_preview_chars
    )
    preview = text[:preview_chars]

    try:
        response = client.chat.completions.create(
            model=classifier_deployment,
            messages=[
                {"role": "system", "content": CLASSIFICATION_PROMPT},
                {"role": "user", "content": preview},
            ],
            temperature=0.0,
            max_tokens=10,
        )
        result = (response.choices[0].message.content or "").strip()
        if result in DOC_CLASSES:
            return result
        return "Sonstiges"
    except Exception:
        return "Sonstiges"
