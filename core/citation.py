"""core/citation.py - Citation verification logic.

Split out of core/evidence.py (PR5) and core/analyzer.py (Step 5).
Contains summarize_citation_verification, citation-matching helpers,
token matching, and verify_citations_for_fields.
"""

import re
from difflib import SequenceMatcher
from typing import Any

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - offline fallback
    fuzz = None

from core.models import (
    ValidationError,
    clean_snippet,
    _normalize_citations_before,
    CitationModel,
)
from core.snippet import (
    _normalize_for_match,
    _to_short_citation_quote,
)


# ---------------------------------------------------------------------------
# Basic helpers (originally in citation.py since PR5)
# ---------------------------------------------------------------------------


def _coerce_non_negative_int(value: Any) -> int | None:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return None
    return num if num >= 0 else None


def _normalize_item(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        return {"text": item, "citations": []}
    if isinstance(item, dict):
        normalized = dict(item)
        if "text" not in normalized and "title" in normalized:
            normalized["text"] = normalized.get("title", "")
        normalized.setdefault("citations", [])
        return normalized
    return {"text": str(item), "citations": []}


def _claim_is_verified(item: dict[str, Any]) -> bool:
    status = str(item.get("citation_status", "") or "").strip().lower()
    if status in {"verified", "unverified"}:
        return status == "verified"
    verified_hits = item.get("citations_verified")
    if isinstance(verified_hits, list):
        return bool(verified_hits)
    return False


def summarize_citation_verification(
    parsed: dict[str, Any] | None,
    fields: tuple[str, ...],
) -> dict[str, Any]:
    claim_verified_count = 0
    claim_unverified_count = 0
    presence_verified_count = 0
    total_items = 0
    check_ran = False
    metrics_raw = None

    if isinstance(parsed, dict):
        check_ran = bool(parsed.get("citation_check_ran", False))
        metrics_raw = parsed.get("citation_metrics")

        for field in fields:
            items = parsed.get(field, [])
            if not isinstance(items, list):
                continue
            for raw_item in items:
                item = _normalize_item(raw_item)
                citations = item.get("citations", []) if isinstance(item.get("citations"), list) else []
                total_items += 1
                if citations:
                    presence_verified_count += 1
                if _claim_is_verified(item):
                    claim_verified_count += 1
                else:
                    claim_unverified_count += 1
                if isinstance(raw_item, dict) and (
                    "citation_status" in raw_item
                    or "citation_candidates" in raw_item
                    or "citations_verified" in raw_item
                ):
                    check_ran = True

    if total_items == 0 and isinstance(metrics_raw, dict):
        claim_verified_count = (
            _coerce_non_negative_int(
                metrics_raw.get("claim_verified_items", metrics_raw.get("verified_items"))
            )
            or 0
        )
        claim_unverified_count = (
            _coerce_non_negative_int(
                metrics_raw.get("claim_unverified_items", metrics_raw.get("unverified_items"))
            )
            or 0
        )
        total_items = (
            _coerce_non_negative_int(metrics_raw.get("total_items"))
            or claim_verified_count + claim_unverified_count
        )
        presence_verified_count = (
            _coerce_non_negative_int(
                metrics_raw.get("presence_verified_items", metrics_raw.get("verified_items"))
            )
            or 0
        )
        presence_verified_count = min(presence_verified_count, total_items)
        claim_verified_count = min(claim_verified_count, total_items)
        claim_unverified_count = max(total_items - claim_verified_count, 0)

    status = (
        "verified"
        if total_items > 0 and claim_unverified_count == 0
        else "partial"
        if claim_verified_count > 0
        else "unverified"
    )
    return {
        "citation_status": status,
        "citation_metrics": {
            "verified_items": claim_verified_count,
            "unverified_items": claim_unverified_count,
            "claim_verified_items": claim_verified_count,
            "claim_unverified_items": claim_unverified_count,
            "presence_verified_items": presence_verified_count,
            "presence_unverified_items": max(total_items - presence_verified_count, 0),
            "total_items": total_items,
        },
        "citation_check_ran": bool(check_ran),
    }


# ---------------------------------------------------------------------------
# Token / quote matching helpers (moved from analyzer.py, Step 5)
# ---------------------------------------------------------------------------


def _normalize_token(token: str) -> str:
    t = token.lower().strip()
    t = t.replace("ß", "ss")
    t = t.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    t = re.sub(r"[^\w]", "", t)
    return t


def _find_token_window_slice(page_text: str, candidate_text: str) -> str | None:
    cand_raw = re.findall(r"\S+", candidate_text or "")
    cand = [_normalize_token(w) for w in cand_raw]
    cand = [w for w in cand if w]
    if not cand:
        return None

    page_words = list(re.finditer(r"\S+", page_text or ""))
    page_norm = [_normalize_token(m.group(0)) for m in page_words]
    n = len(cand)
    if n == 0 or len(page_norm) < n:
        return None

    for i in range(0, len(page_norm) - n + 1):
        if page_norm[i : i + n] == cand:
            start = page_words[i].start()
            end = page_words[i + n - 1].end()
            return page_text[start:end]
    return None


def _find_quote_slice(page_text: str, candidate_text: str) -> str | None:
    if not page_text or not candidate_text:
        return None
    idx = page_text.find(candidate_text)
    if idx != -1:
        return page_text[idx : idx + len(candidate_text)]
    lower_idx = page_text.lower().find(candidate_text.lower())
    if lower_idx != -1:
        return page_text[lower_idx : lower_idx + len(candidate_text)]
    words = re.findall(r"\S+", candidate_text)
    if not words:
        return None
    pattern = r"\b" + r"\s+".join(re.escape(w) for w in words) + r"\b"
    match = re.search(pattern, page_text, flags=re.IGNORECASE)
    if match:
        return page_text[match.start() : match.end()]
    token_window = _find_token_window_slice(page_text, candidate_text)
    if token_window:
        return token_window
    return None


def _iter_windows(page_text: str, max_words: int = 20, step: int = 1) -> list[str]:
    return [text for text, _span in _iter_windows_with_span(page_text, max_words=max_words, step=step)]


def _iter_windows_with_span(
    page_text: str, max_words: int = 20, step: int = 1
) -> list[tuple[str, tuple[int, int]]]:
    """Like _iter_windows, but also yields each window's (start, end) char offsets.

    Needed so callers can tell whether two scored windows are actually distinct
    locations in the text, or just overlapping/nested slices of the same match
    (e.g. "spätestens zum X" vs "bis spätestens zum X" starting one word apart).
    """
    tokens = list(re.finditer(r"\S+", page_text or ""))
    if not tokens:
        return []

    windows: list[tuple[str, tuple[int, int]]] = []
    end_limit = min(max_words, len(tokens))
    for window_words in range(3, end_limit + 1):
        for start in range(0, len(tokens) - window_words + 1, step):
            start_pos = tokens[start].start()
            end_pos = tokens[start + window_words - 1].end()
            windows.append((page_text[start_pos:end_pos], (start_pos, end_pos)))
    return windows


def _partial_ratio(a: str, b: str) -> float:
    if fuzz is not None:
        return float(fuzz.partial_ratio(a, b))
    return SequenceMatcher(None, a, b).ratio() * 100


# ---------------------------------------------------------------------------
# Citation matching helpers (moved from analyzer.py, Step 5)
# ---------------------------------------------------------------------------


def _match_citation_candidate(candidate: CitationModel, pages: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, bool]:
    from core.evidence import _get_page_no

    candidate_text = clean_snippet(candidate.quote_snippet, max_chars=180)
    if not candidate_text:
        return None, False

    candidate_page = candidate.page_number
    scoped_pages = pages
    if candidate_page:
        preferred = [p for p in pages if _get_page_no(p) == candidate_page]
        fallback = [p for p in pages if _get_page_no(p) != candidate_page]
        scoped_pages = preferred + fallback

    exact_hits: list[tuple[int, str]] = []
    for page in scoped_pages:
        page_text = page.get("text", "")
        if not page_text:
            continue

        quote = _find_quote_slice(page_text, candidate_text)
        if quote:
            exact_hits.append((_get_page_no(page), quote))

    if len(exact_hits) == 1:
        page_no, quote = exact_hits[0]
        return {
            "page_number": page_no,
            "quote_snippet": _to_short_citation_quote(quote),
            "section_hint": candidate.section_hint,
        }, True
    if len(exact_hits) > 1:
        if candidate_page:
            for page_no, quote in exact_hits:
                if page_no == candidate_page:
                    return {
                        "page_number": page_no,
                        "quote_snippet": _to_short_citation_quote(quote),
                        "section_hint": candidate.section_hint,
                    }, True
        page_no, quote = exact_hits[0]
        return {
            "page_number": page_no,
            "quote_snippet": _to_short_citation_quote(quote),
            "section_hint": candidate.section_hint,
        }, True

    best_score = -1
    best_match: tuple[int, str] | None = None
    best_span: tuple[int, int] | None = None
    best_page_no: int | None = None
    second_best = -1
    candidate_norm = _normalize_for_match(candidate_text)
    # Collect every scored window (page_no, score, span, text) so the uniqueness
    # margin can be recomputed below against only non-overlapping competitors.
    scored_windows: list[tuple[int, float, tuple[int, int], str]] = []
    for page in scoped_pages:
        page_text = page.get("text", "")
        candidate_words = len(re.findall(r"\S+", candidate_text))
        local_max_words = max(3, min(20, candidate_words + 4))
        page_no = _get_page_no(page)
        for window, span in _iter_windows_with_span(page_text, max_words=local_max_words, step=1):
            score = _partial_ratio(candidate_norm, _normalize_for_match(window))
            scored_windows.append((page_no, score, span, window))
            if score > best_score:
                best_score = score
                best_match = (page_no, window)
                best_span = span
                best_page_no = page_no

    # Sliding windows (step=1) heavily overlap, so the raw runner-up is almost
    # always the same match shifted by one word (e.g. "spätestens zum X" vs
    # "bis spätestens zum X"). Comparing against that would make the uniqueness
    # margin reject correct matches. Instead, only windows that do NOT overlap
    # the best match's span on the same page count as competing candidates —
    # windows on other pages always count. This keeps the margin meaningful:
    # a quote that genuinely appears twice in distinct locations is still
    # flagged as ambiguous and rejected.
    second_best = -1
    if best_span is not None:
        best_start, best_end = best_span
        for page_no, score, (start, end), _window in scored_windows:
            if page_no == best_page_no and start < best_end and end > best_start:
                continue  # overlapping window on the same page - not a distinct location
            if score > second_best:
                second_best = score

    if best_match and best_score >= 80 and (best_score - second_best) >= 1:
        page_no, quote = best_match
        return {
            "page_number": page_no,
            "quote_snippet": _to_short_citation_quote(quote),
            "section_hint": candidate.section_hint,
        }, True

    return None, False


def _build_page_text_index(pages: list[dict[str, Any]]) -> str:
    from core.evidence import _get_page_no

    return "\n\n".join(
        f"[Seite {_get_page_no(page)}]\n{str(page.get('text', '') or '')}" for page in pages
    )


def _normalized_citation_dict(raw_citation: Any) -> dict[str, Any] | None:
    try:
        candidate = CitationModel.model_validate(raw_citation)
    except ValidationError:
        return None
    return {
        "page_number": candidate.page_number,
        "quote_snippet": _to_short_citation_quote(candidate.quote_snippet),
        "section_hint": candidate.section_hint,
    }


def _citation_usage_key(citation: dict[str, Any]) -> tuple[int | None, str]:
    page = citation.get("page_number")
    page_no = _coerce_non_negative_int(page) if page is not None else None
    quote = _normalize_for_match(str(citation.get("quote_snippet", "") or ""))
    return page_no, quote


def _heuristic_citation_for_item_text(
    item_text: str,
    pages: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, float]:
    from core.evidence import _get_page_no

    candidate_text = clean_snippet(str(item_text or ""), max_chars=180)
    if not candidate_text:
        return None, 0.0

    # Prefer exact local slices first to keep snippets semantically tight.
    for page in pages:
        page_text = str(page.get("text", "") or "")
        if not page_text:
            continue
        quote = _find_quote_slice(page_text, candidate_text)
        if quote:
            return (
                {
                    "page_number": _get_page_no(page),
                    "quote_snippet": _to_short_citation_quote(quote),
                    "section_hint": "heuristic:text-match",
                },
                1.0,
            )

    candidate_norm = _normalize_for_match(candidate_text)
    if not candidate_norm:
        return None, 0.0

    candidate_words = len(re.findall(r"\S+", candidate_text))
    local_max_words = max(4, min(20, candidate_words + 4))
    best_score = -1.0
    best_match: tuple[int, str] | None = None
    for page in pages:
        page_text = str(page.get("text", "") or "")
        if not page_text:
            continue
        for window in _iter_windows(page_text, max_words=local_max_words, step=1):
            score = _partial_ratio(candidate_norm, _normalize_for_match(window))
            if score > best_score:
                best_score = score
                best_match = (_get_page_no(page), window)

    if best_match and best_score >= 88:
        page_no, quote = best_match
        return (
            {
                "page_number": page_no,
                "quote_snippet": _to_short_citation_quote(quote),
                "section_hint": "heuristic:fuzzy-match",
            },
            round(best_score / 100.0, 3),
        )
    return None, 0.0


# ---------------------------------------------------------------------------
# Citation verification (moved from analyzer.py, Step 5)
# ---------------------------------------------------------------------------


def verify_citations_for_fields(parsed: dict, pages: list[dict[str, Any]], fields: tuple[str, ...]) -> dict:
    from core.evidence import _searchable_pages_without_repeating_edges

    parsed = dict(parsed)
    searchable_pages = _searchable_pages_without_repeating_edges(pages)

    for field in fields:
        items = parsed.get(field, [])
        if not isinstance(items, list):
            continue

        normalized_items: list[dict[str, Any]] = []
        for raw_item in items:
            item = _normalize_item(raw_item)
            raw_citations = item.get("citations", [])
            has_original_citations = isinstance(raw_citations, list) and bool(raw_citations)
            preserved_citations: list[dict[str, Any]] = []
            if isinstance(raw_citations, list):
                for citation in raw_citations:
                    if isinstance(citation, dict):
                        preserved_citations.append(dict(citation))
                    else:
                        normalized = _normalized_citation_dict(citation)
                        if normalized:
                            preserved_citations.append(dict(normalized))

            normalized_candidates: list[dict[str, Any]] = []
            verified_citations: list[dict[str, Any]] = []
            for raw_citation in _normalize_citations_before(raw_citations):
                citation_dict = _normalized_citation_dict(raw_citation)
                if not citation_dict:
                    continue
                normalized_candidates.append(dict(citation_dict))
                candidate = CitationModel.model_validate(citation_dict)
                match, is_verified = _match_citation_candidate(candidate, searchable_pages)
                if match and is_verified:
                    verified_citations.append(match)

            deduped_verified: list[dict[str, Any]] = []
            seen_match_keys: set[tuple[int | None, str]] = set()
            for citation in verified_citations:
                key = _citation_usage_key(citation)
                if key in seen_match_keys:
                    continue
                seen_match_keys.add(key)
                deduped_verified.append(citation)

            if has_original_citations:
                citations_out = preserved_citations or [dict(item) for item in normalized_candidates]
                item["citations"] = citations_out
                item["citation_candidates"] = [dict(item) for item in normalized_candidates]
                item["citations_verified"] = [dict(item) for item in deduped_verified]
                item["best_evidence"] = dict(deduped_verified[0]) if deduped_verified else None
                total_candidates = max(len(normalized_candidates), len(citations_out), 1)
                item["citation_match_confidence"] = round(len(deduped_verified) / float(total_candidates), 3)
                item["citation_heuristic_added"] = False
                item["citation_source"] = "llm"
                item["citation_status"] = "verified" if deduped_verified else "unverified"
            else:
                heuristic_citation, heuristic_confidence = _heuristic_citation_for_item_text(
                    str(item.get("text", "") or item.get("title", "") or ""),
                    searchable_pages,
                )
                item["citations"] = [dict(heuristic_citation)] if heuristic_citation else []
                item["citation_candidates"] = []
                item["citations_verified"] = []
                item["best_evidence"] = dict(heuristic_citation) if heuristic_citation else None
                item["citation_match_confidence"] = float(heuristic_confidence)
                item["citation_heuristic_added"] = bool(heuristic_citation)
                item["citation_source"] = "heuristic" if heuristic_citation else "none"
                # Claim-verified remains tied to validation of LLM-provided citations only.
                item["citation_status"] = "unverified"

            normalized_items.append(item)

        parsed[field] = normalized_items

    summary = summarize_citation_verification(parsed, fields)
    parsed["citation_status"] = summary["citation_status"]
    parsed["citation_metrics"] = summary["citation_metrics"]
    parsed["citation_check_ran"] = True
    return parsed


def maybe_verify_citations_for_fields(
    parsed: dict,
    pages: list[dict[str, Any]],
    fields: tuple[str, ...],
    *,
    enabled: bool,
) -> dict:
    if enabled:
        return verify_citations_for_fields(parsed, pages, fields)
    out = dict(parsed or {})
    summary = summarize_citation_verification(out, fields)
    out["citation_status"] = "unchecked"
    out["citation_metrics"] = summary["citation_metrics"]
    out["citation_check_ran"] = False
    return out
