"""
segmenter.py - Robuste Multi-Dokument-Segmentierung auf Seitenebene.
"""

from __future__ import annotations

import re
from collections import Counter
from math import ceil
from typing import Any

from core.config import get_config
from core.doctype_classifier import (  # noqa: F401 – re-exported for public API
    DOCTYPE_KEYWORDS,
    DOCTYPE_PRIORITY,
    DocTypeResult,
    _DOC_TYPE_KEYWORDS,
    _DOCTYPE_KEY_TO_LABEL,
    _URTEIL_HARD_MARKERS,
    _URTEIL_MIN_HARD_MARKERS,
    _URTEIL_REQUIRED_MARKER,
    _count_urteil_hard_markers,
    _guess_doc_type_from_text,
    classify_doc_type_with_result,
    detect_doctype_by_keywords,
)
from core.layout_analyzer import (  # noqa: F401 – re-exported for public API
    _ATTACHMENT_PAGE_RE,
    _BOUNDARY_DOC_TITLE_KEYWORDS,
    _BOUNDARY_STRONG_MARKERS,
    _BOUNDARY_TOP_LINES,
    _CONTRACT_CONTINUATION_RE,
    _GENERIC_STRONG_MARKERS,
    _HARD_COOLDOWN_OVERRIDE_MARKERS,
    _INLINE_KEY_VALUE_RE,
    _KEY_VALUE_LINE_RE,
    _META_FIELD_HINT_RE,
    _META_TOP_LINES,
    _SHORT_TITLE_LINE_RE,
    _START_MARKERS,
    _STRONG_PRIORITY_MARKERS,
    _VERTRAG_DOC_TYPES,
    _alpha_upper_ratio,
    _header_footer_context,
    _header_footer_shift_score,
    _heading_score,
    _key_value_lines,
    _keyword_hits,
    _keyword_score,
    _layout_shift_score,
    _layout_start_score,
    _line_fingerprint,
    _looks_like_heading,
    _marker_score,
    _meta_block_indicator,
    _non_empty_lines,
    _normalize_ws,
    _page_metrics,
    _strong_boundary_hits,
    _title_guess,
    _title_keyword_hit,
    _top_lines,
    is_attachment_page,
)
from core.logging_config import get_logger

_log = get_logger(__name__)

_GENERIC_FALLBACK_TITLE = "Dokumentsegment"
_GENERATED_TITLE_RE = re.compile(rf"^{_GENERIC_FALLBACK_TITLE}\s+\d+$", re.IGNORECASE)
_STRONG_MARKER_KEYS = (
    "DOKUMENT START",
    "Dokument X/Y",
    "Dokumenten-ID",
    "Doc-ID",
    "Aktenzeichen",
    "Betreff",
    "Absender",
    "Empfänger",
    "Vertragsparteien",
    "Vereinbarung",
    "Vergleich",
    "Anlage",
)
_DOCUMENT_MARKER_RE = re.compile(r"^\s*(?:=+\s*)?dokument\s+start\b", re.IGNORECASE | re.MULTILINE)
_DOCUMENT_SPLIT_RE = re.compile(r"(?im)(?=^\s*(?:=+\s*)?dokument\s+start\b)")
_BUNDLE_DOCUMENT_SPLIT_RE = re.compile(r"(?i)\bDokument\s+\d+\s*/\s*\d+\b")
_DOCUMENT_START_INLINE_RE = re.compile(r"(?i)\bDOKUMENT\s+START\b")
_DOCUMENT_ID_INLINE_RE = re.compile(r"(?i)\bDokument\s*[-_ ]?ID\s*:\s*[^\s|]+")
_PAGE_TAG_RE = re.compile(r"\[\[PAGE:(\d+)\]\]")
_SEITENFUSS_LINE_RE = re.compile(r"^\s*seitenfu(?:ß|ss)-?marker\s*:", re.IGNORECASE)
_PROTECTED_SEGMENT_LINE_RE = re.compile(
    r"(dokument\s*[-_ ]?id|(?:^|\|)\s*typ\s*:|(?:^|\|)\s*az\b|aktenzeichen|dokument\s*start|seite\s*\d+\s*/\s*\d+)",
    re.IGNORECASE,
)


def _segment_debug_enabled() -> bool:
    import os
    return str(os.getenv("LATE_SEGMENT_DEBUG", "0")).strip() == "1"


def _segment_debug(message: str) -> None:
    if _segment_debug_enabled():
        _log.debug("segment_debug", stage="segmenter", message=message)


def _safe_page_no(page: dict[str, Any], fallback: int) -> int:
    try:
        return int(page.get("page_number") or page.get("page") or fallback)
    except (TypeError, ValueError):
        return fallback


def _to_normalized_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, page in enumerate(pages, start=1):
        copy_page = dict(page)
        # Use deterministic sequential page numbers for segmentation internals.
        # This avoids duplicate/invalid upstream page numbers collapsing coverage checks.
        source_page_no = copy_page.get("page_number", copy_page.get("page"))
        copy_page["source_page_number"] = source_page_no
        page_no = idx
        copy_page["page_number"] = page_no
        copy_page["page"] = page_no
        copy_page["text"] = str(copy_page.get("text", "") or "")
        normalized.append(copy_page)
    return normalized


def _coerce_positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value).strip())
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def _coerce_non_negative_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value).strip())
        return parsed if parsed >= 0 else default
    except (TypeError, ValueError):
        return default


def _coerce_positive_float(value: str | None, default: float) -> float:
    try:
        parsed = float(str(value).strip())
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_newlines(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _boilerplate_fingerprint(line: str) -> str:
    normalized = _normalize_ws(line).lower()
    normalized = re.sub(r"\d+", "#", normalized)
    normalized = re.sub(r"[^a-z0-9äöüß#:/|.%\- ]", "", normalized)
    return normalized[:120]


def _is_boilerplate_candidate_line(line: str) -> bool:
    cleaned = _normalize_ws(line)
    if not cleaned:
        return False
    if _PAGE_TAG_RE.search(cleaned):
        return False
    if _PROTECTED_SEGMENT_LINE_RE.search(cleaned):
        return False
    if len(cleaned) < 4 or len(cleaned) > 140:
        return False
    if len(cleaned.split()) > 14:
        return False
    return sum(ch.isalpha() for ch in cleaned) >= 3


def _split_tagged_pages(text: str) -> list[tuple[int, list[str]]]:
    normalized = _normalize_newlines(text)
    matches = list(_PAGE_TAG_RE.finditer(normalized))
    if not matches:
        return []

    pages: list[tuple[int, list[str]]] = []
    for idx, match in enumerate(matches):
        try:
            page_no = int(match.group(1))
        except (TypeError, ValueError):
            continue
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(normalized)
        page_text = normalized[start:end].strip("\n")
        pages.append((page_no, page_text.splitlines()))
    return pages


def _join_tagged_pages(pages: list[tuple[int, list[str]]]) -> str:
    blocks: list[str] = []
    for page_no, lines in pages:
        body = "\n".join(line.rstrip() for line in lines).strip()
        if body:
            blocks.append(f"[[PAGE:{page_no}]]\n{body}")
        else:
            blocks.append(f"[[PAGE:{page_no}]]")
    return "\n\n".join(blocks).strip()


def _detect_repeated_boilerplate_fingerprints(pages: list[tuple[int, list[str]]]) -> set[str]:
    if len(pages) < 2:
        return set()

    page_presence: Counter[str] = Counter()
    for _, lines in pages:
        trimmed = [line.strip() for line in lines if line.strip()]
        if not trimmed:
            continue
        candidates = trimmed[:3] + trimmed[-2:]
        local: set[str] = set()
        for line in candidates:
            if _SEITENFUSS_LINE_RE.match(line):
                continue
            if not _is_boilerplate_candidate_line(line):
                continue
            fp = _boilerplate_fingerprint(line)
            if fp:
                local.add(fp)
        for fp in local:
            page_presence[fp] += 1

    min_pages = 2 if len(pages) <= 4 else 3
    return {
        fp
        for fp, count in page_presence.items()
        if count >= min_pages and (count / max(len(pages), 1)) >= 0.5
    }


def _clean_tagged_segment_text(text: str) -> str:
    pages = _split_tagged_pages(text)
    if not pages:
        return text.strip()

    removable = _detect_repeated_boilerplate_fingerprints(pages)
    cleaned_pages: list[tuple[int, list[str]]] = []
    for page_no, lines in pages:
        cleaned_lines: list[str] = []
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                if cleaned_lines and cleaned_lines[-1]:
                    cleaned_lines.append("")
                continue
            if _SEITENFUSS_LINE_RE.match(line):
                continue
            fp = _boilerplate_fingerprint(line)
            if fp and fp in removable and _is_boilerplate_candidate_line(line):
                continue
            cleaned_lines.append(line)

        while cleaned_lines and not cleaned_lines[0]:
            del cleaned_lines[0]
        while cleaned_lines and not cleaned_lines[-1]:
            cleaned_lines.pop()
        cleaned_pages.append((page_no, cleaned_lines))

    return _join_tagged_pages(cleaned_pages)


def _clean_plain_segment_text(text: str) -> str:
    lines = _normalize_newlines(text).splitlines()
    counts: Counter[str] = Counter()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _SEITENFUSS_LINE_RE.match(stripped):
            continue
        if _is_boilerplate_candidate_line(stripped):
            fp = _boilerplate_fingerprint(stripped)
            if fp:
                counts[fp] += 1

    removable = {fp for fp, count in counts.items() if count >= 3}
    cleaned_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if cleaned_lines and cleaned_lines[-1]:
                cleaned_lines.append("")
            continue
        if _SEITENFUSS_LINE_RE.match(stripped):
            continue
        fp = _boilerplate_fingerprint(stripped)
        if fp and fp in removable and _is_boilerplate_candidate_line(stripped):
            continue
        cleaned_lines.append(stripped)

    while cleaned_lines and not cleaned_lines[0]:
        del cleaned_lines[0]
    while cleaned_lines and not cleaned_lines[-1]:
        cleaned_lines.pop()
    return "\n".join(cleaned_lines).strip()


def _split_document_blocks(full_text: str) -> list[str]:
    normalized = _normalize_newlines(full_text).strip()
    if not normalized:
        return []

    bundle_matches = list(_BUNDLE_DOCUMENT_SPLIT_RE.finditer(normalized))
    if bundle_matches:
        marker_matches: list[re.Match[str]] = []
        last_marker_key = ""
        for match in bundle_matches:
            marker_key = re.sub(r"\s+", "", match.group(0)).casefold()
            if marker_key == last_marker_key:
                continue
            marker_matches.append(match)
            last_marker_key = marker_key
    else:
        start_matches = list(_DOCUMENT_START_INLINE_RE.finditer(normalized))
        if start_matches:
            marker_matches = start_matches
        else:
            marker_matches = []
            last_marker_key = ""
            for match in _DOCUMENT_ID_INLINE_RE.finditer(normalized):
                marker_key = re.sub(r"\s+", "", match.group(0)).casefold()
                if marker_key == last_marker_key:
                    continue
                marker_matches.append(match)
                last_marker_key = marker_key

    n = len(marker_matches)
    _segment_debug(f"[SEGMENT_DEBUG] markers_found={n}")
    for idx, match in enumerate(marker_matches[:5], start=1):
        snippet = re.sub(r"\s+", " ", normalized[match.start() : match.start() + 140]).strip()
        _segment_debug(f"[SEGMENT_DEBUG] marker_{idx}={snippet}")

    if not marker_matches:
        return [normalized]

    starts_raw = [match.start() for match in marker_matches]
    if _PAGE_TAG_RE.search(normalized):
        page_tag_starts = [m.start() for m in _PAGE_TAG_RE.finditer(normalized)]
        page_aligned_starts: list[int] = []
        for marker_start in starts_raw:
            aligned_start = marker_start
            for tag_start in page_tag_starts:
                if tag_start <= marker_start:
                    aligned_start = tag_start
                else:
                    break
            page_aligned_starts.append(aligned_start)
        starts = sorted(set(page_aligned_starts))
    else:
        starts = sorted(set(starts_raw))

    if not starts:
        return [normalized]

    preface = normalized[: starts[0]].strip()
    blocks: list[str] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(normalized)
        chunk = normalized[start:end].strip()
        if chunk:
            blocks.append(chunk)

    if preface:
        if blocks:
            blocks[0] = f"{preface}\n\n{blocks[0]}".strip()
        else:
            blocks = [preface]
    return blocks


def _extract_page_numbers_from_tagged_text(text: str) -> list[int]:
    page_numbers: list[int] = []
    seen: set[int] = set()
    for match in _PAGE_TAG_RE.finditer(str(text or "")):
        try:
            page_no = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if page_no in seen:
            continue
        seen.add(page_no)
        page_numbers.append(page_no)
    return page_numbers


def _page_text_map_from_tagged_text(text: str) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for page_no, lines in _split_tagged_pages(text):
        page_map[page_no] = "\n".join(lines).strip()
    return page_map


def segment_by_document(full_text: str) -> list[str]:
    blocks = _split_document_blocks(full_text)
    if not blocks:
        return []

    cleaned: list[str] = []
    for block in blocks:
        if _PAGE_TAG_RE.search(block):
            candidate = _clean_tagged_segment_text(block)
        else:
            candidate = _clean_plain_segment_text(block)
        candidate = candidate.strip()
        if candidate:
            cleaned.append(candidate)

    return cleaned


def _page_count(segment: dict[str, Any]) -> int:
    pages = list(segment.get("pages") or [])
    if pages:
        return len(pages)
    start = _safe_page_no({"page_number": segment.get("page_start")}, 1)
    end = _safe_page_no({"page_number": segment.get("page_end")}, start)
    return max(1, end - start + 1)


def _is_generated_title(title: str) -> bool:
    return bool(_GENERATED_TITLE_RE.match(_normalize_ws(title)))


def _choose_doc_type(left: dict[str, Any], right: dict[str, Any], pages: list[dict[str, Any]]) -> str:
    left_type = str(left.get("doc_type_guess", "") or "").strip()
    right_type = str(right.get("doc_type_guess", "") or "").strip()
    if left_type and left_type != "Unklar" and (right_type in {"", "Unklar", left_type}):
        return left_type
    if right_type and right_type != "Unklar" and left_type in {"", "Unklar"}:
        return right_type
    if left_type == right_type and left_type:
        return left_type
    text = "\n".join(str(page.get("text", "") or "") for page in pages)
    return _guess_doc_type_from_text(text[:8000], title_hint=left_type or right_type)


def _choose_title(left: dict[str, Any], right: dict[str, Any], segment_id: int) -> str:
    left_title = str(left.get("title_guess", "") or "").strip()
    right_title = str(right.get("title_guess", "") or "").strip()
    if left_title and not _is_generated_title(left_title):
        return left_title[:100]
    if right_title and not _is_generated_title(right_title):
        return right_title[:100]
    return f"{_GENERIC_FALLBACK_TITLE} {segment_id}"


def _merge_segment_pair(segments: list[dict[str, Any]], left_idx: int, reason_tag: str) -> None:
    left = segments[left_idx]
    right = segments[left_idx + 1]
    merged_pages = list(left.get("pages") or []) + list(right.get("pages") or [])
    merged_doc_type = _choose_doc_type(left, right, merged_pages)

    left_conf = float(left.get("confidence", 0.5) or 0.5)
    right_conf = float(right.get("confidence", 0.5) or 0.5)
    merged_conf = round(max(0.2, min(0.98, max(left_conf, right_conf) * 0.96)), 2)

    left_reason = _normalize_ws(str(left.get("reason", "") or ""))
    right_reason = _normalize_ws(str(right.get("reason", "") or ""))
    reason_parts = [part for part in [left_reason, right_reason] if part]
    reason_parts.append(f"Merge: {reason_tag}")

    merged = dict(left)
    merged["pages"] = merged_pages
    merged["page_end"] = _safe_page_no(merged_pages[-1], _safe_page_no(right, 1))
    merged["end_page"] = merged["page_end"]
    merged["confidence"] = merged_conf
    merged["doc_type_guess"] = merged_doc_type or "Unklar"
    merged["title_guess"] = _choose_title(left, right, int(left.get("segment_id", left_idx + 1) or (left_idx + 1)))
    merged["reason"] = "; ".join(reason_parts[:5]) if reason_parts else "Heuristische Segmentierung"
    merged_boundary_reasons = list(left.get("boundary_reasons", []) or [])
    merged_boundary_reasons.extend(
        f"Merge: {entry}" for entry in list(right.get("boundary_reasons", []) or [])[:2]
    )
    merged["boundary_reasons"] = _dedupe_ordered(merged_boundary_reasons)[:6]
    merged["merge_count"] = int(left.get("merge_count", 0) or 0) + int(right.get("merge_count", 0) or 0) + 1
    segments[left_idx] = merged
    del segments[left_idx + 1]


def _candidate_merge_index_for_segment(segments: list[dict[str, Any]], idx: int) -> int:
    if idx <= 0:
        return 0
    if idx >= len(segments) - 1:
        return idx - 1

    left = segments[idx - 1]
    curr = segments[idx]
    right = segments[idx + 1]

    left_type = str(left.get("doc_type_guess", "") or "")
    curr_type = str(curr.get("doc_type_guess", "") or "")
    right_type = str(right.get("doc_type_guess", "") or "")

    left_same = left_type and curr_type and left_type == curr_type and curr_type != "Unklar"
    right_same = right_type and curr_type and right_type == curr_type and curr_type != "Unklar"
    if left_same and not right_same:
        return idx - 1
    if right_same and not left_same:
        return idx

    left_boundary_score = float(curr.get("boundary_score", 0.0) or 0.0)
    right_boundary_score = float(right.get("boundary_score", 0.0) or 0.0)
    left_strong = bool(curr.get("boundary_strong_marker", False))
    right_strong = bool(right.get("boundary_strong_marker", False))

    if left_strong != right_strong:
        return idx if left_strong else idx - 1
    return idx - 1 if left_boundary_score <= right_boundary_score else idx


def _find_weakest_boundary_merge_index(
    segments: list[dict[str, Any]],
    threshold: float,
    prefer_non_strong: bool = True,
) -> int | None:
    candidates: list[tuple[float, int]] = []
    for right_idx in range(1, len(segments)):
        left = segments[right_idx - 1]
        right = segments[right_idx]

        strong = bool(right.get("boundary_strong_marker", False))
        if prefer_non_strong and strong:
            continue

        left_type = str(left.get("doc_type_guess", "") or "")
        right_type = str(right.get("doc_type_guess", "") or "")
        same_type = left_type and right_type and left_type == right_type and left_type != "Unklar"
        boundary_score = float(right.get("boundary_score", 0.0) or 0.0)
        boundary_margin = float(right.get("boundary_margin", -threshold) or -threshold)
        low_conf = float(left.get("confidence", 1.0) or 1.0) < 0.58 or float(right.get("confidence", 1.0) or 1.0) < 0.58

        merge_cost = boundary_score
        if same_type:
            merge_cost -= 0.45
        if low_conf:
            merge_cost -= 0.25
        if boundary_margin <= 0.35:
            merge_cost -= 0.2
        if strong:
            merge_cost += 0.6
        candidates.append((merge_cost, right_idx - 1))

    if not candidates and prefer_non_strong:
        return _find_weakest_boundary_merge_index(segments, threshold, prefer_non_strong=False)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _renumber_segments(segments: list[dict[str, Any]]) -> None:
    for idx, segment in enumerate(segments, start=1):
        pages = list(segment.get("pages") or [])
        if pages:
            start_page = _safe_page_no(pages[0], idx)
            end_page = _safe_page_no(pages[-1], start_page)
        else:
            start_page = _safe_page_no({"page_number": segment.get("page_start")}, idx)
            end_page = _safe_page_no({"page_number": segment.get("page_end")}, start_page)
        segment["segment_id"] = idx
        segment["page_start"] = start_page
        segment["page_end"] = end_page
        segment["start_page"] = start_page
        segment["end_page"] = end_page
        if _is_generated_title(str(segment.get("title_guess", "") or "")):
            segment["title_guess"] = f"{_GENERIC_FALLBACK_TITLE} {idx}"


def _is_strong_marker(reasons: list[str], marker_score: float) -> bool:
    if marker_score >= 2.8:
        return True
    lowered_reasons = " | ".join(str(reason) for reason in reasons)
    return any(key.lower() in lowered_reasons.lower() for key in _STRONG_MARKER_KEYS)


def _effective_threshold(base_threshold: float, total_pages: int, adaptive: bool) -> float:
    threshold = base_threshold
    if not adaptive:
        return threshold
    if total_pages <= 5:
        threshold += 0.6
    elif total_pages >= 100:
        threshold += 0.45
    elif total_pages >= 50:
        threshold += 0.3
    return threshold


def _target_segment_range(total_pages: int) -> tuple[int, int]:
    lower = max(1, ceil(total_pages / 8))
    upper = max(lower, ceil(total_pages / 3))
    return lower, upper


def _attach_segmentation_meta(segments: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    for segment in segments:
        segment["segmentation_meta"] = dict(meta)


def _has_full_coverage(segments: list[dict[str, Any]], expected_pages: set[int]) -> bool:
    assigned: list[int] = []
    for segment in segments:
        for page in list(segment.get("pages") or []):
            assigned.append(_safe_page_no(page, 0))
    return len(assigned) == len(set(assigned)) and set(assigned) == expected_pages


def _build_segment(
    segment_id: int,
    segment_pages: list[dict[str, Any]],
    start_score: float,
    start_reason: str,
    title_guess: str,
    doc_type_guess: str,
    fallback_title: bool,
) -> dict[str, Any]:
    start_page = _safe_page_no(segment_pages[0], segment_id)
    end_page = _safe_page_no(segment_pages[-1], start_page)
    normalized_title = (
        f"{_GENERIC_FALLBACK_TITLE} {segment_id}" if fallback_title else (title_guess or _GENERIC_FALLBACK_TITLE)
    )

    confidence = round(max(0.2, min(0.98, start_score / 4.0)), 2)
    return {
        "segment_id": segment_id,
        "page_start": start_page,
        "page_end": end_page,
        "start_page": start_page,
        "end_page": end_page,
        "title_guess": normalized_title,
        "doc_type_guess": doc_type_guess or "Unklar",
        "confidence": confidence,
        "reason": start_reason,
        "pages": segment_pages,
    }


def _paragraph_units(page: dict[str, Any]) -> list[tuple[str, bool]]:
    text = str(page.get("text", "") or "")
    lines = text.splitlines()
    units: list[tuple[str, bool]] = []
    buffer: list[str] = []

    def flush(as_heading: bool = False) -> None:
        if not buffer:
            return
        paragraph = _normalize_ws(" ".join(buffer))
        if paragraph:
            units.append((paragraph, as_heading))
        buffer.clear()

    for raw in lines:
        line = raw.strip()
        if not line:
            flush(False)
            continue

        if _looks_like_heading(line):
            flush(False)
            units.append((_normalize_ws(line), True))
            continue

        buffer.append(line)

    flush(False)

    if not units and text.strip():
        units.append((_normalize_ws(text), False))
    return units


def _split_oversize_unit(text: str, max_chars: int) -> list[str]:
    cleaned = _normalize_ws(text)
    if len(cleaned) <= max_chars:
        return [cleaned]

    parts = re.split(r"(?<=[\.!?;:])\s+", cleaned)
    chunks: list[str] = []
    current = ""

    for part in parts:
        part = _normalize_ws(part)
        if not part:
            continue
        if not current:
            current = part
            continue

        candidate = f"{current} {part}"
        if len(candidate) <= max_chars:
            current = candidate
            continue

        chunks.append(current)
        current = part

    if current:
        chunks.append(current)

    if any(len(chunk) > max_chars for chunk in chunks):
        hard_split: list[str] = []
        for chunk in chunks:
            if len(chunk) <= max_chars:
                hard_split.append(chunk)
                continue
            words = chunk.split()
            current_words: list[str] = []
            for word in words:
                candidate = " ".join(current_words + [word])
                if current_words and len(candidate) > max_chars:
                    hard_split.append(" ".join(current_words))
                    current_words = [word]
                else:
                    current_words.append(word)
            if current_words:
                hard_split.append(" ".join(current_words))
        chunks = hard_split

    return [chunk for chunk in chunks if chunk]


def _stitch_pages_with_tags(pages: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for idx, page in enumerate(pages, start=1):
        page_no = _safe_page_no(page, idx)
        body = str(page.get("text", "") or "").strip()
        if body:
            blocks.append(f"[[PAGE:{page_no}]]\n{body}")
        else:
            blocks.append(f"[[PAGE:{page_no}]]")
    return "\n\n".join(blocks).strip()


def _extract_doc_id(text: str) -> str:
    match = re.search(r"\bdokument\s*[-_ ]?id\s*:\s*([^|\n]+)", str(text or ""), flags=re.IGNORECASE)
    if not match:
        return ""
    return _normalize_ws(match.group(1))[:80]


def _extract_doc_type(text: str) -> str:
    match = re.search(r"(?:^|\|)\s*typ\s*:\s*([^|\n]+)", str(text or ""), flags=re.IGNORECASE)
    if match:
        candidate = _normalize_ws(match.group(1))
        if candidate:
            return candidate[:80]
    return _guess_doc_type_from_text(str(text or "")[:8000])


def _segment_pages_from_document_markers(normalized_pages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    stitched = _stitch_pages_with_tags(normalized_pages)
    document_texts = segment_by_document(stitched)
    if not document_texts:
        return None

    marker_mode = bool(_DOCUMENT_MARKER_RE.search(_PAGE_TAG_RE.sub("", stitched)))
    start_to_text: dict[int, str] = {}
    for doc_text in document_texts:
        page_numbers = _extract_page_numbers_from_tagged_text(doc_text)
        if not page_numbers:
            continue
        if marker_mode and not _DOCUMENT_MARKER_RE.search(_PAGE_TAG_RE.sub("", doc_text)):
            continue
        start_page = min(page_numbers)
        if start_page not in start_to_text:
            start_to_text[start_page] = doc_text

    if not start_to_text:
        return None

    first_page = _safe_page_no(normalized_pages[0], 1)
    last_page = _safe_page_no(normalized_pages[-1], len(normalized_pages))
    marker_starts = sorted(start_to_text.keys())
    segments: list[dict[str, Any]] = []

    for idx, marker_start_page in enumerate(marker_starts, start=1):
        # Keep one segment per marker; attach any preface pages to the first marker segment.
        start_page = first_page if idx == 1 else marker_start_page
        end_page = (marker_starts[idx] - 1) if idx < len(marker_starts) else last_page
        segment_pages_slice = [
            dict(page)
            for page in normalized_pages
            if start_page <= _safe_page_no(page, 0) <= end_page
        ]
        if not segment_pages_slice:
            continue

        # Bereinigt Boilerplate/Fußzeilen pro erkanntem Dokument und verteilt
        # den bereinigten Text wieder auf Seitenbasis.
        cleaned_doc_blocks = segment_by_document(_stitch_pages_with_tags(segment_pages_slice))
        cleaned_doc = cleaned_doc_blocks[0] if cleaned_doc_blocks else _stitch_pages_with_tags(segment_pages_slice)
        page_text_map = _page_text_map_from_tagged_text(cleaned_doc)
        cleaned_pages: list[dict[str, Any]] = []
        for page in segment_pages_slice:
            cleaned_page = dict(page)
            page_no = _safe_page_no(cleaned_page, idx)
            cleaned_page["text"] = page_text_map.get(
                page_no,
                _clean_plain_segment_text(str(cleaned_page.get("text", "") or "")),
            )
            cleaned_pages.append(cleaned_page)

        source_doc_text = _PAGE_TAG_RE.sub("", start_to_text.get(marker_start_page, cleaned_doc))
        doc_id = _extract_doc_id(source_doc_text)
        doc_type_guess = _extract_doc_type(source_doc_text)
        title_guess = _title_guess(
            str(cleaned_pages[0].get("text", "") or ""),
            doc_type_guess,
            fallback_prefix=(doc_id or f"{_GENERIC_FALLBACK_TITLE} {idx}"),
        )
        if doc_id and doc_id not in title_guess:
            title_guess = f"{title_guess} [{doc_id}]"

        segments.append(
            _build_segment(
                segment_id=idx,
                segment_pages=cleaned_pages,
                start_score=4.4,
                start_reason="Dokumentgrenze über Marker '=== DOKUMENT START' erkannt",
                title_guess=title_guess,
                doc_type_guess=doc_type_guess,
                fallback_title=False,
            )
        )
        segments[-1]["boundary_score"] = (None if idx == 1 else 4.4)
        segments[-1]["boundary_margin"] = None
        segments[-1]["boundary_strong_marker"] = (idx > 1)
        segments[-1]["boundary_reasons"] = (
            ["Startseite (normalisiert)"] if idx == 1 else ["Marker: DOKUMENT START", "Marker: Dokumenten-ID"]
        )

    if not segments:
        return None

    expected_pages = {_safe_page_no(page, index + 1) for index, page in enumerate(normalized_pages)}
    coverage_complete = _has_full_coverage(segments, expected_pages)

    target_min_segments, target_max_segments = _target_segment_range(len(normalized_pages))
    avg_pages_per_segment = round(len(normalized_pages) / max(len(segments), 1), 2)
    _attach_segmentation_meta(
        segments,
        {
            "initial_segments": len(segments),
            "segments_after_merge": len(segments),
            "segments_merged": 0,
            "merged_due_to_cap": False,
            "min_pages_per_segment": 1,
            "max_segments_cap": get_config().segment_max_segments,
            "target_min_segments": target_min_segments,
            "target_max_segments": target_max_segments,
            "avg_pages_per_segment": avg_pages_per_segment,
            "effective_boundary_threshold": None,
            "base_boundary_threshold": None,
            "adaptive_threshold": False,
            "cooldown_pages": 0,
            "merge_counts": {},
            "coverage_complete": coverage_complete,
            "mode": "document_marker",
        },
    )
    return segments


def _dedupe_ordered(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_ws(value)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def _boundary_diagnostics_for_pages(normalized_pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for idx, page in enumerate(normalized_pages, start=1):
        page_no = _safe_page_no(page, idx)
        page_text = str(page.get("text", "") or "")
        lines = _non_empty_lines(page_text)
        top_lines = _top_lines(lines, limit=_BOUNDARY_TOP_LINES)
        top_text = "\n".join(top_lines)

        strong_hits = _strong_boundary_hits(top_text)
        title_keyword = _title_keyword_hit(top_text)
        has_meta_block, meta_reason = _meta_block_indicator(top_lines, top_text)
        title_meta = bool(title_keyword and has_meta_block)

        score = 0.0
        reasons: list[str] = []
        if strong_hits:
            score += 3.2 + min(1.4, 0.2 * len(strong_hits))
            reasons.extend([f"strong:{label}" for label in strong_hits[:3]])
        if title_meta:
            score += 2.4
            reasons.append(f"title:{title_keyword}+meta")
            if meta_reason:
                reasons.append(meta_reason)

        signal_groups: set[str] = set()
        if strong_hits:
            signal_groups.add("strong")
        if title_meta:
            signal_groups.add("title_meta")

        _, keyword_doc_type, _ = _keyword_score(top_text, page_text[:9000])
        # Contract-continuation: payment/billing details that belong to a Vertrag
        is_contract_cont = bool(_CONTRACT_CONTINUATION_RE.search(top_text))
        reasons = _dedupe_ordered(reasons)[:6]
        diagnostics.append(
            {
                "page": page_no,
                "score": round(score, 3),
                "marker_score": round(score if strong_hits else 0.0, 3),
                "is_strong": bool(strong_hits),
                "is_attachment": is_attachment_page(page_text),
                "is_contract_continuation": is_contract_cont,
                "strong_hits": strong_hits,
                "title_meta": title_meta,
                "title_keyword": title_keyword,
                "meta_reason": meta_reason,
                "doc_type_hint": keyword_doc_type,
                "reasons": reasons,
                "signal_groups": sorted(signal_groups),
            }
        )
    return diagnostics


def _candidate_boundaries_from_diagnostics(
    diagnostics: list[dict[str, Any]],
    threshold: float,
    cooldown_pages: int,
) -> list[int]:
    starts: set[int] = {1}
    last_start_page = 1
    strong_pages = [
        int(item.get("page", 0))
        for item in diagnostics
        if bool(item.get("is_strong")) and int(item.get("page", 0)) > 1
    ]
    non_strong_cooldown = max(cooldown_pages, 2)
    prev_strong_hits: set[str] = set()
    prev_doc_type: str = ""
    continuation_applied: bool = False  # True when a page was suppressed by is_contract_continuation

    for item in diagnostics:
        page_no = int(item.get("page", 0) or 0)
        curr_doc_type = str(item.get("doc_type_hint", "") or "")
        if page_no <= 1:
            prev_strong_hits = set(str(value) for value in list(item.get("strong_hits", []) or []))
            prev_doc_type = curr_doc_type
            continue
        score = float(item.get("score", 0.0) or 0.0)
        is_strong = bool(item.get("is_strong", False))
        strong_hits = {str(value) for value in list(item.get("strong_hits", []) or [])}
        strong_novel = bool((strong_hits - prev_strong_hits) - _GENERIC_STRONG_MARKERS)
        strong_priority = bool(strong_hits & _STRONG_PRIORITY_MARKERS)
        cooldown_override = bool(strong_hits & _HARD_COOLDOWN_OVERRIDE_MARKERS)
        title_meta = bool(item.get("title_meta", False))

        # Anlage-Seiten sind Bestandteil des übergeordneten Dokuments.
        if bool(item.get("is_attachment", False)):
            prev_strong_hits = strong_hits
            prev_doc_type = curr_doc_type
            continue

        # Zahlungsdetails/Rechnungsempfänger-Seiten innerhalb eines Vertrags sind
        # Bestandteil des übergeordneten Vertrags – kein Boundary erzeugen.
        if (
            bool(item.get("is_contract_continuation", False))
            and prev_doc_type in _VERTRAG_DOC_TYPES
            and (curr_doc_type in _VERTRAG_DOC_TYPES or curr_doc_type in {"Unklar", ""})
            and not cooldown_override
        ):
            continuation_applied = True
            prev_strong_hits = strong_hits
            prev_doc_type = curr_doc_type
            continue

        if not (is_strong or title_meta):
            prev_strong_hits = strong_hits
            prev_doc_type = curr_doc_type
            continue

        gap = page_no - last_start_page
        if is_strong:
            should_start = strong_novel or strong_priority
            if should_start and gap <= cooldown_pages and not cooldown_override:
                should_start = False
        else:
            should_start = score >= threshold and gap > non_strong_cooldown

        if should_start:
            starts.add(page_no)
            last_start_page = page_no
        prev_strong_hits = strong_hits
        prev_doc_type = curr_doc_type

    # Fallback is suppressed when continuation logic was applied: the document was
    # intentionally kept as a single segment, so recurring strong markers in body
    # pages must not re-introduce spurious boundaries.
    if len(starts) == 1 and len(strong_pages) >= 2 and not continuation_applied:
        fallback_starts = [1]
        fallback_last = 1
        for strong_page in strong_pages:
            if strong_page - fallback_last > non_strong_cooldown:
                fallback_starts.append(strong_page)
                fallback_last = strong_page
        starts = set(fallback_starts)

    return sorted(starts)


def _segments_from_boundary_starts(
    normalized_pages: list[dict[str, Any]],
    starts: list[int],
    diagnostics: list[dict[str, Any]],
    threshold: float,
) -> list[dict[str, Any]]:
    if not normalized_pages:
        return []
    if not starts:
        starts = [1]

    start_pages = sorted({1, *starts})
    by_page = {int(item.get("page", 0)): item for item in diagnostics}
    last_page = _safe_page_no(normalized_pages[-1], len(normalized_pages))

    segments: list[dict[str, Any]] = []
    for idx, start_page in enumerate(start_pages, start=1):
        end_page = (start_pages[idx] - 1) if idx < len(start_pages) else last_page
        segment_pages_slice = [
            dict(page)
            for page in normalized_pages
            if start_page <= _safe_page_no(page, 0) <= end_page
        ]
        if not segment_pages_slice:
            continue

        diag = by_page.get(start_page, {})
        boundary_score = float(diag.get("score", 0.0) or 0.0)
        boundary_reasons = list(diag.get("reasons", []) or [])
        if start_page == 1:
            boundary_reasons = ["Startseite (normalisiert)"] + boundary_reasons
        boundary_reasons = _dedupe_ordered(boundary_reasons)[:6]
        if not boundary_reasons:
            boundary_reasons = ["Heuristische Dokumentgrenze"]

        doc_type_hint = str(diag.get("doc_type_hint", "") or "")
        segment_text = "\n".join(str(page.get("text", "") or "") for page in segment_pages_slice)
        doc_type_guess = _guess_doc_type_from_text(segment_text[:10000], title_hint=doc_type_hint)
        title_guess = _title_guess(
            str(segment_pages_slice[0].get("text", "") or ""),
            doc_type_guess,
            fallback_prefix=f"{_GENERIC_FALLBACK_TITLE} {idx}",
        )

        score_for_conf = boundary_score
        if start_page == 1 and score_for_conf <= 0.0:
            score_for_conf = threshold
        segment = _build_segment(
            segment_id=idx,
            segment_pages=segment_pages_slice,
            start_score=score_for_conf,
            start_reason="; ".join(boundary_reasons[:3]),
            title_guess=title_guess,
            doc_type_guess=doc_type_guess,
            fallback_title=False,
        )
        segment["boundary_score"] = None if start_page == 1 else round(boundary_score, 3)
        segment["boundary_margin"] = None if start_page == 1 else round(boundary_score - threshold, 3)
        segment["boundary_strong_marker"] = bool(diag.get("is_strong", False)) and start_page > 1
        segment["boundary_reasons"] = boundary_reasons
        segments.append(segment)

    return segments


def _merge_micro_segments(
    segments: list[dict[str, Any]], threshold: float = 2.4
) -> tuple[list[dict[str, Any]], int]:
    if len(segments) <= 1:
        return segments, 0

    merged_count = 0
    changed = True
    while changed and len(segments) > 1:
        changed = False
        for idx, segment in enumerate(list(segments)):
            pages = list(segment.get("pages") or [])
            if len(pages) != 1:
                continue
            if bool(segment.get("boundary_strong_marker", False)):
                continue
            # Ausreichender Boundary-Score oder erkannter DocType → Segment behalten
            bs = float(segment.get("boundary_score") or 0.0)
            dt = str(segment.get("doc_type_guess", "") or "")
            if bs >= threshold or (dt and dt not in ("Unklar", "")):
                continue
            merge_left_idx = idx - 1 if idx > 0 else 0
            _merge_segment_pair(segments, merge_left_idx, reason_tag="microsegment_without_strong_marker")
            _renumber_segments(segments)
            merged_count += 1
            changed = True
            break

    return segments, merged_count


def _segment_pages_by_boundary_scoring(
    normalized_pages: list[dict[str, Any]],
    *,
    max_segments_cap: int | None = None,
    boundary_threshold: float | None = None,
    adaptive_threshold: bool | None = None,
    boundary_cooldown_pages: int | None = None,
) -> list[dict[str, Any]]:
    if not normalized_pages:
        return []

    total_pages = len(normalized_pages)
    cfg = get_config()
    base_threshold = (
        float(boundary_threshold)
        if boundary_threshold is not None
        else float(cfg.segment_boundary_threshold)
    )
    adaptive_threshold = (
        bool(adaptive_threshold)
        if adaptive_threshold is not None
        else bool(cfg.segment_adaptive_threshold)
    )
    effective_threshold = _effective_threshold(base_threshold, total_pages, adaptive_threshold)
    cooldown_pages = (
        int(boundary_cooldown_pages)
        if boundary_cooldown_pages is not None
        else int(cfg.segment_boundary_cooldown_pages)
    )
    max_segments_cap = (
        int(max_segments_cap)
        if max_segments_cap is not None
        else int(cfg.segment_max_segments)
    )

    diagnostics = _boundary_diagnostics_for_pages(normalized_pages)
    starts = _candidate_boundaries_from_diagnostics(
        diagnostics=diagnostics,
        threshold=effective_threshold,
        cooldown_pages=cooldown_pages,
    )
    segments = _segments_from_boundary_starts(
        normalized_pages=normalized_pages,
        starts=starts,
        diagnostics=diagnostics,
        threshold=effective_threshold,
    )

    if not segments:
        segment = _build_segment(
            segment_id=1,
            segment_pages=[dict(page) for page in normalized_pages],
            start_score=effective_threshold,
            start_reason="Startseite (normalisiert)",
            title_guess=_GENERIC_FALLBACK_TITLE,
            doc_type_guess=_guess_doc_type_from_text(
                "\n".join(str(page.get("text", "") or "") for page in normalized_pages)[:10000]
            ),
            fallback_title=True,
        )
        segment["boundary_score"] = None
        segment["boundary_margin"] = None
        segment["boundary_strong_marker"] = False
        segment["boundary_reasons"] = ["Startseite (normalisiert)"]
        segments = [segment]

    initial_segments = len(segments)
    merged_due_to_cap = False
    merge_counts: dict[str, int] = {}
    segments, merged_micro = _merge_micro_segments(segments, threshold=effective_threshold)
    if merged_micro > 0:
        merge_counts["microsegment_without_strong_marker"] = merged_micro

    while len(segments) > max_segments_cap:
        merge_idx = _find_weakest_boundary_merge_index(segments, threshold=effective_threshold)
        if merge_idx is None:
            break
        _merge_segment_pair(segments, merge_idx, reason_tag="max_segments_cap")
        _renumber_segments(segments)
        merged_due_to_cap = True
        merge_counts["max_segments_cap"] = merge_counts.get("max_segments_cap", 0) + 1

    expected_pages = {_safe_page_no(page, index + 1) for index, page in enumerate(normalized_pages)}
    coverage_complete = _has_full_coverage(segments, expected_pages)
    target_min_segments, target_max_segments = _target_segment_range(total_pages)
    avg_pages_per_segment = round(total_pages / max(len(segments), 1), 2)

    _attach_segmentation_meta(
        segments,
        {
            "initial_segments": initial_segments,
            "segments_after_merge": len(segments),
            "segments_merged": max(0, initial_segments - len(segments)),
            "merged_due_to_cap": merged_due_to_cap,
            "min_pages_per_segment": 1,
            "max_segments_cap": max_segments_cap,
            "target_min_segments": target_min_segments,
            "target_max_segments": target_max_segments,
            "avg_pages_per_segment": avg_pages_per_segment,
            "effective_boundary_threshold": round(effective_threshold, 3),
            "base_boundary_threshold": base_threshold,
            "adaptive_threshold": adaptive_threshold,
            "cooldown_pages": cooldown_pages,
            "merge_counts": merge_counts,
            "coverage_complete": coverage_complete,
            "candidate_boundaries": starts,
            "mode": "boundary_scoring_v2",
        },
    )
    return segments


def _segment_text_with_page_markers(pages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for idx, page in enumerate(pages, start=1):
        page_no = _safe_page_no(page, idx)
        page_text = str(page.get("text", "") or "").strip()
        if page_text:
            parts.append(f"[Seite {page_no}]\n{page_text}")
        else:
            parts.append(f"[Seite {page_no}]")
    return "\n\n".join(parts).strip()


def build_segment_chunks(
    pages: list[dict[str, Any]],
    max_chars_per_chunk: int | None = None,
) -> list[dict[str, Any]]:
    """
    Baut Segment-Chunks rein auf Zeichenbudget-Basis.
    Ein Segment wird vollständig in einem LLM-Call gesendet, solange es <= 25.000 Zeichen bleibt.
    """
    if not pages:
        return []

    del max_chars_per_chunk
    max_chars = 25000
    full_segment_text = _segment_text_with_page_markers(pages)
    all_pages = sorted({_safe_page_no(page, idx + 1) for idx, page in enumerate(pages)})
    if not full_segment_text:
        return []

    if len(full_segment_text) <= max_chars:
        return [
            {
                "chunk_index": 1,
                "pages": all_pages or [1],
                "page_start": (all_pages[0] if all_pages else 1),
                "page_end": (all_pages[-1] if all_pages else 1),
                "char_count": len(full_segment_text),
                "text": full_segment_text,
            }
        ]

    paragraphs = [part.strip() for part in re.split(r"\n{2,}", full_segment_text) if part.strip()]
    chunks: list[dict[str, Any]] = []
    current_parts: list[str] = []
    for para in paragraphs:
        if len(para) > max_chars:
            split_units = _split_oversize_unit(para, max_chars=max_chars)
        else:
            split_units = [para]
        for unit in split_units:
            candidate = "\n\n".join(current_parts + [unit]).strip()
            if current_parts and len(candidate) > max_chars:
                chunk_text = "\n\n".join(current_parts).strip()
                chunk_pages = sorted({int(m.group(1)) for m in re.finditer(r"\[Seite\s+(\d+)\]", chunk_text)})
                chunk_pages = chunk_pages or all_pages or [1]
                chunks.append(
                    {
                        "chunk_index": len(chunks) + 1,
                        "pages": chunk_pages,
                        "page_start": chunk_pages[0],
                        "page_end": chunk_pages[-1],
                        "char_count": len(chunk_text),
                        "text": chunk_text,
                    }
                )
                current_parts = [unit]
            else:
                current_parts.append(unit)

    if current_parts:
        chunk_text = "\n\n".join(current_parts).strip()
        chunk_pages = sorted({int(m.group(1)) for m in re.finditer(r"\[Seite\s+(\d+)\]", chunk_text)})
        chunk_pages = chunk_pages or all_pages or [1]
        chunks.append(
            {
                "chunk_index": len(chunks) + 1,
                "pages": chunk_pages,
                "page_start": chunk_pages[0],
                "page_end": chunk_pages[-1],
                "char_count": len(chunk_text),
                "text": chunk_text,
            }
        )
    return chunks


def merge_structured_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Merged strukturierte Chunk-Analysen zu einem Segment-Resultat.
    """
    if not payloads:
        return {
            "dokumenttyp_parteien": "missing",
            "kurzueberblick": "missing",
            "top3_findings": [],
            "kernaussagen": [],
            "auffaelligkeiten": [],
            "prueffragen": [],
            "todos": [],
            "missing_info": [],
        }

    def dedupe_string(items: list[str], limit: int) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            value = _normalize_ws(item)
            norm = value.casefold()
            if not value or norm in seen:
                continue
            seen.add(norm)
            out.append(value)
            if len(out) >= limit:
                break
        return out

    def dedupe_objects(items: list[dict[str, Any]], text_key: str, limit: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            text = _normalize_ws(str(item.get(text_key, "") or ""))
            norm = text.casefold()
            if not text or norm in seen:
                continue
            seen.add(norm)
            normalized = dict(item)
            normalized[text_key] = text
            out.append(normalized)
            if len(out) >= limit:
                break
        return out

    doc_type_candidates = [
        _normalize_ws(str(payload.get("dokumenttyp_parteien", "") or ""))
        for payload in payloads
        if isinstance(payload, dict)
    ]

    def doc_score(value: str) -> tuple[int, int]:
        lowered = value.lower()
        score = 0
        if "dokumenttyp" in lowered:
            score += 2
        if re.search(r"\b(kläger|klaeger|beklagt|mieter|vermieter|arbeitgeber|arbeitnehmer)\b", lowered):
            score += 2
        if "missing" not in lowered:
            score += 1
        return score, len(value)

    best_doc = "missing"
    for candidate in doc_type_candidates:
        if doc_score(candidate) > doc_score(best_doc):
            best_doc = candidate

    kurzueberblick = "missing"
    for payload in payloads:
        candidate = _normalize_ws(str(payload.get("kurzueberblick", "") or ""))
        if candidate and candidate.lower() != "missing":
            kurzueberblick = candidate
            break

    top3_findings = dedupe_objects(
        [item for payload in payloads for item in list(payload.get("top3_findings", []) or [])],
        "title",
        limit=9,
    )
    kernaussagen = dedupe_objects(
        [item for payload in payloads for item in list(payload.get("kernaussagen", []) or [])],
        "text",
        limit=40,
    )
    auffaelligkeiten = dedupe_objects(
        [item for payload in payloads for item in list(payload.get("auffaelligkeiten", []) or [])],
        "text",
        limit=40,
    )

    prueffragen = dedupe_string(
        [str(x) for payload in payloads for x in list(payload.get("prueffragen", []) or [])],
        limit=40,
    )
    todos = dedupe_string(
        [str(x) for payload in payloads for x in list(payload.get("todos", []) or [])],
        limit=40,
    )
    missing_info = dedupe_string(
        [str(x) for payload in payloads for x in list(payload.get("missing_info", []) or [])],
        limit=40,
    )

    return {
        "dokumenttyp_parteien": best_doc,
        "kurzueberblick": kurzueberblick,
        "top3_findings": top3_findings[:3],
        "kernaussagen": kernaussagen,
        "auffaelligkeiten": auffaelligkeiten,
        "prueffragen": prueffragen,
        "todos": todos,
        "missing_info": missing_info,
    }


def segment_pages(
    pages: list[dict[str, Any]],
    max_pages_per_segment: int = 12,
    *,
    max_segments_cap: int | None = None,
    boundary_threshold: float | None = None,
    adaptive_threshold: bool | None = None,
    boundary_cooldown_pages: int | None = None,
) -> list[dict[str, Any]]:
    """
    Segmentiert Seiten strikt auf Dokumentebene.
    Ein Segment entspricht genau einem logischen Dokument-Block.

    Input: pages = [{"page_number": int, "text": str}, ...]
    Output: segments = [{
        segment_id, page_start, page_end, title_guess, doc_type_guess,
        confidence, reason, pages
    }, ...]
    """
    del max_pages_per_segment
    normalized_pages = _to_normalized_pages(pages)
    if not normalized_pages:
        return []

    marker_segments = _segment_pages_from_document_markers(normalized_pages)
    if marker_segments is not None and len(marker_segments) > 1:
        return marker_segments
    return _segment_pages_by_boundary_scoring(
        normalized_pages,
        max_segments_cap=max_segments_cap,
        boundary_threshold=boundary_threshold,
        adaptive_threshold=adaptive_threshold,
        boundary_cooldown_pages=boundary_cooldown_pages,
    )
