"""
core/party_extractor.py – Party detection (people, organizations)
Moved out of core/evidence.py (W2 – evidence split).
"""

import re
from typing import Any

from core.models import _normalize_ws, clean_snippet
from core.snippet import (
    _normalize_for_match,
    _to_short_citation_quote,
    _clip_quote_200,
)
from core.citation import _normalize_item

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PARTY_ROLE_HINTS = (
    "kläger",
    "klaeger",
    "beklagter",
    "beklagte",
    "vermieter",
    "mieter",
    "arbeitgeber",
    "arbeitnehmer",
    "käufer",
    "verkäufer",
)
_STOPWORD_ENTITIES = {
    "zwischen",
    "nachfolgend",
    "arbeitgeber",
    "arbeitnehmer",
    "mieter",
    "vermieter",
    "unterzeichnet",
    "unterschriften",
    "praeambel",
    "präambel",
    "ort",
    "ort, datum",
    "absatz",
    "seite",
    "datum",
    "vertragsparteien",
    "partei",
    "parteien",
    "klausel",
    "anlage",
    "anhang",
    "paragraph",
    "preambel",
    "präambel",
    "§",
}
_RHETORIC_PATTERNS = [
    r"^\s*(zwischen|nachfolgend|arbeitgeber|arbeitnehmer)\b",
    r"^\s*(unterzeichnet|unterschriften|präambel|praeambel)\b",
    r"^\s*(§|absatz|seite|datum:?)\b",
]
_ARTIFACT_VALUES = {
    "zwischen",
    "nachfolgend",
    "arbeitgeber",
    "arbeitnehmer",
    "mieter",
    "vermieter",
    "unterschriften",
    "§",
    "ort datum",
    "ort, datum",
    "präambel",
    "praeambel",
}
# Document-type labels that must NOT be extracted as a party name.
# Party = natural or legal person; document-type headers are not parties.
_DOC_TYPE_HEADER_KEYWORDS: frozenset[str] = frozenset({
    "MÄNGELANZEIGE", "MAENGELANZEIGE",
    "KLAGESCHRIFT",
    "KLAGEERWIDERUNG",
    "URTEIL",
    "BESCHLUSS",
    "MAHNUNG",
    "KÜNDIGUNG", "KUENDIGUNG",
    "ANTWORTSCHREIBEN",
    "KOSTENAUFSTELLUNG",
    "SACHVERSTÄNDIGENGUTACHTEN", "SACHVERSTAENDIGENGUTACHTEN",
    "SCHRIFTSATZ",
    "ABMAHNUNG",
    "BERUFUNGSBEGRÜNDUNG", "BERUFUNGSBEGRUENDUNG",
    "WIDERSPRUCH",
    "VOLLMACHT",
    "PROTOKOLL",
})

_ORG_SUFFIX_RE = re.compile(r"\b(gmbh|ug|ag|e\.v\.|kg|ohg)\b", flags=re.IGNORECASE)
_PERSON_RE = re.compile(r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+\b")

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _is_doc_type_header(value: str) -> bool:
    """True if the candidate is a document-type label, not a party.

    Checks whether one of the known document-type header terms is contained
    as a whole word in the value (case-insensitive).
    """
    v_upper = _normalize_ws(value).upper()
    for kw in _DOC_TYPE_HEADER_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", v_upper):
            return True
    return False


def _is_artifact_value(value: str) -> bool:
    normalized = _normalize_for_match(value).strip(" :;,.")
    return normalized in {_normalize_for_match(v) for v in _ARTIFACT_VALUES}


def _dedupe_str_keep_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        norm = _normalize_for_match(value)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(value)
    return out


def _looks_like_person(value: str) -> bool:
    tokens = [t for t in re.findall(r"[A-Za-zÄÖÜäöüß\-]+", value) if t]
    if len(tokens) < 2:
        return False
    first_two = tokens[:2]
    norm_tokens = [t.lower() for t in first_two]
    if any(t in _STOPWORD_ENTITIES for t in norm_tokens):
        return False
    if any(not token[:1].isupper() for token in first_two):
        return False
    return True


def _looks_like_org(value: str) -> bool:
    text = _normalize_ws(value)
    if _is_artifact_value(text):
        return False
    if _ORG_SUFFIX_RE.search(text):
        return True
    tokens = [t for t in re.findall(r"[A-Za-zÄÖÜäöüß&\-.]+", text) if t]
    if len(tokens) < 2:
        return False
    if any(_is_artifact_value(token) for token in tokens):
        return False
    return True


def _is_rhetoric_value(value: str) -> bool:
    text = _normalize_ws(value)
    if not text:
        return True
    if _is_artifact_value(text):
        return True
    norm = text.lower().strip(" :;,.")
    if norm in _STOPWORD_ENTITIES:
        return True
    return any(re.search(p, norm, flags=re.IGNORECASE) for p in _RHETORIC_PATTERNS)


def _extract_party_candidates(line: str) -> list[str]:
    # Removes typical role/preamble markers and extracts only genuine entities.
    stripped = _normalize_ws(line)
    stripped = re.sub(
        r"\b(nachfolgend|arbeitgeber|arbeitnehmer|mieter|vermieter|zwischen|vertragsparteien|präambel)\b",
        " ",
        stripped,
        flags=re.IGNORECASE,
    )
    if ":" in stripped:
        stripped = stripped.split(":", 1)[1]
    candidates = [
        _normalize_ws(chunk)
        for chunk in re.split(r"\bund\b|/|,|;|\|", stripped, flags=re.IGNORECASE)
        if _normalize_ws(chunk)
    ]
    regex_hits = [m.group(0) for m in _PERSON_RE.finditer(line)]
    for match in _ORG_SUFFIX_RE.finditer(line):
        start = max(0, line.rfind(" ", 0, match.start() - 1))
        end = line.find(",", match.end())
        if end == -1:
            end = len(line)
        regex_hits.append(_normalize_ws(line[start:end]))
    merged = _dedupe_str_keep_order(candidates + regex_hits)
    filtered: list[str] = []
    for candidate in merged:
        if _is_rhetoric_value(candidate) or _is_artifact_value(candidate):
            continue
        if _is_doc_type_header(candidate):
            continue
        if _looks_like_person(candidate) or _looks_like_org(candidate):
            filtered.append(candidate)
    return filtered


# ---------------------------------------------------------------------------
# Party extraction from structured output / segment text
# ---------------------------------------------------------------------------


def _extract_party_values_from_text(text: str) -> list[str]:
    source = _normalize_ws(text)
    if not source:
        return []

    values: list[str] = []
    role_re = re.compile(
        r"\b(kl[äa]ger(?:in)?|beklagt(?:e|er|en)?|vermieter(?:in)?|mieter(?:in)?|"
        r"arbeitgeber|arbeitnehmer|k[äa]ufer(?:in)?|verk[äa]ufer(?:in)?)\b\s*[:\-]\s*([^.;\n]+)",
        flags=re.IGNORECASE,
    )
    for match in role_re.finditer(source):
        role = match.group(1)
        value = match.group(2).strip()
        candidates = _extract_party_candidates(f"{role}: {value}")
        values.extend(candidates if candidates else [value])

    if not values and "zwischen" in source.lower():
        values.extend(_extract_party_candidates(source))

    cleaned: list[str] = []
    for value in _dedupe_str_keep_order(values):
        norm = _normalize_ws(value)
        if not norm or _is_artifact_value(norm):
            continue
        if _is_doc_type_header(norm):
            continue
        if _looks_like_person(norm) or _looks_like_org(norm):
            cleaned.append(norm[:120])
    return cleaned


def _collect_structured_party_evidence(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    parties = _extract_party_values_from_text(str(parsed.get("dokumenttyp_parteien", "") or ""))
    if not parties:
        return []

    citation_pool: list[dict[str, Any]] = []
    for field in ("kernaussagen", "auffaelligkeiten", "top3_findings"):
        for raw in list(parsed.get(field, []) or []):
            item = _normalize_item(raw)
            for citation in list(item.get("citations", []) or []):
                page = citation.get("page_number")
                quote = _to_short_citation_quote(str(citation.get("quote_snippet", "") or ""))
                if page is None or not quote:
                    continue
                citation_pool.append(
                    {
                        "page": int(page),
                        "quote": quote,
                    }
                )

    if not citation_pool:
        return []

    unique_pool: list[dict[str, Any]] = []
    seen_pool: set[tuple[int, str]] = set()
    for citation in citation_pool:
        key = (citation["page"], _normalize_for_match(citation["quote"]))
        if key in seen_pool:
            continue
        seen_pool.add(key)
        unique_pool.append(citation)

    evidence: list[dict[str, Any]] = []
    for party in parties:
        party_tokens = {t for t in _normalize_for_match(party).split() if len(t) > 2}
        match = None
        for citation in unique_pool:
            quote_tokens = set(_normalize_for_match(citation["quote"]).split())
            if party_tokens and party_tokens & quote_tokens:
                match = citation
                break
        if match is None:
            match = unique_pool[0]
        evidence.append(
            {
                "field": "parteien",
                "value": party,
                "page": match["page"],
                "quote": match["quote"],
            }
        )
    from core.evidence_map import _dedupe_evidence  # lazy: avoids circular import
    return _dedupe_evidence(evidence)


def _collect_regex_party_evidence(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from core.evidence_map import _dedupe_evidence, _get_page_no  # lazy: avoids circular import
    evidence: list[dict[str, Any]] = []
    for page in pages:
        page_no = _get_page_no(page)
        page_text = str(page.get("text", "") or "")
        for raw_line in page_text.splitlines():
            line = _normalize_ws(raw_line)
            if not line:
                continue
            line_l = line.lower()
            if not any(hint in line_l for hint in _PARTY_ROLE_HINTS) and "zwischen" not in line_l:
                continue
            for party in _extract_party_candidates(line):
                evidence.append(
                    {
                        "field": "parteien",
                        "value": party[:120],
                        "page": page_no,
                        "quote": clean_snippet(line, max_chars=180),
                    }
                )
    return _dedupe_evidence(evidence)


def ensure_party_evidence_consistency(
    evidence_map: list[dict[str, Any]],
    parsed: dict[str, Any],
    pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Prevents parteien=missing when parties are identifiable in the structured output or in the segment text.
    Priority:
    a) structured parties from LLM output (with citations)
    b) regex-based heuristic from segment text
    c) missing
    """
    from core.evidence_map import (  # lazy: avoids circular import
        _dedupe_evidence,
        _searchable_pages_without_repeating_edges,
        _normalize_evidence_map,
    )

    non_party_items = [
        item for item in list(evidence_map or [])
        if str(item.get("field", "") or "") != "parteien" and str(item.get("value", "") or "") != "missing"
    ]
    existing_party_items = [
        item for item in list(evidence_map or [])
        if str(item.get("field", "") or "") == "parteien" and str(item.get("value", "") or "") != "missing"
    ]

    party_items = _dedupe_evidence(existing_party_items)
    if not party_items:
        party_items = _collect_structured_party_evidence(parsed if isinstance(parsed, dict) else {})
    if not party_items:
        party_items = _collect_regex_party_evidence(_searchable_pages_without_repeating_edges(pages))

    merged_items = list(non_party_items) + list(party_items)
    return _normalize_evidence_map(merged_items)
