"""
core/evidence_map.py – Evidence map construction & normalization
Extracted from core/evidence.py (W2 – evidence split).
"""

import re
from collections import Counter, defaultdict
from datetime import date as _date
from math import ceil
from typing import Any

from core.config import get_config
from core.models import (
    _normalize_ws,
    _LEADING_ARTIFACT_RE,
    clean_snippet,
)
from core.snippet import (
    _normalize_for_match,
    _select_best_sentence_snippet,
    _clip_quote_200,
    _window_quote,
    _is_meta_block_context,
    _contains_trigger_phrase,
    _normalize_date_to_iso,
    _has_termination_label_trigger,
    _snippet_quality_score,
    _deadline_label_from_context,
    _DEADLINE_TRIGGER_PHRASES,
    _FIELD_SENTENCE_KEYWORDS,
    _GERMAN_MONTH_DATE_RE,
)
from core.labels import (
    klassifiziere_betrag_mit_confidence,
    klassifiziere_betrag_mit_kontext,
    extract_context_lines,
)
from core.party_extractor import (
    _looks_like_person,
    _looks_like_org,
    _is_artifact_value,
    _is_rhetoric_value,
    _extract_party_candidates,
    _dedupe_str_keep_order,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_BETRAG_CONFIDENCE = 1.0  # only emit amounts on unambiguous classification

_REPEATING_EDGE_PAGE_RE = re.compile(
    r"\b(seite|page)\s*\d+(?:\s*(?:/|von|of)\s*\d+)?\b",
    flags=re.IGNORECASE,
)
_REPEATING_EDGE_RATIO_RE = re.compile(r"\b\d+\s*/\s*\d+\b")
_REPEATING_EDGE_DATE_RE = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b")
_REPEATING_EDGE_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")

_REQUIRED_EVIDENCE_FIELDS = (
    "parteien",
    "betraege",
    "fristen_termine",
    "datum_beginn",
    "kuendigung_fristen",
    "urlaub_ueberstunden",
    "wettbewerbsverbot",
    "kaution",
    "miete",
    "nebenkosten",
    "rueckstand",
    "schadensersatz",
    "aktenzeichen",
    "gericht",
    "streitwert",
    "beweismittel",
)
_EVIDENCE_TOPK_BY_FIELD: dict[str, int] = {
    "parteien": 2,
    "betraege": 2,
    "fristen_termine": 2,
    "datum_beginn": 1,
    "kuendigung_fristen": 2,
    "urlaub_ueberstunden": 1,
    "wettbewerbsverbot": 1,
    "kaution": 1,
    "miete": 1,
    "nebenkosten": 1,
    "rueckstand": 2,
    "schadensersatz": 1,
    "aktenzeichen": 1,
    "gericht": 1,
    "streitwert": 1,
    "beweismittel": 5,
}

_DEADLINE_DURATION_RE = re.compile(
    # PR12: also capture inflected forms (Monaten, Tagen, Jahren, Wochen)
    r"\b\d+\s*(?:monate?n?|wochen?|tage?n?|jahre?n?)\b",
    flags=re.IGNORECASE,
)
# Case-reference ("Aktenzeichen") markers: "Az.", "AZ:", "Aktenzeichen:",
# "Geschäftszeichen:", "Unser Zeichen:" followed by the actual reference
# value (alphanumeric, up to 80 characters)
_AZ_LINE_RE = re.compile(
    r"\b(?:az\.?:?|aktenzeichen\s*:?|gesch[äa]ftszeichen\s*:?|unser\s+zeichen\s*:?)\s*"
    r"([^\n,;(]{3,80})",
    re.IGNORECASE,
)


# FIX 3: German stopwords that are NOT case-reference components, but have
# <=3 letters with uppercase characters (e.g. "Der", "Die", "Das", "Ihr", "Bis").
_AZ_STOP_WORDS = frozenset({
    "der", "die", "das", "den", "dem", "des",
    "ein", "eine", "einer", "eines", "einem", "einen",
    "ihr", "bis", "vom", "zum", "zur", "von", "und",
    "mit", "für", "auf", "aus", "bei", "nach", "über",
    "mein", "sein", "kein", "nur", "auch", "noch", "oder",
})


def _clean_az_value(raw: str) -> str:
    """FIX 2: Truncate AZ to just the case-reference tokens, strip trailing freetext.

    Valid AZ components: tokens with digits/slashes/dots/dashes, or 1-3 ALL-UPPERCASE
    abbreviations (like "F", "VI", "OLG"). Stop at the first token that looks like a
    regular German word (3+ chars with lowercase letters).
    FIX 3: Also stop at German stop words (Der, Die, Das, Ihr, Bis, etc.) that
    coincidentally have uppercase letters but are not AZ components.
    """
    if not raw:
        return raw
    tokens = raw.split()
    az_tokens: list[str] = []
    for tok in tokens:
        # Strip trailing punctuation for classification only
        tok_clean = tok.rstrip(".,;:!?")
        # FIX 3: Skip common German words that are not case-reference components
        if tok_clean.lower() in _AZ_STOP_WORDS:
            break
        is_az_component = (
            bool(re.search(r"[\d/.\-]", tok_clean))          # contains digit/slash/dot/dash
            or (len(tok_clean) <= 3 and any(c.isupper() for c in tok_clean))  # 1-3 char abbreviation with uppercase
        )
        if is_az_component:
            az_tokens.append(tok)
        else:
            break  # first non-AZ token → stop
    if not az_tokens:
        # Fallback: hard truncate at 40 chars
        return raw[:40].rstrip()
    result = " ".join(az_tokens).rstrip(".,;:!?").strip()
    if len(result) > 40:
        result = result[:40].rstrip()
    return result
# Court ("Gericht") detection: court type (explicitly case-insensitive in the
# pattern) + place name requiring uppercase. No re.IGNORECASE, so that
# "olg wurde" does not match.
_GERICHT_RE = re.compile(
    r"\b((?:[Aa]mtsgericht|[Ll]andgericht|[Oo]berlandesgericht|OLG|[Bb]undesgerichtshof|BGH"
    r"|[Aa]rbeitsgericht|[Ss]ozialgericht|[Ff]inanzgericht|[Vv]erwaltungsgericht)"
    r"(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+(?:\s+(?:am|an\s+der?)\s+[A-ZÄÖÜ][a-zäöüß]+)?)?)"
)
# PR12 P1: word-boundary-aware "Kündigung" (termination) pattern — does NOT
# match 'vorankündigung' (advance notice)
_KUENDIGUNG_WORD_RE = re.compile(
    r"\b(?:kündigung|kuendigung)(?:sfrist)?\b",
    re.IGNORECASE,
)
# PR13 P4: year-number detection — excludes "2026 EUR" from being read as a date token
_YEAR_AMOUNT_RE = re.compile(r"^(?:19|20)\d{2}$")
# FIX 4: strong deadline ("Frist") triggers — dates without these keywords are not deadlines
_STRONG_FRIST_TRIGGERS = (
    "frist", "fristablauf", "spätestens", "spaetestens",
    "fällig bis", "faellig bis", "bis spätestens", "bis spaetestens",
    "zahlungsfrist", "zahlungsziel", "zu zahlen bis", "zu zahlen",
    "zahlung bis", "zahlung am", "zahlung",
    "letzte zahlungsaufforderung", "termin", "fällig am", "faellig am",
    "ablauf", "lief ab", "läuft ab", "verstrichen",
)
# FIX 2/6: negative context — these keywords signal birth dates / start-of-employment
# dates / founding dates. Such dates are NOT deadlines, even if "termin" or similar
# appears in the surrounding window.
_NON_FRIST_CONTEXT_KEYWORDS = (
    "geboren", "geb.", "geburtsdatum", "geburtstag",
    "* ",  # genealogisches Asterisk-Zeichen vor Datum
    "beschäftigt seit", "beschaeftigt seit", "eingestellt am",
    "eintritt", "beschäftigungsbeginn", "beschaeftigungsbeginn",
    "tätig seit", "taetig seit", "arbeitsverhältnis seit", "arbeitsverhaeltnis seit",
    "angestellt seit", "arbeitsbeginn", "seit dem",  # FIX 3: zusätzliche Beschäftigungsbeginn-Keywords
    "in kraft seit",  # FIX 3: contract-start keyword
    "gegründet", "gegruendet", "gründung", "gruendung",
    "eingetragen", "eintragung", "registriert",
    # FIX 1/2 new: employment-contract and start-of-employment signatures
    "arbeitsvertrag vom", "beginn des arbeitsverhältnisses", "beginn des arbeitsverhaeltnisses",
    "eingetreten am", "trat ein am",
)
# FIX 2: "Vertrag vom" (contract dated) only filters dates before 2024; more recent
# contracts may contain relevant deadlines.
_VERTRAG_VOM_RE = re.compile(r"\bvertrag\s+vom\b", re.IGNORECASE)
_PAST_DATE_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


_FULL_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.((?:19|20)\d{2})\b")


def _is_past_date_no_strong_frist_context(date_value: str, context: str) -> bool:
    """FIX 4 / FIX 2 / FIX 6: Returns True if the date should be filtered (not a deadline).

    Filtering rules (checked in order):
    1. Non-deadline context keywords (birth, employment, founding) → always filter.
    2. Dates before 1960 → always filter.
    3. Past date without a strong deadline trigger keyword → filter.
    Historically expired deadlines ("Frist lief ab am ...") are preserved because they
    contain strong keywords like "frist" or "lief ab".
    """
    # Try to parse full date (dd.mm.yyyy) for accurate past/future check
    full_match = _FULL_DATE_RE.search(date_value)
    if full_match:
        try:
            day, month, year = int(full_match.group(1)), int(full_match.group(2)), int(full_match.group(3))
            is_past = _date(year, month, day) < _date.today()
        except ValueError:
            is_past = False
    else:
        year_match = _PAST_DATE_YEAR_RE.search(date_value)
        if not year_match:
            return False
        year = int(year_match.group(1))
        is_past = year < _date.today().year

    if not is_past:
        return False  # future/today dates are always kept
    ctx = context.lower()
    # Extract year_val early (needed for year-based checks below)
    if full_match:
        year_val = int(full_match.group(3))
    else:
        year_match2 = _PAST_DATE_YEAR_RE.search(date_value)
        year_val = int(year_match2.group(1)) if year_match2 else 0
    # FIX 2/6: negative context check — birth/employment/founding dates are NEVER
    # deadlines, regardless of whether "termin" or other soft triggers appear in
    # the same window.
    for neg_kw in _NON_FRIST_CONTEXT_KEYWORDS:
        if neg_kw in ctx:
            return True  # always filter — not a deadline
    # Dates before 1960 are never deadlines (birth years, founding years, etc.)
    if year_val > 0 and year_val < 1960:
        return True  # always filter pre-1960 dates regardless of context
    for trigger in _STRONG_FRIST_TRIGGERS:
        if trigger in ctx:
            return False  # has strong context → keep as a deadline
    # FIX 2: "Vertrag vom" (contract dated) — filter old contracts (pre-2024);
    # recent ones (2024+) may be relevant.
    # This check runs after strong triggers so "Frist aus dem Vertrag vom..." is kept.
    if _VERTRAG_VOM_RE.search(ctx) and year_val > 0:
        if year_val < 2024:
            return True   # old contract date without explicit deadline context → filter
        return False      # recent contract date → keep
    return True  # past date without strong deadline context → filter
# U10: total-payment-obligation ("Gesamtzahllast") detection
# Pattern 1 (left part): "betraegt/beträgt" directly before the amount
_ZAHLLAST_LEFT_RE = re.compile(r"betr[äa]g[t]\s*$", re.IGNORECASE)
# Pattern 1 (right part): "fällig jeweils" qualifies as a recurring total payment
_ZAHLLAST_RIGHT_RE = re.compile(r"\bf[äa]llig\s+jeweils\b", re.IGNORECASE)

# Split into two tiers by how unambiguous the legal-advice signal is.
#
# _ALWAYS_ON_REPLACEMENTS: multi-word / unambiguous legal-advice markers. These
# do not collide with ordinary restatement of document content ("die Klausel
# ist rechtlich problematisch" is always an assessment, never a quote of what
# the document itself says), so they are safe to apply everywhere, including
# restatement fields.
#
# _ASSESSMENT_ONLY_REPLACEMENTS: single ordinary German words/short phrases
# ("muss", "wirksam", "anspruch", ...) that show up constantly in ordinary
# restatement of document content (e.g. a citation-adjacent claim that quotes
# "... MUSS dem Vermieter ... zugehen"). Applying these outside assessment
# fields silently falsifies what the document says, so they are restricted to
# fields where the model is producing its own evaluative judgment.
#
# Ordering matters within each tier: multi-word specific patterns must run
# before any single-word pattern that could match a substring of them (e.g.
# "rechtlich unzulässig" before a bare "unzulässig").
_ALWAYS_ON_REPLACEMENTS: list[tuple[str, str]] = [
    (r"\brechtlich problematisch\b", "Hinweis (zu prüfen)"),
    (r"\brechtlich korrekt\b", "zu prüfen"),
    (r"\brechtlich zulässig\b", "zu prüfen"),
    (r"\brechtlich unzulässig\b", "zu prüfen"),
    (r"\bgesetzlich\s+erlaubt\b", "zu prüfen"),
    (r"\bgesetzlich\s+verboten\b", "zu prüfen"),
    (r"\berfolgsaussicht(?:en)?\b", "offen / unklar / Rückfrage"),
    (r"\bbelegen\s+k[oö]nnen\b", "im Dokument genannt / Beweismittel genannt"),
    (r"\brechtsfolge(?:n)?\b", "Hinweis"),
]
_ASSESSMENT_ONLY_REPLACEMENTS: list[tuple[str, str]] = [
    (r"\bzulässig\b", "zu prüfen"),
    (r"\bunzulässig\b", "zu prüfen"),
    (r"\bwirksam\b", "zu prüfen"),
    (r"\bunwirksam\b", "zu prüfen"),
    (r"\brechtswidrig\b", "zu prüfen"),
    (r"\brechtm[aä][sß]{1,2}ig\b", "zu prüfen"),
    (r"\banspruch\b", "Angabe"),
    (r"\bverjährt\b", "Fristlage zu prüfen"),
    (r"\bmuss\b", "zu prüfen"),
    (r"\bdarf\s+nicht\b", "zu prüfen"),
    (r"\bnur\s+wenn\b", "unter Bedingung (zu prüfen)"),
    (r"\bist\s+verpflichtet\b", "wird im Dokument erwähnt (zu prüfen)"),
    (r"\baussichten?\b", "offen / unklar / Rückfrage"),
    (r"\bschl[üu]ssig\b", "zu prüfen / Abgleich erforderlich"),
    (r"\bausreichend\b", "zu prüfen / Abgleich erforderlich"),
    (r"\berschweren\b", "offen / unklar / Rückfrage"),
    (r"\bdurchsetzbar\b", "offen / unklar / Rückfrage"),
]
# Backward-compat alias: some callers/tests may still reference the combined
# list (e.g. to check "is this pattern known at all"). Assessment-only rules
# are listed after always-on rules, preserving the original ordering.
_FORBIDDEN_REPLACEMENTS: list[tuple[str, str]] = (
    _ALWAYS_ON_REPLACEMENTS + _ASSESSMENT_ONLY_REPLACEMENTS
)

# Top-level schema fields where the model restates document content (facts,
# dates, parties, amounts, key statements) rather than producing its own
# evaluative judgment. These fields carry the SummaryPayloadModel/
# AktenvermerkPayloadModel/SegmentSummaryPayloadModel/ChunkPayloadModel/
# DocumentAnalysisPayloadModel restatement fields per core/prompts.py.
_RESTATEMENT_FIELDS: frozenset[str] = frozenset(
    {
        "kernaussagen",
        "fristen_termine",
        "dokumenttyp_parteien",
        "eckdaten",
        "kurzueberblick",
        # ChunkPayloadModel
        "key_points",
        "dates",
        # DocumentAnalysisPayloadModel
        "facts",
        "parties",
        "amounts",
        "key_facts",
        "deadlines",
        "summary_neutral",
        "metadata",
        "document_type",
    }
)

# Top-level schema fields where the model produces its own assessment/
# flagging (auffaelligkeiten, contradictions, missing-info questions, to-dos).
# These are the fields apply_no_legal_advice_guard() already targets via its
# own smaller _LEGAL_GUARD_REPLACEMENTS list; _neutralize_legal_tone follows
# the same seam for the broader ordinary-German rule set.
_ASSESSMENT_FIELDS: frozenset[str] = frozenset(
    {
        "auffaelligkeiten",
        "widersprueche",
        "fehlende_angaben",
        "todos",
        "prueffragen",
        "missing_info",
        "top3_findings",
        "top3_questions",
        "issues",
        "questions",
        "uncertainties",
    }
)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _normalize_repeating_edge_line(line: str) -> str:
    normalized = _normalize_ws(str(line or "")).lower()
    if not normalized:
        return ""
    normalized = _REPEATING_EDGE_PAGE_RE.sub(
        lambda m: re.sub(r"\d+", "<#>", m.group(0).lower()),
        normalized,
    )
    normalized = _REPEATING_EDGE_RATIO_RE.sub("<#>/<#>", normalized)
    normalized = _REPEATING_EDGE_DATE_RE.sub("<date>", normalized)
    normalized = _REPEATING_EDGE_TIME_RE.sub("<time>", normalized)
    return _normalize_ws(normalized)


def strip_repeating_header_footer_lines(
    pages: list[dict[str, Any]],
    *,
    edge_lines: int = 4,
    min_pages: int = 3,
    min_ratio: float = 0.5,
) -> list[dict[str, Any]]:
    copied_pages = [dict(page) for page in list(pages or [])]
    if len(copied_pages) <= 1:
        return copied_pages

    edge_count = max(1, int(edge_lines))
    page_lines: list[list[str]] = []
    edge_line_indexes_per_page: list[set[int]] = []
    edge_norms_per_page: list[set[str]] = []

    for page in copied_pages:
        lines = str(page.get("text", "") or "").splitlines()
        page_lines.append(lines)

        indexed_non_empty = [(idx, line.strip()) for idx, line in enumerate(lines) if line.strip()]
        if len(indexed_non_empty) <= edge_count * 2:
            top_candidates = indexed_non_empty[:1]
            bottom_candidates = indexed_non_empty[-1:]
        else:
            top_candidates = indexed_non_empty[:edge_count]
            bottom_candidates = indexed_non_empty[-edge_count:]
        edge_candidates = top_candidates + bottom_candidates
        edge_line_indexes = {idx for idx, _ in edge_candidates}
        edge_line_indexes_per_page.append(edge_line_indexes)

        edge_norms: set[str] = set()
        for _, candidate in edge_candidates:
            norm = _normalize_repeating_edge_line(candidate)
            if norm:
                edge_norms.add(norm)
        edge_norms_per_page.append(edge_norms)

    threshold = max(int(min_pages), int(ceil(len(copied_pages) * float(min_ratio))))
    recurring_norms = {
        norm
        for norm, count in Counter(
            norm
            for norms in edge_norms_per_page
            for norm in norms
        ).items()
        if count >= threshold
    }
    if not recurring_norms:
        return copied_pages

    cleaned_pages: list[dict[str, Any]] = []
    for idx, page in enumerate(copied_pages):
        lines = page_lines[idx]
        edge_indexes = edge_line_indexes_per_page[idx]
        kept_lines: list[str] = []
        for line_idx, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            if (
                stripped
                and line_idx in edge_indexes
                and _normalize_repeating_edge_line(stripped) in recurring_norms
            ):
                continue
            kept_lines.append(raw_line)
        cleaned_page = dict(page)
        cleaned_page["text"] = "\n".join(kept_lines).strip()
        cleaned_pages.append(cleaned_page)
    return cleaned_pages


def _searchable_pages_without_repeating_edges(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cfg = get_config()
    edge_lines = cfg.repeat_edge_lines
    min_pages = cfg.repeat_edge_min_pages
    min_ratio = cfg.repeat_edge_min_ratio
    return strip_repeating_header_footer_lines(
        pages,
        edge_lines=edge_lines,
        min_pages=min_pages,
        min_ratio=min_ratio,
    )


def _is_year_like_amount(value: str) -> bool:
    """True if the amount value matches a year number (1900-2099) with no decimal places."""
    stripped = re.sub(r"\s*(?:eur(?:o)?|€)\s*", "", value, flags=re.IGNORECASE).strip()
    return bool(_YEAR_AMOUNT_RE.match(stripped))


def _neutralize_legal_tone(
    value: Any,
    *,
    key: str | None = None,
    root_field: str | None = None,
) -> Any:
    """Neutralizes legal-advice-style phrasing, but only where it is safe to do so.

    `root_field` carries the TOP-LEVEL schema field name (e.g. "kernaussagen",
    "auffaelligkeiten") down through the recursive walk over nested lists/dicts,
    so the rule selection can depend on whether the top-level field restates
    document content or holds the model's own assessment. `key` remains the
    immediate parent key, used only for the pre-existing quote exemption.

    Rule selection:
    - quote_snippet/quote values: never touched (exempt everywhere), as before.
    - assessment fields (see _ASSESSMENT_FIELDS): both always-on and
      assessment-only replacements apply.
    - unrecognized/unlisted top-level fields: conservative default, treated as
      restatement (always-on rules only). Falsifying reported document content
      is a worse failure mode than leaving a slightly un-neutralized tone in an
      edge-case field, and the prompt design plus apply_no_legal_advice_guard()
      already cover the assessment path for known fields.
    - restatement fields (see _RESTATEMENT_FIELDS) and everything else not in
      _ASSESSMENT_FIELDS: always-on replacements only.
    """
    if isinstance(value, str):
        if key in {"quote_snippet", "quote"}:
            return clean_snippet(value, max_chars=180)
        normalized = value
        if root_field in _ASSESSMENT_FIELDS:
            rules = _ALWAYS_ON_REPLACEMENTS + _ASSESSMENT_ONLY_REPLACEMENTS
        else:
            # Restatement fields AND unknown/unlisted fields default to the
            # conservative, always-on-only side.
            rules = _ALWAYS_ON_REPLACEMENTS
        for pattern, repl in rules:
            normalized = re.sub(pattern, repl, normalized, flags=re.IGNORECASE)
        return _normalize_ws(normalized)
    if isinstance(value, list):
        return [_neutralize_legal_tone(v, root_field=root_field) for v in value]
    if isinstance(value, dict):
        return {
            k: _neutralize_legal_tone(
                v,
                key=k,
                root_field=k if root_field is None else root_field,
            )
            for k, v in value.items()
        }
    return value


def _get_page_no(page: dict[str, Any]) -> int:
    return int(page.get("page_number") or page.get("page") or 0)


def _dedupe_keep_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        norm = _normalize_for_match(item)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(item.strip())
    return out


def _normalize_evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    field = str(item.get("field", "")).strip().lower()
    value = _normalize_ws(str(item.get("value", "") or ""))[:120]
    quote = clean_snippet(str(item.get("quote", "") or ""), max_chars=180)
    if field == "kuendigung_fristen" and not _has_termination_label_trigger(f"{value} {quote}"):
        field = "fristen_termine"
    normalized["field"] = field
    normalized["value"] = value
    page = item.get("page")
    normalized["page"] = int(page) if page is not None else None
    normalized["quote"] = quote
    label = _normalize_ws(str(item.get("label", "") or ""))
    if label:
        normalized["label"] = label[:40]
    date_iso = _normalize_date_to_iso(normalized.get("value", ""))
    if date_iso:
        normalized["date_iso"] = date_iso
    return normalized


def _dedupe_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_key: dict[tuple[str, int, str], dict[str, Any]] = {}
    for item in items:
        normalized = _normalize_evidence_item(item)
        field = str(normalized.get("field", "")).strip().lower()
        value = str(normalized.get("value", "")).strip()
        if not field or not value or value == "missing":
            continue
        page = int(normalized.get("page") or 0)
        key = (field, page, _normalize_for_match(value))
        existing = best_by_key.get(key)
        if existing is None or _snippet_quality_score(normalized) > _snippet_quality_score(existing):
            best_by_key[key] = normalized
    return list(best_by_key.values())


def _select_top_evidence(items: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    max_items = _EVIDENCE_TOPK_BY_FIELD.get(field, 2)
    ordered = sorted(
        (_normalize_evidence_item(item) for item in items),
        key=lambda x: (_snippet_quality_score(x), -(x.get("page") or 0)),
        reverse=True,
    )
    return ordered[:max_items]


def _normalize_evidence_map(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = _dedupe_evidence(items)
    by_field: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in deduped:
        by_field[str(item.get("field", ""))].append(item)

    normalized: list[dict[str, Any]] = []
    for field in _REQUIRED_EVIDENCE_FIELDS:
        selected = _select_top_evidence(by_field.get(field, []), field)
        if selected:
            normalized.extend(selected)
            continue
        normalized.append({"field": field, "value": "missing", "page": None, "quote": "missing"})
    return normalized


def build_evidence_map(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Produces an evidence map per segment:
    {"field", "value", "page", "quote"}.
    """
    evidence: list[dict[str, Any]] = []
    searchable_pages = _searchable_pages_without_repeating_edges(pages)
    if not searchable_pages:
        return evidence

    amount_re = re.compile(
        r"(?:€|eur(?:o)?)\s*\d{1,3}(?:[.]\d{3})*(?:,\d{2})?"  # EUR/€ prefix (e.g. EUR 1.090)
        r"|\d{1,3}(?:[.]\d{3})+(?:,\d{2})?\s*(?:€|eur(?:o)?)"  # thousands-separator amount (e.g. 1.090 €)
        r"|\d{1,4}(?:,\d{2})?\s*(?:€|eur(?:o)?)",               # short amount up to 4 digits (e.g. 240 €)
        re.IGNORECASE,
    )
    date_re = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b")

    for page in searchable_pages:
        page_no = _get_page_no(page)
        page_text = str(page.get("text", "") or "")
        if not page_text.strip():
            continue

        flat_text = _normalize_ws(page_text)
        if flat_text:
            # Extract role + name from tokenized PDFs (e.g. "Vermieter Thomas Bergmann").
            for role in ("Vermieter", "Vermieterin", "Mieter", "Mieterin", "Arbeitgeber", "Arbeitnehmer", "Käufer", "Verkäufer"):
                person_pattern = re.compile(
                    rf"{role}\s*:?\s*([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+)"
                )
                for match in person_pattern.finditer(flat_text):
                    party = _normalize_ws(match.group(1))
                    if not (_looks_like_person(party) or _looks_like_org(party)):
                        continue
                    if _is_artifact_value(party):
                        continue
                    quote = _window_quote(flat_text, match.start(), match.end(), left=40, right=80)
                    evidence.append(
                        {
                            "field": "parteien",
                            "value": party[:120],
                            "page": page_no,
                            "quote": _clip_quote_200(quote),
                        }
                    )

            for amount_match in amount_re.finditer(flat_text):
                amount_value = _normalize_ws(amount_match.group(0))
                if _is_year_like_amount(amount_value):  # PR13 P4: exclude year tokens
                    continue
                quote = _window_quote(flat_text, amount_match.start(), amount_match.end(), left=50, right=70)
                # Amount-to-field mapping: two-stage — (1) clean_snippet prevents keyword
                # bleed from the preceding sentence, (2) fall back to the raw window if no
                # keyword is found (catches keywords like "Grundmiete" that clean_snippet
                # would otherwise cut off, U10 fix).
                _fs, _fe = amount_match.start(), amount_match.end()
                _raw_window = flat_text[max(0, _fs - 50) : min(len(flat_text), _fe + 50)]
                kontext_fenster = _clip_quote_200(_raw_window)
                klassifikation = klassifiziere_betrag_mit_confidence(kontext_fenster)
                if klassifikation.confidence < MIN_BETRAG_CONFIDENCE and not klassifikation.is_ambiguous:
                    # No match with the clipped window → try the raw window again.
                    # Only accept unambiguous matches; ignore ambiguity in the raw
                    # window (that would be bleed from a neighboring sentence).
                    raw_klass = klassifiziere_betrag_mit_confidence(_normalize_ws(_raw_window))
                    if raw_klass.confidence >= MIN_BETRAG_CONFIDENCE:
                        klassifikation = raw_klass
                if klassifikation.confidence >= MIN_BETRAG_CONFIDENCE:
                    field_name = klassifikation.feldname
                elif klassifikation.is_ambiguous:
                    field_name = "betraege_unklar"  # ambiguous → do not render
                else:
                    field_name = "betraege"  # no keyword → generic amount
                # U10: total-payment-obligation ("Gesamtzahllast") override — "zahllast"
                # must be present in the left-hand context (prevents false positives such
                # as expert-witness fees).
                # Pattern 1: "zahllast ... betraegt [AMOUNT] fällig jeweils am"
                # Pattern 2: "Gesamte monatliche Zahllast: [AMOUNT]" (direct label)
                # "betraege" is included: if there is no rent-keyword match within the
                # 50-character window → field="betraege" generic, but pattern 2 matches →
                # the quote must be secured with left=150 so that clean_snippet (applied
                # 3x) doesn't strip out "Zahllast:" and the quality score stays competitive.
                _is_zahllast = False
                if field_name in ("nebenkosten", "betraege_unklar", "miete", "betraege"):
                    _left_100 = flat_text[max(0, _fs - 100):_fs].lower()
                    _left_20 = flat_text[max(0, _fs - 20):_fs]
                    _right_50 = flat_text[_fe:min(len(flat_text), _fe + 50)]
                    _p1 = (
                        "zahllast" in _left_100
                        and _ZAHLLAST_LEFT_RE.search(_left_20)
                        and _ZAHLLAST_RIGHT_RE.search(_right_50)
                    )
                    _p2 = bool(re.search(r"zahllast\s*[:\-]?\s*$", _left_20, re.IGNORECASE))
                    if _p1 or _p2:
                        field_name = "betraege"
                        quote = _window_quote(flat_text, _fs, _fe, left=150, right=70)
                        _is_zahllast = True
                _ev_item: dict[str, Any] = {
                    "field": field_name,
                    "value": amount_value,
                    "page": page_no,
                    "quote": _clip_quote_200(quote),
                }
                if _is_zahllast:
                    _ev_item["is_zahllast"] = True
                evidence.append(_ev_item)

            for date_match in date_re.finditer(flat_text):
                date_value = _normalize_ws(date_match.group(0))
                date_window = _window_quote(flat_text, date_match.start(), date_match.end(), left=120, right=120)
                date_sentence = _select_best_sentence_snippet(
                    flat_text,
                    date_value,
                    field_hint="fristen_termine",
                    fallback=date_window,
                )
                context = f"{date_window} {date_sentence}"
                if _is_meta_block_context(date_window):
                    continue
                if _contains_trigger_phrase(context, _DEADLINE_TRIGGER_PHRASES):
                    # FIX 4: filter past dates without a strong deadline context
                    if not _is_past_date_no_strong_frist_context(date_value, context):
                        evidence.append(
                            {
                                "field": "fristen_termine",
                                "value": date_value,
                                "page": page_no,
                                "quote": _clip_quote_200(date_sentence or date_window),
                                "label": _deadline_label_from_context(context),
                            }
                        )
                if _contains_trigger_phrase(context, _FIELD_SENTENCE_KEYWORDS["datum_beginn"]):
                    ctx_lower = context.lower()
                    _is_price_adj_context = any(
                        kw in ctx_lower
                        for kw in (
                            "preisanpassung", "preiserhöhung", "preiserhoehung",
                            "stichtag", "frühestens", "fruehestens",
                            "nach vertragsbeginn", "nach dem vertragsbeginn",
                            # FIX 5: "X monate nach" = calculation reference date, not contract start
                            "monate nach", "monate nach dem",
                        )
                    )
                    if _is_meta_block_context(context) or _is_price_adj_context:
                        pass  # no datum_beginn (contract-start) entry
                    else:
                        begin_quote = _select_best_sentence_snippet(
                            flat_text,
                            date_value,
                            field_hint="datum_beginn",
                            fallback=date_window,
                        )
                        evidence.append(
                            {
                                "field": "datum_beginn",
                                "value": date_value,
                                "page": page_no,
                                "quote": _clip_quote_200(begin_quote or date_window),
                            }
                        )

            # Long-format dates: "31. Januar 2025" – _select_best_sentence_snippet is
            # NOT used here because _SENTENCE_BOUNDARY_RE treats "31. " as a sentence
            # boundary and always splits the date across two sentences. Instead, use
            # the +/-120-character context window directly as the quote.
            for long_date_match in _GERMAN_MONTH_DATE_RE.finditer(flat_text):
                long_date_raw = _normalize_ws(long_date_match.group(0))
                _iso = _normalize_date_to_iso(long_date_raw)
                if _iso:
                    _y, _m, _d = _iso.split("-")
                    long_date_value = f"{_d}.{_m}.{_y}"
                else:
                    long_date_value = long_date_raw
                long_date_window = _window_quote(
                    flat_text, long_date_match.start(), long_date_match.end(), left=120, right=120
                )
                context = long_date_window
                if _is_meta_block_context(long_date_window):
                    continue
                if _contains_trigger_phrase(context, _DEADLINE_TRIGGER_PHRASES):
                    # FIX 4: filter past long-format dates without a strong deadline context
                    if not _is_past_date_no_strong_frist_context(long_date_value, context):
                        evidence.append(
                            {
                                "field": "fristen_termine",
                                "value": long_date_value,
                                "page": page_no,
                                "quote": _clip_quote_200(long_date_window),
                                "label": _deadline_label_from_context(context),
                            }
                        )

            # PR12 P2: determine renewal periods up front — these must not be
            # classified as a termination deadline, even if 'Kündigung' appears in
            # the context window.
            from core.legacy_deadlines import extract_renewal_and_termination
            _renewal_pairs = extract_renewal_and_termination(flat_text)
            _renewal_duration_values = {
                _normalize_ws(v) for v, lbl in _renewal_pairs if lbl == "verlaengerung"
            }

            for dur_match in _DEADLINE_DURATION_RE.finditer(flat_text):
                duration_value = _normalize_ws(dur_match.group(0))
                dur_window = _window_quote(flat_text, dur_match.start(), dur_match.end(), left=120, right=120)
                dur_quote = _select_best_sentence_snippet(
                    flat_text,
                    duration_value,
                    field_hint="fristen_termine",
                    fallback=dur_window,
                )
                context = f"{dur_window} {dur_quote}"
                if _is_meta_block_context(context):
                    continue
                if _contains_trigger_phrase(context, _DEADLINE_TRIGGER_PHRASES + ("kündigung", "kuendigung", "laufzeit")):
                    # PR12 P1: word-boundary check prevents a match on 'vorankündigung'
                    if _KUENDIGUNG_WORD_RE.search(context):
                        # PR12 P2: don't record the renewal period (e.g. "12 Monate") as
                        # a termination deadline — extract_renewal_and_termination() already
                        # supplies the termination deadline correctly, separately.
                        if duration_value not in _renewal_duration_values:
                            evidence.append(
                                {
                                    "field": "kuendigung_fristen",
                                    "value": duration_value,
                                    "page": page_no,
                                    "quote": _clip_quote_200(dur_quote or dur_window),
                                }
                            )
                    else:
                        evidence.append(
                            {
                                "field": "fristen_termine",
                                "value": duration_value,
                                "page": page_no,
                                "quote": _clip_quote_200(dur_quote or dur_window),
                                "label": _deadline_label_from_context(context),
                            }
                        )

            # PR12 P2: record the termination deadline from the renewal/termination clause
            for renewal_value, renewal_label in _renewal_pairs:
                if renewal_label == "kuendigungsfrist":
                    # Quote contains flat_text so that _has_termination_label_trigger applies
                    evidence.append(
                        {
                            "field": "kuendigung_fristen",
                            "value": renewal_value,
                            "page": page_no,
                            "quote": _clip_quote_200(flat_text),
                        }
                    )

        _page_lines = page_text.splitlines()
        for _line_idx, raw_line in enumerate(_page_lines):
            line = _normalize_ws(raw_line)
            if not line:
                continue
            line_l = line.lower()

            # Extract parties exclusively from segment lines and discard artifacts.
            # Extended with supply-contract/work-contract roles (lieferant, auftragnehmer, auftraggeber).
            if any(k in line_l for k in ("zwischen", "vertragsparteien", "arbeitgeber", "arbeitnehmer", "vermieter", "mieter", "käufer", "verkäufer", "lieferant", "auftragnehmer", "auftraggeber")):
                for party in _extract_party_candidates(line):
                    evidence.append(
                        {
                            "field": "parteien",
                            "value": party[:120],
                            "page": page_no,
                            "quote": _clip_quote_200(line),
                        }
                    )

            for amount_match in amount_re.finditer(line):
                amount_value = _normalize_ws(amount_match.group(0))
                if _is_artifact_value(amount_value):
                    continue
                if _is_year_like_amount(amount_value):  # PR13 P4: exclude year tokens
                    continue
                # Amount-to-field mapping: single line first, +/-2-line fallback
                _kontext = extract_context_lines(_page_lines, _line_idx, window=2)
                klassifikation = klassifiziere_betrag_mit_kontext(line, _kontext)
                if klassifikation.confidence >= MIN_BETRAG_CONFIDENCE:
                    field_name = klassifikation.feldname
                elif klassifikation.is_ambiguous:
                    field_name = "betraege_unklar"  # ambiguous → do not render
                else:
                    field_name = "betraege"  # no keyword → generic amount
                _entry: dict[str, Any] = {
                    "field": field_name,
                    "value": amount_value,
                    "page": page_no,
                    "quote": _clip_quote_200(line),
                }
                if field_name == "betraege_unklar":
                    _entry["label"] = "unklar – bitte prüfen"
                evidence.append(_entry)

            # Deadlines/dates ("Fristen/Termine"), document-based, from segment lines.
            if any(x in line_l for x in ("frist", "termin", "fällig", "faellig", "spätestens", "spaetestens", "kündigung", "kuendigung", "laufzeit")):
                values: list[str] = []
                values.extend(m.group(0) for m in date_re.finditer(line))
                values.extend(_normalize_ws(m.group(0)) for m in _DEADLINE_DURATION_RE.finditer(line))
                # FIX 2 (P1): raw-text fallback removed. Only lines with an actual date
                # or duration value produce deadline evidence. This keeps document
                # headers, company-name lines, and address blocks from being emitted
                # as a deadline value (e.g. "Kündigungsfrist: Innovatech Solutions GmbH...").
                # (Previously: values.append(line[:120]) as a fallback)
                for value in _dedupe_str_keep_order(values):
                    if _is_artifact_value(value):
                        continue
                    best_quote = _select_best_sentence_snippet(
                        page_text,
                        value,
                        field_hint="fristen_termine",
                        fallback=line,
                    )
                    context = f"{line} {best_quote}"
                    if _is_meta_block_context(context):
                        continue
                    # FIX 1/2: filter birth/employment/founding dates from the line-based path too.
                    if _is_past_date_no_strong_frist_context(value, context):
                        continue
                    # PR12 P1: word-boundary check prevents a match on 'vorankündigung'
                    if _KUENDIGUNG_WORD_RE.search(line_l):
                        kf_quote = _select_best_sentence_snippet(
                            page_text,
                            value,
                            field_hint="kuendigung_fristen",
                            fallback=line,
                        )
                        evidence.append(
                            {
                                "field": "kuendigung_fristen",
                                "value": value[:120],
                                "page": page_no,
                                "quote": _clip_quote_200(kf_quote or line),
                            }
                        )
                    else:
                        evidence.append(
                            {
                                "field": "fristen_termine",
                                "value": value[:120],
                                "page": page_no,
                                "quote": _clip_quote_200(best_quote or line),
                                "label": _deadline_label_from_context(context),
                            }
                        )

            if any(x in line_l for x in ("beginn", "vertragsbeginn", "ab dem", "beginn des vertrages")):
                # FIX 5: price-adjustment reference dates are not a contract start date.
                # Only check specific patterns that clearly indicate a calculated
                # reference date. "frühestens" and "preiserhöhung" alone must not block —
                # they can appear in a different sentence on the same line (→ a genuine
                # contract-start date is still kept).
                _line_is_price_adj = any(
                    kw in line_l
                    for kw in (
                        "nach vertragsbeginn", "nach dem vertragsbeginn",
                        "monate nach vertragsbeginn",
                        "stichtag",
                        "preisanpassung",
                    )
                )
                if _is_meta_block_context(line) or _line_is_price_adj:
                    pass  # no datum_beginn (contract-start) entry
                else:
                    for match in date_re.finditer(line):
                        begin_value = _normalize_ws(match.group(0))
                        begin_quote = _select_best_sentence_snippet(
                            page_text,
                            begin_value,
                            field_hint="datum_beginn",
                            fallback=line,
                        )
                        evidence.append(
                            {
                                "field": "datum_beginn",
                                "value": begin_value,
                                "page": page_no,
                                "quote": _clip_quote_200(begin_quote or line),
                            }
                        )

            if any(x in line_l for x in ("urlaub", "überstunden", "ueberstunden", "mehrarbeit")) and not _is_rhetoric_value(line):
                evidence.append(
                    {
                        "field": "urlaub_ueberstunden",
                        "value": line[:120],
                        "page": page_no,
                        "quote": _clip_quote_200(line),
                    }
                )

            if any(x in line_l for x in ("wettbewerbsverbot", "konkurrenz", "wettbewerbsklausel")) and not _is_rhetoric_value(line):
                evidence.append(
                    {
                        "field": "wettbewerbsverbot",
                        "value": line[:120],
                        "page": page_no,
                        "quote": _clip_quote_200(line),
                    }
                )

            # Case reference ("Aktenzeichen"): "Az. 4 O 321/24", "Aktenzeichen: ABC-2025", "Geschäftszeichen: ..."
            _az_match = _AZ_LINE_RE.search(line)
            if _az_match:
                az_value = _clean_az_value(_normalize_ws(_az_match.group(1)).strip())
                if az_value and not _is_artifact_value(az_value):
                    evidence.append(
                        {
                            "field": "aktenzeichen",
                            "value": az_value[:80],
                            "page": page_no,
                            "quote": _clip_quote_200(line),
                        }
                    )

            # Court ("Gericht"): "Amtsgericht Frankfurt", "Landgericht Berlin", "OLG München"
            _g_match = _GERICHT_RE.search(line)
            if _g_match:
                gericht_value = _normalize_ws(_g_match.group(1))
                if gericht_value and not _is_artifact_value(gericht_value):
                    evidence.append(
                        {
                            "field": "gericht",
                            "value": gericht_value[:80],
                            "page": page_no,
                            "quote": _clip_quote_200(line),
                        }
                    )

    # FIX 3: filter court ("Gericht") matches that appear only on the last page of a
    # multi-page segment. These usually originate from the next segment
    # (cross-segment contamination). Single-page segments are not filtered.
    # For segments with <= 3 pages: do NOT apply the filter – short documents often
    # place jurisdiction clauses in the closing provisions (last page).
    if len(searchable_pages) > 3:
        last_page_no = _get_page_no(searchable_pages[-1])
        gericht_pages = {e["page"] for e in evidence if e["field"] == "gericht"}
        if gericht_pages and gericht_pages <= {last_page_no}:
            evidence = [e for e in evidence if e["field"] != "gericht"]

    return _normalize_evidence_map(evidence)


def build_evidence_context(evidence_map: list[dict[str, Any]]) -> str:
    lines = [
        "EVIDENCE MAP (nur diese Werte verwenden):",
        "Wenn ein Feld 'missing' ist, antworte mit 'unverified' oder 'missing' und formuliere eine Rückfrage.",
    ]
    for item in evidence_map:
        field = item.get("field", "")
        value = item.get("value", "missing")
        page = item.get("page")
        quote = _clip_quote_200(str(item.get("quote", "")))
        page_token = "?" if page is None else str(page)
        lines.append(f"- field={field} | value={value} | page={page_token} | quote={quote}")
    return "\n".join(lines)


def attach_evidence_map_to_parsed(parsed: dict, evidence_map: list[dict[str, Any]]) -> dict:
    out = dict(parsed or {})
    out["evidence_map"] = evidence_map
    return out
