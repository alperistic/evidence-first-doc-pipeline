"""core/utils.py – Generische Hilfsfunktionen für die LATE-Pipeline.

Keine Streamlit-Importe. Keine Pipeline-Orchestrierung.
Wird von core/pipeline.py, app.py und ui/components.py importiert.
"""

import os
import re
from typing import Any

from core.models import clean_snippet

# ---------------------------------------------------------------------------
# Regex-Konstanten
# ---------------------------------------------------------------------------
_TAGGED_PAGE_RE = re.compile(r"\[\[PAGE:(\d+)\]\]")
_DOC_TYPE_HINT_RE = re.compile(r"(?:^|\|)\s*typ\s*:\s*([^|\n]+)", flags=re.IGNORECASE)

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _page_no(page: dict) -> int:
    return int(page.get("page_number") or page.get("page") or 0)


def _stitch_pages_with_tags(pages: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for idx, page in enumerate(pages, start=1):
        page_no = _page_no(page) or idx
        page_text = str(page.get("text", "") or "").strip()
        if page_text:
            blocks.append(f"[[PAGE:{page_no}]]\n{page_text}")
        else:
            blocks.append(f"[[PAGE:{page_no}]]")
    return "\n\n".join(blocks).strip()


def _split_tagged_pages(text: str) -> list[tuple[int, str]]:
    matches = list(_TAGGED_PAGE_RE.finditer(str(text or "")))
    if not matches:
        return []
    result: list[tuple[int, str]] = []
    for idx, match in enumerate(matches):
        try:
            page_no = int(match.group(1))
        except (TypeError, ValueError):
            continue
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        page_text = str(text[start:end]).strip()
        result.append((page_no, page_text))
    return result


def _doc_type_from_block(block_text: str) -> str:
    match = _DOC_TYPE_HINT_RE.search(str(block_text or ""))
    if not match:
        return "Unklar"
    value = str(match.group(1) or "").strip()
    return value[:80] if value else "Unklar"


def _sum_present_tokens(values: list[int | None]) -> int | None:
    present = [int(v) for v in values if isinstance(v, int) and v >= 0]
    if not present:
        return None
    return sum(present)


def _clean_quote(text: str) -> str:
    return clean_snippet(str(text or ""), max_chars=180)


def _safe_text(value: str | None, fallback: str = "fehlende Angabe") -> str:
    text = (value or "").strip()
    return text if text else fallback


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _dedupe_str_list(values: list[str], limit: int = 50) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _safe_text(str(raw or ""), fallback="").strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _build_marker_segments(segment_blocks: list[str], redacted_pages: list[dict]) -> list[dict]:
    pages_by_no = {int(_page_no(page)): dict(page) for page in redacted_pages if _page_no(page)}
    segments: list[dict] = []
    for idx, block in enumerate(segment_blocks, start=1):
        tagged_pages = _split_tagged_pages(block)
        if not tagged_pages:
            continue
        page_numbers = [page_no for page_no, _ in tagged_pages]
        page_text_map = {page_no: text for page_no, text in tagged_pages}
        segment_pages: list[dict] = []
        for page_no in sorted(set(page_numbers)):
            source_page = pages_by_no.get(int(page_no))
            if not source_page:
                continue
            prepared_page = dict(source_page)
            prepared_page["text"] = page_text_map.get(page_no, str(source_page.get("text", "") or ""))
            segment_pages.append(prepared_page)
        if not segment_pages:
            continue
        first_line = next(
            (line.strip() for line in str(segment_pages[0].get("text", "") or "").splitlines() if line.strip()),
            "",
        )
        segments.append(
            {
                "segment_id": idx,
                "page_start": min(page_numbers),
                "page_end": max(page_numbers),
                "start_page": min(page_numbers),
                "end_page": max(page_numbers),
                "title_guess": first_line[:100] if first_line else f"Dokumentsegment {idx}",
                "doc_type_guess": _doc_type_from_block(block),
                "confidence": 1.0,
                "reason": "Dokumentgrenze über Marker erkannt",
                "pages": segment_pages,
            }
        )

    if segments:
        return segments

    if not redacted_pages:
        return []

    return [
        {
            "segment_id": 1,
            "page_start": _page_no(redacted_pages[0]) or 1,
            "page_end": _page_no(redacted_pages[-1]) or 1,
            "start_page": _page_no(redacted_pages[0]) or 1,
            "end_page": _page_no(redacted_pages[-1]) or 1,
            "title_guess": "Dokumentsegment 1",
            "doc_type_guess": "Unklar",
            "confidence": 1.0,
            "reason": "Kein Dokumentmarker gefunden",
            "pages": [dict(page) for page in redacted_pages],
        }
    ]
