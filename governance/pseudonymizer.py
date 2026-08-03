"""governance/pseudonymizer.py – Person and company pseudonymization.

Extracted from governance/redaction.py (W2 god-file split).
Contains pseudonymize_people(), _apply_pseudonymization(),
_apply_company_redaction(), apply_pseudonym_map(), and all
related helper functions.

Note: the patterns below target German-language legal/business documents
(company suffixes, address formats, honorifics, etc.) and are intentionally
not translated — they match content, not code.
"""

from __future__ import annotations

import re
from typing import Any

from governance.redaction_patterns import (
    _ADDRESS_HINT_RE,
    _ALL_UPPER_LINE_RE,
    _COMPANY_ARTICLE_RE,
    _COMPANY_NAME_RE,
    _COMPOUND_TERM_SUFFIXES,
    _NON_PERSON_PREFIXES,
    _NON_PERSON_TOKENS,
    _NOT_A_PERSON_TOKENS,
    _PARTY_COMPANY_RE,
    _PARTY_PERSON_RE,
    _PERSON_CANDIDATE_RE,
    _PERSON_STOPLIST,
    _PLACEHOLDER_LEAK_RE,
    _PSEUDONYM_TOKEN_RE,
    _ROLE_CONTEXT_RE,
    _STANDALONE_ORG_RE,
    _TITLE_PREFIX_RE,
    _TRAILING_ORG_SUFFIX_RE,
    _extract_mask_type,
    _new_placeholder,
)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _normalize_person_key(name: str) -> str:
    cleaned = _TITLE_PREFIX_RE.sub("", (name or "").strip())
    return re.sub(r"\s+", " ", cleaned)


def _person_label_for_index(index: int, max_labels: int) -> str:
    if index == 0:
        return "[PARTEI_1]"
    if index == 1:
        return "[PARTEI_2]"
    del max_labels
    return f"[PERSON_{index - 1}]"


def _is_likely_address(text: str, start: int, end: int, candidate: str) -> bool:
    address_suffixes = (
        "straße",
        "strasse",
        "str",
        "platz",
        "weg",
        "allee",
        "gasse",
        "ufer",
        "damm",
        "chaussee",
        "promenade",
        "stieg",
        "steig",
    )
    if _ADDRESS_HINT_RE.search(candidate):
        return True
    for token in re.findall(r"[A-Za-zÄÖÜäöüß\-]+", candidate):
        if token.lower().rstrip(".").endswith(address_suffixes):
            return True
    window = text[max(0, start - 30) : min(len(text), end + 30)]
    if _ADDRESS_HINT_RE.search(window) and re.search(r"\d{1,4}[a-zA-Z]?", window):
        return True
    return False


def _is_person_candidate(text: str, start: int, end: int, candidate: str) -> bool:
    normalized = re.sub(r"\s+", " ", (candidate or "").strip())
    if not normalized:
        return False
    # Stoplist: known legal/boilerplate terms
    if normalized.casefold() in _PERSON_STOPLIST:
        return False
    # Lines consisting only of uppercase letters/digits/special chars → not a name
    if _ALL_UPPER_LINE_RE.fullmatch(normalized):
        return False
    core = _TITLE_PREFIX_RE.sub("", normalized)
    name_tokens = re.findall(r"[A-ZÄÖÜ][a-zäöüß]+", core)
    if len(name_tokens) < 2:
        return False
    lowered_tokens = [t.lower() for t in name_tokens]
    if any(token in _NON_PERSON_TOKENS or token in _NOT_A_PERSON_TOKENS for token in lowered_tokens):
        return False
    tokens = normalized.split()
    if tokens and tokens[0] in _NON_PERSON_PREFIXES:
        return False
    if any(_PSEUDONYM_TOKEN_RE.match(token) for token in tokens):
        return False
    if _is_likely_address(text, start, end, normalized):
        return False
    # Compound professional term check: "Projektbezogene Tagessätze" → not a person.
    # Check the candidate itself AND the surrounding ±40-char context.
    if _COMPOUND_TERM_SUFFIXES.search(normalized):
        return False
    right_ctx = text[end : min(len(text), end + 40)]
    if _COMPOUND_TERM_SUFFIXES.search(right_ctx):
        return False
    left_context = text[max(0, start - 60) : start]
    if _COMPOUND_TERM_SUFFIXES.search(left_context[:40]):
        return False
    if not _ROLE_CONTEXT_RE.search(left_context) and normalized.count(" ") > 2:
        return False
    return True


def _overlaps_seen(start: int, end: int, seen_spans: set[tuple[int, int]]) -> bool:
    """Checks whether (start, end) overlaps with any already reserved span."""
    return any(not (end <= s or start >= e) for s, e in seen_spans)

# ---------------------------------------------------------------------------
# Main functions
# ---------------------------------------------------------------------------


def pseudonymize_people(text: str, max_labels: int = 10) -> tuple[str, dict[str, str]]:
    """
    Deterministically replaces detected person names with pseudonyms.

    Returns: (pseudonymized_text, mapping_original_to_label)
    """
    if not text:
        return "", {}

    safe_max_labels = max(3, int(max_labels or 10))
    assigned_labels = {
        token
        for token in _PLACEHOLDER_LEAK_RE.findall(text)
        if _PSEUDONYM_TOKEN_RE.fullmatch(token)
    }
    next_label_index = 0
    key_to_label: dict[str, str] = {}
    original_to_label: dict[str, str] = {}
    replacements: list[tuple[int, int, str]] = []
    seen_spans: set[tuple[int, int]] = set()

    def _assign_label(name_key: str) -> str:
        nonlocal next_label_index
        if name_key not in key_to_label:
            while True:
                label = _person_label_for_index(next_label_index, safe_max_labels)
                next_label_index += 1
                if label in assigned_labels:
                    continue
                key_to_label[name_key] = label
                assigned_labels.add(label)
                break
        return key_to_label[name_key]

    # P1-A / FIX 1: Pre-initialize the ORG counter and map so that both
    # the _PARTY_PERSON_RE loop (on ORG-suffix detection) and the later
    # standalone-ORG loop use the same counter.
    _existing_org_nums = [int(m.group(1)) for m in re.finditer(r"\[ORG_(\d+)\]", text)]
    _org_counter: int = max(_existing_org_nums) if _existing_org_nums else 0
    _org_key_to_label: dict[str, str] = {}

    # PR11 P9: Scan company names after role designations first, so the
    # full company name (incl. GmbH suffix) blocks the partial person match
    # (only 2 words via _PARTY_PERSON_RE) via the overlap guard.
    # IMPORTANT: _assign_label() is only called AFTER the overlap check,
    # so no label slot is consumed for skipped partial matches.
    for match in _PARTY_COMPANY_RE.finditer(text):
        company_start, company_end = match.span("company")
        original = match.group("company").strip()
        if not original or "[" in original or "]" in original:
            continue
        if _overlaps_seen(company_start, company_end, seen_spans):
            continue
        key = _normalize_person_key(original)
        if not key:
            continue
        label = _assign_label(key)
        original_to_label.setdefault(original, label)
        replacements.append((company_start, company_end, label))
        seen_spans.add((company_start, company_end))

    for match in _PARTY_PERSON_RE.finditer(text):
        name_start, name_end = match.span("name")
        original = match.group("name").strip()
        if not _is_person_candidate(text, name_start, name_end, original):
            continue
        if _overlaps_seen(name_start, name_end, seen_spans):
            continue
        # P1-A: Check whether a legal-form suffix follows the matched name
        # → if so, it's a company name (ORG), not a person.
        suffix_match = _TRAILING_ORG_SUFFIX_RE.match(text[name_end:])
        if suffix_match:
            # Extend the span to the full company name incl. suffix
            full_end = name_end + suffix_match.end()
            full_original = text[name_start:full_end].strip()
            if _overlaps_seen(name_start, full_end, seen_spans):
                continue
            org_key = full_original.casefold()
            if org_key not in _org_key_to_label:
                _org_counter += 1
                _org_key_to_label[org_key] = f"[ORG_{_org_counter}]"
            org_label = _org_key_to_label[org_key]
            original_to_label.setdefault(full_original, org_label)
            replacements.append((name_start, full_end, org_label))
            seen_spans.add((name_start, full_end))
            continue
        key = _normalize_person_key(original)
        if not key:
            continue
        label = _assign_label(key)
        original_to_label.setdefault(original, label)
        replacements.append((name_start, name_end, label))
        seen_spans.add((name_start, name_end))

    # Bug 1 (P1): Standalone-ORG detection – company name WITHOUT role context.
    # Runs after role-based detection (_PARTY_COMPANY_RE / _PARTY_PERSON_RE),
    # so role-context companies keep their [PARTEI_N] labels. Runs BEFORE
    # _PERSON_CANDIDATE_RE, so no partial name gets masked as PERSON and
    # the legal-form suffix ("GmbH & Co. KG") stays in plaintext.
    # _org_counter / _org_key_to_label were initialized above, before the
    # _PARTY_PERSON_RE loop (P1-A), so both loops use the same counter.
    for match in _STANDALONE_ORG_RE.finditer(text):
        org_start, org_end = match.span()
        original = match.group(0).strip()
        if not original or "[" in original or "]" in original:
            continue
        if _overlaps_seen(org_start, org_end, seen_spans):
            continue
        key = original.casefold()
        if key not in _org_key_to_label:
            _org_counter += 1
            _org_key_to_label[key] = f"[ORG_{_org_counter}]"
        org_label = _org_key_to_label[key]
        original_to_label.setdefault(original, org_label)
        replacements.append((org_start, org_end, org_label))
        seen_spans.add((org_start, org_end))

    for match in _PERSON_CANDIDATE_RE.finditer(text):
        original = match.group(0).strip()
        if not _is_person_candidate(text, match.start(), match.end(), original):
            continue
        if _overlaps_seen(match.start(), match.end(), seen_spans):
            continue
        # P1-A: Also check context-free candidates for a legal-form suffix
        suffix_match = _TRAILING_ORG_SUFFIX_RE.match(text[match.end():])
        if suffix_match:
            full_end = match.end() + suffix_match.end()
            full_original = text[match.start():full_end].strip()
            if _overlaps_seen(match.start(), full_end, seen_spans):
                continue
            org_key = full_original.casefold()
            if org_key not in _org_key_to_label:
                _org_counter += 1
                _org_key_to_label[org_key] = f"[ORG_{_org_counter}]"
            org_label = _org_key_to_label[org_key]
            original_to_label.setdefault(full_original, org_label)
            replacements.append((match.start(), full_end, org_label))
            seen_spans.add((match.start(), full_end))
            continue
        key = _normalize_person_key(original)
        if not key:
            continue
        label = _assign_label(key)
        original_to_label.setdefault(original, label)
        replacements.append((match.start(), match.end(), label))
        seen_spans.add((match.start(), match.end()))

    if not replacements:
        return text, {}

    out_parts: list[str] = []
    cursor = 0
    # Longer spans take precedence at the same start point (safety net).
    for start, end, label in sorted(replacements, key=lambda item: (item[0], -item[1])):
        if start < cursor:
            continue
        out_parts.append(text[cursor:start])
        out_parts.append(label)
        cursor = end
    out_parts.append(text[cursor:])
    out = "".join(out_parts)

    # FIX 6 (P2): Second pass – masks standalone surnames when the full
    # name has already been captured elsewhere in the document.
    # Example: "Kevin Stark" → [PARTEI_1]; afterwards "Stark" alone remains
    # unmasked in the text. This second pass replaces it with the same
    # placeholder. Only surnames with >=4 characters, capitalized, and not
    # a stopword.
    _surname_to_label: dict[str, str] = {}
    for original, label in original_to_label.items():
        name_clean = _TITLE_PREFIX_RE.sub("", original).strip()
        parts = name_clean.split()
        if len(parts) >= 2:
            surname = parts[-1]
            if (
                len(surname) >= 4
                and surname[0].isupper()
                and surname.lower() not in _NON_PERSON_TOKENS
                and surname not in _surname_to_label
            ):
                _surname_to_label[surname] = label

    for surname, label in sorted(_surname_to_label.items(), key=lambda x: -len(x[0])):
        surname_re = re.compile(rf"\b{re.escape(surname)}\b")
        out = surname_re.sub(label, out)

    return out, original_to_label


def _apply_pseudonymization(text: str) -> tuple[str, dict[str, str], dict[str, Any]]:
    out, original_to_label = pseudonymize_people(text)
    if not original_to_label:
        return out, {}, {"mask_counts": {}, "mask_types": {}, "entity_count": 0, "placeholder_keys": []}

    mapping_add: dict[str, str] = {}
    label_counts: dict[str, int] = {}
    for original, label in original_to_label.items():
        mapping_add.setdefault(label, original)
        label_counts[label] = out.count(label)

    mask_types: dict[str, int] = {}
    for label, count in label_counts.items():
        kind = _extract_mask_type(label)
        mask_types[kind] = int(mask_types.get(kind, 0)) + int(count)

    return out, mapping_add, {
        "mask_counts": dict(sorted(label_counts.items(), key=lambda item: item[0])),
        "mask_types": dict(sorted(mask_types.items(), key=lambda item: item[0])),
        "entity_count": len(mapping_add),
        "placeholder_keys": sorted(label_counts.keys()),
    }


def _apply_company_redaction(text: str) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Pseudonymizes company names as [FIRMA_N]."""
    if not text:
        return "", {}, {"mask_counts": {}, "mask_types": {}, "entity_count": 0, "placeholder_keys": []}

    mapping_add: dict[str, str] = {}
    counters: dict[str, int] = {}
    assigned_by_value: dict[str, str] = {}
    mask_counts: dict[str, int] = {}

    def _sub(match: re.Match[str]) -> str:
        raw = match.group(0)
        source = raw.strip()
        if not source or "[" in source or "]" in source:
            return raw
        # Strip leading article (e.g. "Die Müller GmbH" → "Müller GmbH")
        article_match = _COMPANY_ARTICLE_RE.match(source)
        prefix = ""
        if article_match:
            prefix = article_match.group(0)
            source = source[len(prefix):]
        if not source:
            return raw
        value_key = source.casefold()
        placeholder = assigned_by_value.get(value_key)
        if not placeholder:
            placeholder = _new_placeholder("FIRMA", counters)
            assigned_by_value[value_key] = placeholder
            mapping_add[placeholder] = source
        mask_counts[placeholder] = int(mask_counts.get(placeholder, 0)) + 1
        return prefix + placeholder

    out = _COMPANY_NAME_RE.sub(_sub, text)

    mask_types: dict[str, int] = {}
    for placeholder, count in mask_counts.items():
        kind = _extract_mask_type(placeholder)
        mask_types[kind] = int(mask_types.get(kind, 0)) + int(count)

    return out, mapping_add, {
        "mask_counts": dict(sorted(mask_counts.items(), key=lambda item: item[0])),
        "mask_types": dict(sorted(mask_types.items(), key=lambda item: item[0])),
        "entity_count": len(mapping_add),
        "placeholder_keys": sorted(mask_counts.keys()),
    }


def apply_pseudonym_map(text: str, mapping: dict[str, str]) -> str:
    """
    Applies an existing pseudonym mapping to arbitrary text.
    Expects mapping: original -> placeholder.
    """
    if not text or not mapping:
        return text
    out = text
    for original, label in sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True):
        if not original:
            continue
        out = out.replace(original, label)
    return out
