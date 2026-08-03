"""core/layout_analyzer.py – Layout analysis and boundary scoring.

Extracted from core/segmenter.py (W2 god-file split).
Contains marker constants, scoring functions for page boundaries,
and the attachment/contract-continuation detection logic.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from core.doctype_classifier import _DOC_TYPE_KEYWORDS

# ---------------------------------------------------------------------------
# Marker constants
# ---------------------------------------------------------------------------

_START_MARKERS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("DOKUMENT START", re.compile(r"\bdokument\s*start\b", re.IGNORECASE), 3.4),
    ("Dokument X/Y", re.compile(r"\bdokument\s+\d+\s*/\s*\d+\b", re.IGNORECASE), 3.0),
    ("Dokumenten-ID", re.compile(r"\b(?:dokument(?:en)?[-_\s]*id|doc[-_\s]*id)\b", re.IGNORECASE), 2.8),
    ("Aktenzeichen", re.compile(r"\b(?:az\.?|aktenzeichen)\b", re.IGNORECASE), 2.6),
    ("Betreff", re.compile(r"^\s*betreff\s*:", re.IGNORECASE), 2.2),
    ("Absender", re.compile(r"^\s*absender\s*:", re.IGNORECASE), 2.1),
    ("Empfänger", re.compile(r"^\s*empf(?:a|ä)nger\s*:", re.IGNORECASE), 2.1),
    ("Vertragsparteien", re.compile(r"\bvertragsparteien\b", re.IGNORECASE), 2.2),
    ("wird geschlossen", re.compile(r"\bwird\s+geschlossen\b", re.IGNORECASE), 1.8),
    ("Vereinbarung", re.compile(r"\bvereinbarung\b", re.IGNORECASE), 1.9),
    ("Vergleich", re.compile(r"\bvergleich\b", re.IGNORECASE), 2.0),
    ("Anlage", re.compile(r"^\s*anlagen?(?:\s+[a-z0-9]+)?\b", re.IGNORECASE), 2.0),
)

_KEY_VALUE_LINE_RE = re.compile(r"^\s*[A-ZÄÖÜa-zäöüß][^:\n]{1,42}:\s+\S")
_SHORT_TITLE_LINE_RE = re.compile(r"^[A-ZÄÖÜ0-9][^.!?]{3,100}$")
_BOUNDARY_TOP_LINES = 25
_META_TOP_LINES = 18

_BOUNDARY_STRONG_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Aktenzeichen", re.compile(r"\b(?:aktenzeichen|az)\s*:", re.IGNORECASE),),
    ("Dokumenten-ID", re.compile(r"\b(?:dokument(?:en)?[-_\s]*id|doc[-_\s]*id)\s*:", re.IGNORECASE),),
    ("Betreff", re.compile(r"\bbetreff\s*:", re.IGNORECASE),),
    ("Absender", re.compile(r"\babsender\s*:", re.IGNORECASE),),
    ("Empfänger", re.compile(r"\bempf(?:a|ä)nger\s*:", re.IGNORECASE),),
    ("Vertragsparteien", re.compile(r"\bvertragsparteien\b", re.IGNORECASE),),
    ("zwischen", re.compile(r"\bzwischen\b", re.IGNORECASE),),
    ("Vergleich", re.compile(r"\bvergleich\b", re.IGNORECASE),),
    ("Ratenzahlungsvereinbarung", re.compile(r"\bratenzahlungsvereinbarung\b", re.IGNORECASE),),
    ("DOKUMENT START", re.compile(r"\bdokument\s*start\b", re.IGNORECASE),),
    ("Testament", re.compile(r"\btestament\b|\bletztwillige\s+verf[uü]gung\b", re.IGNORECASE),),
    ("Einspruch/OWi", re.compile(r"\beinspruch\b|\bb[uü][sß]geldbescheid\b", re.IGNORECASE),),
    ("Pflichtteilsberechnung", re.compile(r"\bpflichtteilsberechnung\b|\bnachlassverzeichnis\b", re.IGNORECASE),),
    ("Scheidungsfolgenvereinbarung", re.compile(r"\bscheidungsfolgenvereinbarung\b", re.IGNORECASE),),
    ("Ur-Nr", re.compile(r"\bur[-.\s]*nr\.?\s*\d+\b", re.IGNORECASE),),
)

_BOUNDARY_DOC_TITLE_KEYWORDS: tuple[str, ...] = (
    "mietvertrag",
    "wohnraummietvertrag",
    "arbeitsvertrag",
    "mahnung",
    "forderungsschreiben",
    "abnahmeprotokoll",
    "preisblatt",
    "sla",
    "auftragsverarbeitung",
    "anlage",
    "anlagenpaket",
    "vergleich",
    "ratenzahlungsvereinbarung",
    "rahmenvertrag",
    "avv",
)
_META_FIELD_HINT_RE = re.compile(
    r"(?im)\b(?:vertragsdatum|parteien|rechnungsdatum|fällig(?:keit)?|forderung|kund(?:e|in)nr|aktenzeichen|dokument(?:en)?[-_\s]*id)\s*:",
)
_INLINE_KEY_VALUE_RE = re.compile(r"\b[A-ZÄÖÜa-zäöüß][A-Za-zÄÖÜäöüß0-9/._\- ]{1,30}:\s*\S")
_STRONG_PRIORITY_MARKERS = {
    "DOKUMENT START",
    "Dokumenten-ID",
    "Doc-ID",
    "Betreff",
    "Absender",
    "Empfänger",
    "Vertragsparteien",
    "Ratenzahlungsvereinbarung",
}
_HARD_COOLDOWN_OVERRIDE_MARKERS = {
    "DOKUMENT START",
    "Dokumenten-ID",
    "Doc-ID",
}
_GENERIC_STRONG_MARKERS = {
    "Aktenzeichen",
    "zwischen",
    "Vereinbarung",
    "Vergleich",
}

# ---------------------------------------------------------------------------
# Attachment / contract continuation
# ---------------------------------------------------------------------------

_ATTACHMENT_PAGE_RE = re.compile(
    r"^\s*anlage\s+(?:[A-Z]|[0-9]+)\s*:", re.IGNORECASE
)
# Keywords that signal a contract-continuation page (payment details within the same Vertrag).
# These pages must NOT create a new document boundary when the preceding segment is a Vertrag.
_CONTRACT_CONTINUATION_RE = re.compile(
    r"\b(?:zahlungsdetails?|rechnungsempf[aä]nger|bankverbindung|zahlungsziel)\b",
    re.IGNORECASE,
)
# Doc types considered "Vertrag" for the same-contract heuristic
_VERTRAG_DOC_TYPES = {"Vertrag", "Ratenzahlungsvereinbarung", "Kaufvertrag", "Mietvertrag", "Arbeitsvertrag"}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _non_empty_lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _top_lines(lines: list[str], limit: int = 20) -> list[str]:
    return lines[:limit]


def _line_fingerprint(line: str) -> str:
    line = _normalize_ws(str(line or "")).lower()
    line = re.sub(r"\d+", "#", line)
    line = re.sub(r"[^a-z0-9äöüß# ]", "", line)
    return _normalize_ws(line)[:120]


def _alpha_upper_ratio(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    upper = sum(1 for ch in letters if ch.isupper())
    return upper / max(len(letters), 1)


def _page_metrics(lines: list[str], text: str) -> dict[str, float]:
    line_lengths = [len(line) for line in lines]
    return {
        "line_count": float(len(lines)),
        "avg_line_len": (sum(line_lengths) / len(line_lengths)) if line_lengths else 0.0,
        "uppercase_ratio": _alpha_upper_ratio(text),
    }


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

def _marker_score(top_text: str) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    for label, pattern, weight in _START_MARKERS:
        if pattern.search(top_text):
            score += weight
            reasons.append(f"Marker: {label}")
    return min(score, 4.5), reasons


def _looks_like_heading(line: str) -> bool:
    stripped = _normalize_ws(line)
    if not stripped:
        return False
    if re.match(r"^(?:§\s*\d+|[A-ZÄÖÜ][A-ZÄÖÜ0-9\- ]{5,}|[IVX]+\.|\d+\.)", stripped):
        return True
    if stripped.endswith(":") and len(stripped) <= 80:
        return True
    return _alpha_upper_ratio(stripped) >= 0.65 and len(stripped) >= 8


def _heading_score(top_lines: list[str]) -> tuple[float, list[str]]:
    if not top_lines:
        return 0.0, []
    score = 0.0
    reasons: list[str] = []
    first = top_lines[0]
    if _looks_like_heading(first):
        score += 1.0
        reasons.append("Heading in erster Zeile")
    heading_hits = sum(1 for line in top_lines[:6] if _looks_like_heading(line))
    if heading_hits >= 2:
        score += 0.35
        reasons.append("Mehrere Heading-Zeilen")
    return min(score, 1.4), reasons


def _key_value_lines(lines: list[str], limit: int = 14) -> int:
    return sum(1 for line in lines[:limit] if _KEY_VALUE_LINE_RE.match(line))


def _layout_start_score(top_lines: list[str]) -> tuple[float, list[str]]:
    if not top_lines:
        return 0.0, []

    score = 0.0
    reasons: list[str] = []
    first_line = _normalize_ws(top_lines[0])
    kv_count = _key_value_lines(top_lines, limit=14)

    if _looks_like_heading(first_line) and len(first_line.split()) <= 10 and len(first_line) <= 100:
        score += 0.55
        reasons.append("Kurze dominante Titelzeile")

    if kv_count >= 2 and (_looks_like_heading(first_line) or _SHORT_TITLE_LINE_RE.match(first_line)):
        score += 0.7
        reasons.append("Titel + Meta-Block")
    elif kv_count >= 4:
        score += 0.55
        reasons.append("Viele Key:Value-Zeilen am Seitenanfang")

    return min(score, 1.35), reasons


def _strong_boundary_hits(top_text: str) -> list[str]:
    hits: list[str] = []
    for label, pattern in _BOUNDARY_STRONG_MARKERS:
        if pattern.search(top_text):
            hits.append(label)
    return _dedupe_ordered(hits)


def _title_keyword_hit(top_text: str) -> str:
    lowered = top_text.lower()
    for keyword in _BOUNDARY_DOC_TITLE_KEYWORDS:
        if keyword in lowered:
            return keyword
    return ""


def _meta_block_indicator(top_lines: list[str], top_text: str) -> tuple[bool, str]:
    kv_lines = _key_value_lines(top_lines, limit=_META_TOP_LINES)
    kv_inline = len(_INLINE_KEY_VALUE_RE.findall(top_text))
    field_hint_count = len(_META_FIELD_HINT_RE.findall(top_text))
    kv_count = max(kv_lines, kv_inline)
    if kv_count >= 3:
        return True, f"meta:kv{kv_count}"
    if kv_count >= 2 and field_hint_count >= 1:
        return True, f"meta:kv{kv_count}+field"
    return False, ""


def _keyword_hits(text: str) -> dict[str, int]:
    lowered = text.lower()
    hits: dict[str, int] = {}
    for doc_type, keywords in _DOC_TYPE_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in lowered)
        if count > 0:
            hits[doc_type] = count
    return hits


def _keyword_score(top_text: str, full_text: str) -> tuple[float, str, list[str]]:
    top_hits = _keyword_hits(top_text)
    full_hits = _keyword_hits(full_text)
    merged_hits: dict[str, int] = {}
    for doc_type in set(top_hits) | set(full_hits):
        merged_hits[doc_type] = (top_hits.get(doc_type, 0) * 2) + full_hits.get(doc_type, 0)

    if not merged_hits:
        return 0.0, "Unklar", []

    best_doc_type, best_score = max(merged_hits.items(), key=lambda item: item[1])
    score = min(2.2, 0.4 * best_score)
    reasons = [f"DocType-Keyword: {best_doc_type}"]
    if top_hits.get(best_doc_type, 0) >= 1:
        reasons.append("DocType-Hinweis im Kopfbereich")
    return score, best_doc_type, reasons


def _layout_shift_score(
    prev_metrics: dict[str, float],
    curr_metrics: dict[str, float],
    prev_top: str,
    curr_top: str,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    prev_lines = max(prev_metrics.get("line_count", 0.0), 1.0)
    curr_lines = max(curr_metrics.get("line_count", 0.0), 1.0)
    line_delta = abs(curr_lines - prev_lines) / max(prev_lines, curr_lines)
    if line_delta >= 0.45:
        score += 0.35
        reasons.append("Layout-Wechsel: Zeilenanzahl")

    prev_avg = prev_metrics.get("avg_line_len", 0.0)
    curr_avg = curr_metrics.get("avg_line_len", 0.0)
    avg_delta = abs(curr_avg - prev_avg) / max(prev_avg, curr_avg, 1.0)
    if avg_delta >= 0.35:
        score += 0.25
        reasons.append("Layout-Wechsel: Zeilenlänge")

    upper_delta = abs(curr_metrics.get("uppercase_ratio", 0.0) - prev_metrics.get("uppercase_ratio", 0.0))
    if upper_delta >= 0.2:
        score += 0.25
        reasons.append("Layout-Wechsel: Großschreibung")

    if _line_fingerprint(prev_top) and _line_fingerprint(curr_top):
        if _line_fingerprint(prev_top) != _line_fingerprint(curr_top):
            score += 0.2
            reasons.append("Layout-Wechsel: Kopfzeile")

    return min(score, 1.2), reasons


def _header_footer_context(pages: list[dict[str, Any]]) -> tuple[set[str], set[str], list[str], list[str]]:
    top_fps: list[str] = []
    bottom_fps: list[str] = []
    for page in pages:
        lines = _non_empty_lines(page.get("text", ""))
        top = _line_fingerprint(lines[0]) if lines else ""
        bottom = _line_fingerprint(lines[-1]) if lines else ""
        top_fps.append(top)
        bottom_fps.append(bottom)

    top_counts = Counter(fp for fp in top_fps if fp)
    bottom_counts = Counter(fp for fp in bottom_fps if fp)
    recurring_top = {fp for fp, cnt in top_counts.items() if cnt >= 3}
    recurring_bottom = {fp for fp, cnt in bottom_counts.items() if cnt >= 3}
    return recurring_top, recurring_bottom, top_fps, bottom_fps


def _header_footer_shift_score(
    prev_top_fp: str,
    curr_top_fp: str,
    prev_bottom_fp: str,
    curr_bottom_fp: str,
    recurring_top: set[str],
    recurring_bottom: set[str],
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    if prev_top_fp in recurring_top and curr_top_fp in recurring_top and prev_top_fp != curr_top_fp:
        score += 0.25
        reasons.append("Kopfzeile wechselt")
    elif prev_top_fp in recurring_top and curr_top_fp and curr_top_fp not in recurring_top:
        score += 0.15
        reasons.append("Kopfzeile-Pattern endet")

    if prev_bottom_fp in recurring_bottom and curr_bottom_fp in recurring_bottom and prev_bottom_fp != curr_bottom_fp:
        score += 0.2
        reasons.append("Fußzeile wechselt")

    return min(score, 0.45), reasons


# ---------------------------------------------------------------------------
# Page classification
# ---------------------------------------------------------------------------

def is_attachment_page(text: str) -> bool:
    """Detects pages that introduce an attachment (e.g. 'Anlage A:', 'Anlage 1:').

    Attachments are part of the parent document and must not create their
    own separate document boundaries.
    """
    for line in _non_empty_lines(text)[:10]:
        if _ATTACHMENT_PAGE_RE.match(line):
            return True
    return False


def _title_guess(page_text: str, doc_type_guess: str, fallback_prefix: str = "Dokumentsegment") -> str:
    lines = _top_lines(_non_empty_lines(page_text), limit=12)
    for line in lines:
        if _looks_like_heading(line):
            candidate = _normalize_ws(line)
            if len(candidate) >= 6:
                return candidate[:100]
    if doc_type_guess and doc_type_guess != "Unklar":
        return doc_type_guess
    if lines:
        return _normalize_ws(lines[0])[:100]
    return fallback_prefix


# ---------------------------------------------------------------------------
# Helper function (kept local to avoid circular imports)
# ---------------------------------------------------------------------------

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
