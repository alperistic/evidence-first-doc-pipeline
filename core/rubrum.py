"""core/rubrum.py – Case caption (rubrum) parsing and party extraction.

Moved out of core/analyzer.py (Step 4).
Contains all rubrum regexes, helpers, and the public
apply_rubrum_parties_override function.
"""

import re
from typing import Any

from core.models import _normalize_ws
from core.evidence import _is_artifact_value
from core.snippet import _normalize_for_match, _clip_quote_200

# ---------------------------------------------------------------------------
# Rubrum regex constants
# ---------------------------------------------------------------------------

_PAGE_TAG_RE = re.compile(r"^\s*\[(?:seite|page)\s+(\d+)\]\s*$", flags=re.IGNORECASE | re.MULTILINE)
_RUBRUM_PLAINTIFF_MARKER_RE = re.compile(
    r"\b(kl[äa]ger(?:in)?)\s*:\s*",
    flags=re.IGNORECASE,
)
_RUBRUM_DEFENDANT_MARKER_RE = re.compile(
    r"\b(beklagt(?:e|er|en)?)\s*:\s*",
    flags=re.IGNORECASE,
)
_RUBRUM_COUNSEL_MARKER_RE = re.compile(
    r"\b(?:vertreten\s+durch|prozessbevollm[äa]chtigte(?:r|n)?)\s*:\s*",
    flags=re.IGNORECASE,
)
_RUBRUM_MARKER_RE = re.compile(
    r"\b(?:kl[äa]ger(?:in)?\s*:|beklagt(?:e|er|en)?\s*:|vertreten\s+durch\s*:)\b",
    flags=re.IGNORECASE,
)
_RUBRUM_SECTION_BREAK_RE = re.compile(
    r"\b(?:"
    r"i\.\s*antr[äa]ge|ii\.\s*sachverhalt|iii\.\s*rechtliche|iv\.\s*beweismittel|"
    r"klage(?:\s+wegen)?|streitwert|antr[äa]ge|sachverhalt|beweismittel|"
    r"[a-zäöüß]+,\s*den\s+\d{1,2}\.\d{1,2}\.\d{2,4}|den\s+\d{1,2}\.\d{1,2}\.\d{2,4}"
    r")\b",
    flags=re.IGNORECASE,
)
_RUBRUM_ADDR_OR_META_SPLIT_RE = re.compile(
    r"(?:\[(?:ADDR|EMAIL|IBAN)_[A-Z0-9]+\])"
    r"|(?:\b(?:e-?mail|telefon|tel\.?|fax|aktenzeichen)\b)"
    r"|(?:\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]*"
    r"(?:straße|str\.|strasse|weg|allee|platz|gasse|ring|damm|ufer|"
    r"chaussee|promenade|stieg|steig)\b)"
    r"|(?:\b\d{5}\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+)",
    flags=re.IGNORECASE,
)
_RUBRUM_PSEUDONYM_RE = re.compile(r"\[(?:PARTEI|PERSON)_[A-Z0-9]+\]")
_RUBRUM_LAWYER_PREFIX_RE = re.compile(
    r"^(?:rechtsanw[aä]lt(?:in)?|ra(?:in)?\b)",
    flags=re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Rubrum helpers
# ---------------------------------------------------------------------------


def _normalize_rubrum_value(parts: list[str]) -> str:
    cleaned = [_normalize_ws(str(part or "")) for part in parts if _normalize_ws(str(part or ""))]
    value = _normalize_ws(" ".join(cleaned))
    value = re.sub(r"\s+,", ",", value)
    return value.strip(" ,;")


def _looks_like_klageschrift_or_rubrum(parsed: dict[str, Any], text: str) -> bool:
    document_type = _normalize_for_match(str((parsed or {}).get("document_type", "") or ""))
    if "klageschrift" in document_type:
        return True
    doktyp = _normalize_for_match(str((parsed or {}).get("dokumenttyp_parteien", "") or ""))
    if "klageschrift" in doktyp:
        return True
    return bool(_RUBRUM_MARKER_RE.search(str(text or "")))


def _rubrum_header_zone(source: str) -> str:
    text = str(source or "")
    if not text:
        return ""
    quarter_limit = max(1, int(len(text) * 0.25))
    page_tags = list(_PAGE_TAG_RE.finditer(text))
    if len(page_tags) >= 2:
        page2_end = page_tags[2].start() if len(page_tags) >= 3 else len(text)
        limit = max(quarter_limit, page2_end)
    elif len(page_tags) == 1:
        limit = max(quarter_limit, len(text))
    else:
        limit = quarter_limit
    return text[:limit]


def _rubrum_page_for_offset(source: str, offset: int) -> int:
    page_no = 1
    for match in _PAGE_TAG_RE.finditer(str(source or "")):
        if match.start() > offset:
            break
        try:
            page_no = int(match.group(1))
        except (TypeError, ValueError):
            page_no = 1
    return page_no


def _trim_rubrum_block_value(block: str) -> str:
    raw = str(block or "")
    if not raw:
        return ""
    section_break = _RUBRUM_SECTION_BREAK_RE.search(raw)
    if section_break:
        raw = raw[: section_break.start()]
    split_match = _RUBRUM_ADDR_OR_META_SPLIT_RE.search(raw)
    if split_match and split_match.start() > 0:
        raw = raw[: split_match.start()]
    value = _normalize_rubrum_value([raw])
    if not value:
        token_match = _RUBRUM_PSEUDONYM_RE.search(str(block or ""))
        if token_match:
            value = _normalize_ws(token_match.group(0))
    return value


def _apply_reverse_mapping_to_text(text: str, reverse_mapping: list[tuple[str, str]] | None) -> str:
    out = str(text or "")
    for original, placeholder in list(reverse_mapping or []):
        if not original or not placeholder:
            continue
        out = out.replace(str(original), str(placeholder))
    if _RUBRUM_PSEUDONYM_RE.search(out):
        return out
    normalized_out = _normalize_ws(out)
    if not normalized_out:
        return out
    for original, placeholder in list(reverse_mapping or []):
        normalized_original = _normalize_ws(str(original or ""))
        if not normalized_original:
            continue
        if normalized_out in normalized_original:
            return str(placeholder)
    return out


def _apply_reverse_mapping_to_parties(
    parties: list[dict[str, Any]],
    reverse_mapping: list[tuple[str, str]] | None,
) -> list[dict[str, Any]]:
    if not reverse_mapping:
        return parties
    remapped: list[dict[str, Any]] = []
    for item in parties:
        remapped_item = dict(item or {})
        raw_text = str(remapped_item.get("text", "") or "")
        remapped_text = _apply_reverse_mapping_to_text(
            raw_text,
            reverse_mapping,
        )
        if ":" in raw_text:
            raw_role, raw_value = raw_text.split(":", 1)
            mapped_value = _apply_reverse_mapping_to_text(raw_value.strip(), reverse_mapping)
            if mapped_value:
                remapped_text = f"{_normalize_ws(raw_role)}: {mapped_value}"
        remapped_item["text"] = remapped_text
        citations: list[dict[str, Any]] = []
        for citation in list(remapped_item.get("citations", []) or []):
            if not isinstance(citation, dict):
                continue
            citation_copy = dict(citation)
            citation_copy["quote_snippet"] = _apply_reverse_mapping_to_text(
                str(citation_copy.get("quote_snippet", "") or ""),
                reverse_mapping,
            )
            citations.append(citation_copy)
        remapped_item["citations"] = citations
        remapped.append(remapped_item)
    return remapped


def _extract_rubrum_parties_from_text(
    text: str,
    *,
    parsed: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    parsed_obj = parsed if isinstance(parsed, dict) else {}
    source = str(text or "")
    if not _looks_like_klageschrift_or_rubrum(parsed_obj, source):
        return []
    header_zone = _rubrum_header_zone(source)
    if not header_zone:
        return []

    claimant_marker = _RUBRUM_PLAINTIFF_MARKER_RE.search(header_zone)
    defendant_marker = _RUBRUM_DEFENDANT_MARKER_RE.search(header_zone)
    if claimant_marker is None or defendant_marker is None:
        return []

    counsel_marker = _RUBRUM_COUNSEL_MARKER_RE.search(header_zone)

    claimant_role_raw = _normalize_for_match(str(claimant_marker.group(1) or ""))
    claimant_role = "Klägerin" if "in" in claimant_role_raw else "Kläger"
    defendant_role_raw = _normalize_for_match(str(defendant_marker.group(1) or ""))
    defendant_role = "Beklagte" if defendant_role_raw.endswith("e") else "Beklagter"

    claimant_end_candidates: list[int] = [
        marker_pos
        for marker_pos in (
            counsel_marker.start() if counsel_marker and counsel_marker.start() > claimant_marker.end() else None,
            defendant_marker.start() if defendant_marker.start() > claimant_marker.end() else None,
        )
        if isinstance(marker_pos, int)
    ]
    claimant_break = _RUBRUM_SECTION_BREAK_RE.search(header_zone, claimant_marker.end())
    if claimant_break is not None:
        claimant_end_candidates.append(claimant_break.start())
    claimant_end = min(claimant_end_candidates) if claimant_end_candidates else len(header_zone)

    claimant_block = header_zone[claimant_marker.end() : claimant_end]

    counsel_block = ""
    if counsel_marker is not None:
        counsel_end_candidates: list[int] = [
            marker_pos
            for marker_pos in (
                defendant_marker.start() if defendant_marker.start() > counsel_marker.end() else None,
            )
            if isinstance(marker_pos, int)
        ]
        counsel_break = _RUBRUM_SECTION_BREAK_RE.search(header_zone, counsel_marker.end())
        if counsel_break is not None:
            counsel_end_candidates.append(counsel_break.start())
        counsel_end = min(counsel_end_candidates) if counsel_end_candidates else len(header_zone)
        counsel_block = header_zone[counsel_marker.end() : counsel_end]

    defendant_end_candidates: list[int] = []
    defendant_break = _RUBRUM_SECTION_BREAK_RE.search(header_zone, defendant_marker.end())
    if defendant_break is not None:
        defendant_end_candidates.append(defendant_break.start())
    defendant_blank_break = re.search(r"\n\s*\n", header_zone[defendant_marker.end() :])
    if defendant_blank_break is not None:
        defendant_end_candidates.append(defendant_marker.end() + int(defendant_blank_break.start()))
    defendant_end = min(defendant_end_candidates) if defendant_end_candidates else len(header_zone)
    defendant_block = header_zone[defendant_marker.end() : defendant_end]

    claimant_value = _trim_rubrum_block_value(claimant_block)
    counsel_value = _trim_rubrum_block_value(counsel_block)
    defendant_value = _trim_rubrum_block_value(defendant_block)

    if claimant_value and _RUBRUM_LAWYER_PREFIX_RE.match(claimant_value):
        claimant_value = ""
    if defendant_value and _RUBRUM_LAWYER_PREFIX_RE.match(defendant_value):
        defendant_value = ""

    parties: list[dict[str, Any]] = []

    def _append_party(role: str, value: str, marker_start: int, quote_source: str) -> None:
        norm_value = _normalize_ws(value).strip(" ,;.")
        if not norm_value or _is_artifact_value(norm_value):
            return
        page_no = _rubrum_page_for_offset(header_zone, marker_start)
        quote = _clip_quote_200(_normalize_ws(str(quote_source or "")))
        citations = []
        if quote:
            citations.append(
                {
                    "page_number": int(page_no) if int(page_no) > 0 else 1,
                    "quote_snippet": quote,
                    "section_hint": "rubrum",
                }
            )
        parties.append({"text": f"{role}: {norm_value}", "citations": citations})

    if claimant_value:
        _append_party(claimant_role, claimant_value, claimant_marker.start(), claimant_block)
    if counsel_value:
        _append_party("Prozessbevollmächtigte", counsel_value, counsel_marker.start() if counsel_marker else claimant_marker.start(), counsel_block)
    if defendant_value:
        _append_party(defendant_role, defendant_value, defendant_marker.start(), defendant_block)
    return parties


def apply_rubrum_parties_override(
    parsed: dict[str, Any],
    text: str,
    *,
    reverse_mapping: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    out = dict(parsed or {})
    extracted = _extract_rubrum_parties_from_text(text, parsed=out)
    if not extracted:
        return out
    out["parties"] = _apply_reverse_mapping_to_parties(extracted, reverse_mapping)
    return out


def _apply_rubrum_parties_extraction(
    parsed: dict[str, Any],
    text: str,
    *,
    reverse_mapping: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    return apply_rubrum_parties_override(parsed, text, reverse_mapping=reverse_mapping)
