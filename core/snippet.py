"""core/snippet.py – Snippet selection and text-matching helper functions.

Moved out of core/evidence.py (PR5).
No Streamlit import. No pipeline logic.
"""

import re
from datetime import date
from typing import Any

from core.models import _normalize_ws, _LEADING_ARTIFACT_RE, clean_snippet

# ---------------------------------------------------------------------------
# Trigger-phrase constants
# ---------------------------------------------------------------------------

_DEADLINE_TRIGGER_PHRASES = (
    "bis spätestens",
    "bis spaetestens",
    "spätestens",
    "spaetestens",
    "zahlungsfrist",
    "zahlungsziel",
    "frist",
    "fristablauf",
    "fällig bis",
    "faellig bis",
    "fällig",
    "faellig",
    "zu zahlen bis",
    "zahlung bis",
    "letzte zahlungsaufforderung",
    "bis",
    "zahlung",
)
_PAYMENT_DEADLINE_TRIGGER_PHRASES = (
    "zahlung",
    "zahlungsfrist",
    "zahlungsziel",
    "zahlungsaufforderung",
    "fällig",
    "faellig",
    "zu zahlen bis",
    "fällig bis",
    "faellig bis",
    "bis spätestens",
    "bis spaetestens",
    "spätestens",
    "spaetestens",
)
_GENERAL_TIME_SENTENCE_TRIGGERS = (
    "beginnt",
    "mietbeginn",
    "fällig",
    "faellig",
    "bis spätestens",
    "spätestens",
    "frist",
    "kündigung",
    "kuendigung",
    "zahlung",
    "probezeit",
)
_GENERIC_SENTENCE_HINTS = (
    "wohnzwecke",
    "standard",
    "allgemein",
    "beschreibung",
    "vertragsgegenstand",
)
_FIELD_SENTENCE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "fristen_termine": _DEADLINE_TRIGGER_PHRASES + ("kündigung", "kuendigung", "laufzeit"),
    "datum_beginn": ("beginn", "beginnt", "mietbeginn", "vertragsbeginn", "ab dem"),
    "kuendigung_fristen": ("kündigung", "kuendigung", "kündigungsfrist", "kuendigungsfrist"),
}
_TERMINATION_LABEL_TRIGGERS = (
    "kündig",
    "kuendig",
    "kündigungs",
    "kuendigungs",
    "kündigung",
    "kuendigung",
    "beend",
    "beendigung",
    "auflös",
    "aufloes",
    "vertragsende",
    "frist zum",
    "ende des",
    "verlänger",
    "verlaenger",
)
_EVIDENCE_BLACKLIST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)fiktives?\s+testdokument"),
    re.compile(r"(?i)kanzlei.testpaket"),
    re.compile(r"(?i)nur\s+zur\s+systemprüfung"),
    re.compile(r"(?i)keine\s+rechtsberatung"),
    re.compile(r"(?i)^seite\s+\d+\s*$"),
    re.compile(r"(?i)^(doc-id|az|aktenzeichen)\s*:"),
    re.compile(r"(?i)^(hinweis|note)\s*:"),
    re.compile(r"^\s*version\s+\d+\.\d+\s*$", re.IGNORECASE),
    # Extended blacklist (PR9 fix B)
    re.compile(r"(?im)^AZ\s*:", re.IGNORECASE),
    re.compile(r"(?im)^Aktenzeichen\s*:", re.IGNORECASE),
    re.compile(r"(?im)^DOC-ID\s*:", re.IGNORECASE),
    re.compile(r"^\s*Version\s+\d+\.\d+", re.IGNORECASE),
    # PR10 fix B': inline AZ patterns (not just line start) – string 1 root cause:
    # "AZ:" appears after leading text ("Aktenzeichen: AZ: LATE-..."), not at the start of the line,
    # so the ^AZ patterns don't match. New patterns match anywhere in the string.
    re.compile(r"(?i)\bAZ\s*:\s*[A-Z]{2,}[A-Z0-9]*[-/]"),  # "AZ: LATE-..." inline
    re.compile(r"(?i)\bDokumenten-?ID\s*:"),                 # "Dokumenten-ID:" inline
)

# Minimum length for a valid evidence source (stricter than 20 chars for standalone meta-lines)
_EVIDENCE_MIN_LENGTH = 25

# Pattern: text with EUR amounts or concrete numbers → higher snippet score
_EUR_AMOUNT_RE = re.compile(r"\b(?:EUR|€)\s*\d[\d.,]*|\b\d[\d.,]*\s*(?:EUR|€)")
_CONCRETE_NUMBER_RE = re.compile(r"\b\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?\b")

_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?;])\s+|\n+")

# FIX 2 (P1): only populate deadline fields with dates or deadline keywords.
# Patterns for DD.MM.YYYY and "DD. Monatname YYYY" date formats.
_DATE_IN_SENTENCE_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b")
_GERMAN_MONTH_DATE_RE = re.compile(
    r"\b\d{1,2}\.\s+"
    r"(?:januar|februar|m[äa]rz|april|mai|juni|juli|august|september|oktober|november|dezember)"
    r"\s+\d{4}\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_FRISTEN_SENTENCE_KEYWORDS_RE = re.compile(
    r"\b(?:frist|termin|fällig|faellig|bis\s+zum|spätestens|spaetestens|"
    r"innerhalb\s+von|ablauf|kündigung|kuendigung|laufzeit)\b",
    re.IGNORECASE,
)
# Fields for which the quote anchor only accepts sentences with a date or deadline keyword
_FRISTEN_FIELD_HINTS = frozenset({"fristen_termine", "kuendigung_fristen", "datum_beginn"})

_META_BLOCK_INDICATORS = (
    "aktenzeichen:",
    "az:",
    "doc-id:",
    "dokumenten-id:",
    "dokumenten_id:",
    "version ",
    "dokumenttyp:",
)

# ---------------------------------------------------------------------------
# Text normalization (placed here to avoid circular imports)
# ---------------------------------------------------------------------------

def _normalize_for_match(text: str) -> str:
    t = _normalize_ws(text).lower()
    t = t.replace("ß", "ss")
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ---------------------------------------------------------------------------
# Meta-block / trigger-phrase helpers
# ---------------------------------------------------------------------------

def _is_meta_block_context(text: str) -> bool:
    t = text.lower()
    return any(ind in t for ind in _META_BLOCK_INDICATORS)


def _contains_trigger_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    norm_text = _normalize_for_match(text)
    if not norm_text:
        return False
    return any(_normalize_for_match(phrase) in norm_text for phrase in phrases)


# ---------------------------------------------------------------------------
# Sentence segmentation and scoring
# ---------------------------------------------------------------------------

def _sentence_spans(text: str) -> list[tuple[int, int, str]]:
    source = str(text or "")
    spans: list[tuple[int, int, str]] = []
    start = 0
    for match in _SENTENCE_BOUNDARY_RE.finditer(source):
        end = match.start()
        sentence = _normalize_ws(source[start:end])
        if sentence:
            spans.append((start, end, sentence))
        start = match.end()
    if start < len(source):
        sentence = _normalize_ws(source[start:])
        if sentence:
            spans.append((start, len(source), sentence))
    if not spans and _normalize_ws(source):
        spans.append((0, len(source), _normalize_ws(source)))
    return spans


def _score_sentence_for_field(sentence: str, field_hint: str | None = None) -> int:
    score = 0
    sentence_l = str(sentence or "").lower()
    if _contains_trigger_phrase(sentence_l, _GENERAL_TIME_SENTENCE_TRIGGERS):
        score += 3
    field_key = str(field_hint or "").strip().lower()
    field_keywords = _FIELD_SENTENCE_KEYWORDS.get(field_key, ())
    if field_keywords and _contains_trigger_phrase(sentence_l, field_keywords):
        score += 2
    # Reward sentences containing EUR amounts or concrete numbers (content-dense)
    if _EUR_AMOUNT_RE.search(sentence):
        score += 2
    elif _CONCRETE_NUMBER_RE.search(sentence):
        score += 1
    words = re.findall(r"\S+", sentence_l)
    if len(words) <= 3 or any(hint in sentence_l for hint in _GENERIC_SENTENCE_HINTS):
        score -= 1
    # Penalise meta-block sentences (header-like structure)
    if _is_meta_block_context(sentence):
        score -= 4
    return score


def _sentence_has_date_or_frist(sentence: str) -> bool:
    """True if the sentence contains a recognizable date or deadline keyword."""
    return bool(
        _DATE_IN_SENTENCE_RE.search(sentence)
        or _GERMAN_MONTH_DATE_RE.search(sentence)
        or _ISO_DATE_RE.search(sentence)
        or _FRISTEN_SENTENCE_KEYWORDS_RE.search(sentence)
    )


def _select_best_sentence_snippet(
    text: str,
    needle: str,
    *,
    field_hint: str | None = None,
    fallback: str = "",
) -> str:
    needle_norm = _normalize_ws(str(needle or ""))
    if not needle_norm:
        return clean_snippet(fallback, max_chars=180)

    field_key = str(field_hint or "").strip().lower()
    is_fristen_field = field_key in _FRISTEN_FIELD_HINTS

    candidates: list[tuple[int, int, str]] = []
    for _, _, sentence in _sentence_spans(text):
        if needle_norm not in sentence:
            continue
        if _is_meta_block_context(sentence):
            continue
        if not is_valid_evidence_source(sentence):
            continue
        score = _score_sentence_for_field(sentence, field_hint=field_hint)
        candidates.append((score, len(sentence), sentence))

    if not candidates:
        # FIX 2 (P1): for deadline fields, only use the fallback if it itself
        # contains a date or deadline keyword. Otherwise return "", so that
        # no address block / header appears as a deadline quote.
        if is_fristen_field:
            fallback_clean = str(fallback or "")
            if fallback_clean and _sentence_has_date_or_frist(fallback_clean):
                return clean_snippet(fallback_clean, max_chars=180)
            return ""
        return clean_snippet(fallback or needle_norm, max_chars=180)

    # FIX 2 (P1): for deadline fields, only accept sentences that contain a
    # recognizable date or deadline keyword. Address blocks, power-of-attorney
    # headers, and other content-mismatched sentences are filtered out.
    if is_fristen_field:
        filtered = [c for c in candidates if _sentence_has_date_or_frist(c[2])]
        if filtered:
            candidates = filtered
        else:
            return ""  # No valid deadline sentence found

    # Prefer higher score; break ties by shorter length (content-dense sentences
    # over long headers that merely contain the needle).
    best_sentence = max(candidates, key=lambda item: (item[0], -item[1]))[2]
    return clean_snippet(best_sentence, max_chars=180)


# ---------------------------------------------------------------------------
# Deadline / label helpers
# ---------------------------------------------------------------------------

def _deadline_label_from_context(text: str) -> str:
    if _contains_trigger_phrase(text, _PAYMENT_DEADLINE_TRIGGER_PHRASES):
        return "Zahlungsfrist"
    return "Frist/Termin"


def _has_termination_label_trigger(text: str) -> bool:
    return _contains_trigger_phrase(text, _TERMINATION_LABEL_TRIGGERS)


_GERMAN_MONTH_NAMES: dict[str, int] = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "dezember": 12,
}


def _normalize_date_to_iso(value: str) -> str | None:
    # Numeric format: DD.MM.YYYY or DD.MM.YY
    match = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b", str(value or ""))
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year_raw = match.group(3)
        year = int(year_raw)
        if len(year_raw) == 2:
            year += 2000 if year < 70 else 1900
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    # Long format: "31. Januar 2025"
    long_match = re.search(
        r"\b(\d{1,2})\.\s+"
        r"(januar|februar|m[äa]rz|april|mai|juni|juli|august|september|oktober|november|dezember)"
        r"\s+(\d{4})\b",
        str(value or ""),
        re.IGNORECASE,
    )
    if long_match:
        day = int(long_match.group(1))
        month = _GERMAN_MONTH_NAMES.get(long_match.group(2).lower())
        if month is None:
            return None
        year = int(long_match.group(3))
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Quote / snippet helpers
# ---------------------------------------------------------------------------

def _clip_quote_200(text: str) -> str:
    return clean_snippet(text, max_chars=180)


def _window_quote(text: str, start: int, end: int, left: int, right: int) -> str:
    if not text:
        return ""
    raw_start = max(0, start - left)
    raw_end = min(len(text), end + right)

    sentence_boundary = max(
        text.rfind(". ", 0, raw_start),
        text.rfind("! ", 0, raw_start),
        text.rfind("? ", 0, raw_start),
        text.rfind("\n", 0, raw_start),
    )
    if sentence_boundary != -1:
        raw_start = sentence_boundary + (1 if text[sentence_boundary] == "\n" else 2)
    else:
        word_boundary = text.rfind(" ", 0, raw_start)
        if word_boundary != -1:
            raw_start = word_boundary + 1

    for token in (". ", "! ", "? ", "\n"):
        idx = text.find(token, raw_end)
        if idx != -1:
            raw_end = min(raw_end, idx + (1 if token == "\n" else 1))

    return _clip_quote_200(text[raw_start:raw_end])


def _to_short_citation_quote(text: str, max_words: int = 20) -> str:
    words = re.findall(r"\S+", clean_snippet(text, max_chars=180))
    return " ".join(words[:max_words])


def _is_sentence_like_snippet(text: str) -> bool:
    snippet = clean_snippet(text, max_chars=180)
    if len(re.findall(r"\S+", snippet)) < 5:
        return False
    starts_clean = bool(re.match(r"^[A-ZÄÖÜ]", snippet))
    has_terminal_punct = bool(re.search(r"[.!?]\s*$", snippet))
    return starts_clean and has_terminal_punct


def _snippet_quality_score(item: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    quote = clean_snippet(str(item.get("quote", "") or ""), max_chars=180)
    value = _normalize_ws(str(item.get("value", "") or ""))
    field = str(item.get("field", "") or "").strip().lower()
    # Valid evidence sources always outrank invalid ones (metadata headers, test markers).
    # Root cause PR10: flat_text window-quotes could include header text; when the
    # per-line loop produced a short but valid quote, the header quote won via len(quote).
    is_valid = 1 if is_valid_evidence_source(quote) else 0
    # Zahllast items (total rent due / monthly rent) are semantically high-value even when
    # their quote gets shortened by repeated clean_snippet calls (abbreviations like "zzgl.",
    # "i.H.v." trigger the sentence-start jump). The is_zahllast flag bridges this scoring
    # disadvantage and ensures the Zahllast amount ranks above generic amounts with a
    # longer but less content-relevant quote.
    sentence_like = 1 if (
        _is_sentence_like_snippet(quote) or bool(item.get("is_zahllast"))
    ) else 0
    has_artifact = 1 if _LEADING_ARTIFACT_RE.match(quote or "") else 0
    has_time_context = 1 if (
        field in {"fristen_termine", "datum_beginn", "kuendigung_fristen"}
        and _contains_trigger_phrase(f"{quote} {value}", _GENERAL_TIME_SENTENCE_TRIGGERS + _DEADLINE_TRIGGER_PHRASES)
    ) else 0
    return (
        is_valid,
        has_time_context,
        sentence_like,
        1 - has_artifact,
        len(quote),
        len(value),
    )


def is_valid_evidence_source(text: str) -> bool:
    """Checks whether a text qualifies as an evidence source (not just metadata/header).

    Returns False for headers, test-document markers, very short lines,
    and other non-substantive text fragments.
    """
    text_stripped = str(text or "").strip()
    if len(text_stripped) < _EVIDENCE_MIN_LENGTH:
        return False
    for pattern in _EVIDENCE_BLACKLIST_PATTERNS:
        if pattern.search(text_stripped):
            return False
    return True
