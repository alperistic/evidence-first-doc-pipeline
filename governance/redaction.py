"""
redaction.py - fail-closed Redaction-Pipeline für LLM-Aufrufe.

Sicherheitsprinzipien:
- LLM erhält ausschließlich redacted_text (Platzhalter)
- Mapping bleibt lokal (placeholder -> original)
- Findings/Stats enthalten keine Klartextwerte
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

# ---------------------------------------------------------------------------
# Re-Exports aus governance.redaction_patterns (Fassade für Abwärtskompatibilität)
# ---------------------------------------------------------------------------
from governance.redaction_patterns import (  # noqa: F401
    _WORD_CHARS,
    _SPACE_RE,
    _FUZZY_TOKEN_RE,
    _FUZZY_CLEAN_RE,
    _PLACEHOLDER_RE,
    _RESTORE_TOKEN_RE,
    _PLACEHOLDER_LEAK_RE,
    _PERSON_CANDIDATE_RE,
    _PARTY_PERSON_RE,
    _PARTY_COMPANY_RE,
    _TITLE_PREFIX_RE,
    _PSEUDONYM_TOKEN_RE,
    _TRAILING_ORG_SUFFIX_RE,
    _ADDRESS_HINT_RE,
    _NON_PERSON_PREFIXES,
    _COMPOUND_TERM_SUFFIXES,
    _PERSON_STOPLIST,
    _ALL_UPPER_LINE_RE,
    _NON_PERSON_TOKENS,
    _NOT_A_PERSON_TOKENS,
    _ROLE_CONTEXT_RE,
    _MONEY_EXCLUDE_RE,
    _DATE_EXCLUDE_RE,
    _CASE_ID_EXCLUDE_RE,
    _ADDR_STREET_RE,
    _ADDR_PLZ_CITY_RE,
    _EXTERNAL_PARTY_META_LINE_RE,
    _EXTERNAL_ORG_RE,
    _STANDALONE_ORG_RE,
    _ORPHAN_COMPANY_SUFFIX_RE,
    _RegexRule,
    _BIC_VALID_COUNTRY_CODES,
    _is_valid_bic,
    _render_full_placeholder,
    _render_role_and_placeholder,
    _render_court_and_placeholder,
    _render_birthdate_placeholder,
    _ORG_SUFFIX_ENDS_RE,
    _is_not_org_name,
    _REGEX_RULES,
    _COMPANY_ARTICLE_RE,
    _COMPANY_NAME_RE,
    _EXTERNAL_SOURCE_LINE_RE,
    _EXT_NAME_AFTER_PLACEHOLDER_RE,
    _EXT_HAUSNR_AFTER_PLACEHOLDER_RE,
    _VALIDATOR_PLACEHOLDER_PATTERN,
    _POST_REDACTION_PII_PATTERNS,
    _POST_REDACTION_PII_PATTERN_NAMES,
    _new_placeholder,
    _extract_mask_type,
)

# ---------------------------------------------------------------------------
# Re-Exports aus governance.pseudonymizer (Fassade für Abwärtskompatibilität)
# ---------------------------------------------------------------------------
from governance.pseudonymizer import (  # noqa: F401
    _normalize_person_key,
    _person_label_for_index,
    _is_likely_address,
    _is_person_candidate,
    _overlaps_seen,
    pseudonymize_people,
    _apply_pseudonymization,
    _apply_company_redaction,
    apply_pseudonym_map,
)


@dataclass
class _ExternalMaskState:
    org_by_key: dict[str, str] = field(default_factory=dict)
    addr_by_key: dict[str, str] = field(default_factory=dict)
    org_counter: int = 0
    addr_counter: int = 0


@dataclass
class RedactionContext:
    redacted_text: str
    mapping: dict[str, str]  # placeholder -> original (nur lokal)
    stats: dict[str, Any]


def _normalize_spaces(value: str) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()


def _normalize_fuzzy_value(value: str) -> str:
    lowered = str(value or "").casefold()
    cleaned = _FUZZY_CLEAN_RE.sub(" ", lowered)
    return _normalize_spaces(cleaned)


def _prepare_dictionary_entities(entities: list[str] | None) -> list[str]:
    unique_entities: list[str] = []
    seen: set[str] = set()
    for raw in entities or []:
        cleaned = _normalize_spaces(raw)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique_entities.append(cleaned)
    unique_entities.sort(key=len, reverse=True)
    return unique_entities


def _build_exact_entity_pattern(entity: str) -> re.Pattern:
    tokens = [re.escape(part) for part in re.split(r"\s+", entity.strip()) if part]
    inner = r"[\s\u00A0]+".join(tokens) if tokens else re.escape(entity)
    return re.compile(
        rf"(?<![{_WORD_CHARS}]){inner}(?![{_WORD_CHARS}])",
        flags=re.IGNORECASE,
    )


def _replace_spans(text: str, spans: list[tuple[int, int]], replacement: str) -> str:
    if not spans:
        return text
    out_parts: list[str] = []
    cursor = 0
    for start, end in spans:
        if start < cursor:
            continue
        out_parts.append(text[cursor:start])
        out_parts.append(replacement)
        cursor = end
    out_parts.append(text[cursor:])
    return "".join(out_parts)


def _collect_fuzzy_spans(
    text: str,
    entity: str,
    threshold: int,
    ratio_fn,
) -> list[tuple[int, int]]:
    entity_norm = _normalize_fuzzy_value(entity)
    if not entity_norm:
        return []

    token_spans = [match.span() for match in _FUZZY_TOKEN_RE.finditer(text)]
    if not token_spans:
        return []

    entity_tokens = max(1, len(_FUZZY_TOKEN_RE.findall(entity)))
    min_window = max(1, entity_tokens - 1)
    max_window = entity_tokens + 1

    candidates: list[tuple[int, int, float]] = []
    for start_idx in range(len(token_spans)):
        for window_size in range(min_window, max_window + 1):
            end_idx = start_idx + window_size - 1
            if end_idx >= len(token_spans):
                break
            start = token_spans[start_idx][0]
            end = token_spans[end_idx][1]
            snippet = text[start:end]
            if "[" in snippet or "]" in snippet:
                continue
            score = float(ratio_fn(entity_norm, _normalize_fuzzy_value(snippet)))
            if score >= threshold:
                candidates.append((start, end, score))

    if not candidates:
        return []

    accepted: list[tuple[int, int]] = []
    occupied: list[tuple[int, int]] = []
    for start, end, score in sorted(
        candidates,
        key=lambda item: (-item[2], item[0], -(item[1] - item[0])),
    ):
        if any(not (end <= occ_start or start >= occ_end) for occ_start, occ_end in occupied):
            continue
        occupied.append((start, end))
        accepted.append((start, end))
    accepted.sort(key=lambda item: item[0])
    return accepted


def _placeholder_key(mask_type: str, value: str) -> tuple[str, str]:
    return (mask_type, _normalize_spaces(value).casefold())


def _merge_placeholder_mapping(target: dict[str, str], incoming: dict[str, str]) -> None:
    for placeholder, original in incoming.items():
        if placeholder in target and target[placeholder] != original:
            raise ValueError("Inconsistent placeholder mapping detected")
        target.setdefault(placeholder, original)


def apply_dictionary_redaction(
    text: str,
    entities: list[str],
    fuzzy: bool,
    threshold: int,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """
    Maskiert zusätzliche fall-spezifische Entities deterministisch als [DICT_n].

    Rückgabe:
    - text2
    - mapping_add: placeholder -> original
    - stats_add: ausschließlich mask-only Metadaten
    """
    if not text:
        return "", {}, {"matched_entities_count": 0, "matched_masks": [], "mask_counts": {}, "mask_types": {}}

    prepared_entities = _prepare_dictionary_entities(entities)
    if not prepared_entities:
        return text, {}, {"matched_entities_count": 0, "matched_masks": [], "mask_counts": {}, "mask_types": {}}

    out = text
    mapping_add: dict[str, str] = {}
    mask_order: list[str] = []
    mask_counts: dict[str, int] = {}
    safe_threshold = max(0, min(100, int(threshold or 92)))

    ratio_fn = None
    if fuzzy:
        try:
            from rapidfuzz import fuzz

            ratio_fn = fuzz.ratio
        except ModuleNotFoundError:
            from difflib import SequenceMatcher

            ratio_fn = lambda left, right: SequenceMatcher(None, left, right).ratio() * 100

    for idx, entity in enumerate(prepared_entities, start=1):
        mask = f"[DICT_{idx}]"
        mask_order.append(mask)
        mask_counts[mask] = 0

        exact_pattern = _build_exact_entity_pattern(entity)

        def _exact_sub(match: re.Match[str]) -> str:
            found = match.group(0)
            mapping_add.setdefault(mask, found)
            mask_counts[mask] = int(mask_counts.get(mask, 0)) + 1
            return mask

        out = exact_pattern.sub(_exact_sub, out)

        if fuzzy and ratio_fn is not None:
            fuzzy_spans = _collect_fuzzy_spans(out, entity, safe_threshold, ratio_fn)
            if fuzzy_spans:
                if mask not in mapping_add:
                    first_start, first_end = fuzzy_spans[0]
                    mapping_add[mask] = out[first_start:first_end]
                out = _replace_spans(out, fuzzy_spans, mask)
                mask_counts[mask] = int(mask_counts.get(mask, 0)) + len(fuzzy_spans)

    matched_masks = [mask for mask in mask_order if mask_counts.get(mask, 0) > 0]
    report = {
        "matched_entities_count": len(matched_masks),
        "matched_masks": matched_masks,
        "mask_counts": {mask: int(mask_counts[mask]) for mask in matched_masks},
        "mask_types": {"DICT": int(sum(mask_counts.get(mask, 0) for mask in matched_masks))},
        "fuzzy_enabled": bool(fuzzy),
        "threshold": safe_threshold,
        "entity_count": len(mapping_add),
    }
    return out, mapping_add, report


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans, key=lambda item: (item[0], item[1])):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        prev_start, prev_end = merged[-1]
        merged[-1] = (prev_start, max(prev_end, end))
    return merged


def _collect_protected_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in (_MONEY_EXCLUDE_RE, _DATE_EXCLUDE_RE, _CASE_ID_EXCLUDE_RE):
        spans.extend(match.span() for match in pattern.finditer(text))
    return _merge_spans(spans)


def _spans_overlap(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    for span_start, span_end in spans:
        if end <= span_start:
            continue
        if start >= span_end:
            continue
        return True
    return False


def _is_addr_candidate(source: str) -> bool:
    value = str(source or "").strip()
    if not value:
        return False
    if "[" in value or "]" in value:
        return False
    if _MONEY_EXCLUDE_RE.fullmatch(value):
        return False
    if _DATE_EXCLUDE_RE.fullmatch(value):
        return False
    if _CASE_ID_EXCLUDE_RE.fullmatch(value):
        return False
    if re.fullmatch(r"[\d\s.,/-]+", value):
        return False
    if _ADDR_STREET_RE.search(value):
        return True
    if _ADDR_PLZ_CITY_RE.search(value):
        return True
    return False


def _apply_regex_redaction(text: str) -> tuple[str, dict[str, str], dict[str, Any]]:
    if not text:
        return "", {}, {"mask_counts": {}, "mask_types": {}, "entity_count": 0, "placeholder_keys": []}

    out = text
    mapping_add: dict[str, str] = {}
    counters: dict[str, int] = {}
    assigned_by_value: dict[tuple[str, str], str] = {}
    mask_counts: dict[str, int] = {}

    for rule in _REGEX_RULES:
        render = rule.render or _render_full_placeholder
        protected_spans = _collect_protected_spans(out) if rule.mask_type == "ADDR" else []

        def _sub(match: re.Match[str]) -> str:
            if isinstance(rule.source_group, str):
                source = str(match.group(rule.source_group) or "")
                source_span = match.span(rule.source_group)
            else:
                source = str(match.group(rule.source_group) or "")
                source_span = match.span(rule.source_group)
            source = source.strip()
            if not source:
                return match.group(0)
            if rule.mask_type == "ADDR":
                start, end = source_span
                if _spans_overlap(start, end, protected_spans):
                    return match.group(0)
                if not _is_addr_candidate(source):
                    return match.group(0)
            # FIX 5 (P2): Optionaler Validator – z.B. für BIC-Ländercodeprüfung.
            if rule.validate is not None and not rule.validate(source):
                return match.group(0)

            value_key = _placeholder_key(rule.mask_type, source)
            placeholder = assigned_by_value.get(value_key)
            if not placeholder:
                placeholder = _new_placeholder(rule.mask_type, counters)
                assigned_by_value[value_key] = placeholder
                mapping_add[placeholder] = source
            mask_counts[placeholder] = int(mask_counts.get(placeholder, 0)) + 1
            return render(match, placeholder)

        out = rule.pattern.sub(_sub, out)

    mask_types: dict[str, int] = {}
    for placeholder, count in mask_counts.items():
        kind = _extract_mask_type(placeholder)
        mask_types[kind] = int(mask_types.get(kind, 0)) + int(count)

    stats = {
        "mask_counts": dict(sorted(mask_counts.items(), key=lambda item: item[0])),
        "mask_types": dict(sorted(mask_types.items(), key=lambda item: item[0])),
        "entity_count": len(mapping_add),
        "placeholder_keys": sorted(mask_counts.keys()),
    }
    return out, mapping_add, stats


def _policy_get(policy: Any, key: str, default: Any) -> Any:
    if isinstance(policy, dict):
        return policy.get(key, default)
    if hasattr(policy, key):
        return getattr(policy, key)
    return default


def prepare_for_llm(original_text: str, policy: Any) -> RedactionContext:
    """
    Zentrale fail-closed Vorbereitung für LLM.

    policy kann dict oder Objekt sein. Relevante Keys/Attribute:
    - dictionary_entities: list[str] | str (newline-separiert)
    - dictionary_fuzzy: bool (default False)
    - dictionary_threshold: int (default 92)
    - pseudonymize_people: bool (default True)
    """
    text = str(original_text or "")
    entities_raw = _policy_get(policy, "dictionary_entities", [])
    if isinstance(entities_raw, str):
        entities = [line.strip() for line in entities_raw.splitlines() if line.strip()]
    elif isinstance(entities_raw, list):
        entities = [str(item).strip() for item in entities_raw if str(item).strip()]
    else:
        entities = []

    fuzzy = bool(_policy_get(policy, "dictionary_fuzzy", False))
    threshold = int(_policy_get(policy, "dictionary_threshold", 92) or 92)
    use_pseudonymize = bool(_policy_get(policy, "pseudonymize_people", True))
    use_company_redaction = bool(_policy_get(policy, "redact_company_names", False))

    if not text:
        return RedactionContext(
            redacted_text="",
            mapping={},
            stats={
                "redaction_applied": False,
                "entity_count": 0,
                "mask_counts": {},
                "mask_types": {},
                "placeholder_keys": [],
                "dictionary": {
                    "matched_entities_count": 0,
                    "matched_masks": [],
                    "mask_counts": {},
                    "mask_types": {},
                    "fuzzy_enabled": fuzzy,
                    "threshold": threshold,
                    "entity_count": 0,
                },
                "regex": {"mask_counts": {}, "mask_types": {}, "entity_count": 0, "placeholder_keys": []},
                "pseudonymization": {
                    "mask_counts": {},
                    "mask_types": {},
                    "entity_count": 0,
                    "placeholder_keys": [],
                },
                "fuzzy_enabled": fuzzy,
                "fuzzy_threshold": threshold,
            },
        )

    mapping: dict[str, str] = {}
    mask_counts: dict[str, int] = {}
    mask_types: dict[str, int] = {}

    out, dict_mapping, dict_stats = apply_dictionary_redaction(
        text=text,
        entities=entities,
        fuzzy=fuzzy,
        threshold=threshold,
    )
    _merge_placeholder_mapping(mapping, dict_mapping)
    for placeholder, count in (dict_stats.get("mask_counts") or {}).items():
        mask_counts[placeholder] = int(mask_counts.get(placeholder, 0)) + int(count)
    for kind, count in (dict_stats.get("mask_types") or {}).items():
        mask_types[kind] = int(mask_types.get(kind, 0)) + int(count)

    out, regex_mapping, regex_stats = _apply_regex_redaction(out)
    _merge_placeholder_mapping(mapping, regex_mapping)
    for placeholder, count in (regex_stats.get("mask_counts") or {}).items():
        mask_counts[placeholder] = int(mask_counts.get(placeholder, 0)) + int(count)
    for kind, count in (regex_stats.get("mask_types") or {}).items():
        mask_types[kind] = int(mask_types.get(kind, 0)) + int(count)

    pseudo_stats = {"mask_counts": {}, "mask_types": {}, "entity_count": 0, "placeholder_keys": []}
    if use_pseudonymize:
        out, pseudo_mapping, pseudo_stats = _apply_pseudonymization(out)
        _merge_placeholder_mapping(mapping, pseudo_mapping)
        for placeholder, count in (pseudo_stats.get("mask_counts") or {}).items():
            mask_counts[placeholder] = int(mask_counts.get(placeholder, 0)) + int(count)
        for kind, count in (pseudo_stats.get("mask_types") or {}).items():
            mask_types[kind] = int(mask_types.get(kind, 0)) + int(count)

    company_stats = {"mask_counts": {}, "mask_types": {}, "entity_count": 0, "placeholder_keys": []}
    if use_company_redaction:
        out, company_mapping, company_stats = _apply_company_redaction(out)
        _merge_placeholder_mapping(mapping, company_mapping)
        for placeholder, count in (company_stats.get("mask_counts") or {}).items():
            mask_counts[placeholder] = int(mask_counts.get(placeholder, 0)) + int(count)
        for kind, count in (company_stats.get("mask_types") or {}).items():
            mask_types[kind] = int(mask_types.get(kind, 0)) + int(count)

    # FIX 4 (P1): Second Pass – bereinigt verbleibende Rechtsform-Suffixe
    # ("& Co. KG", "& Co. KGaA") die nach einem Platzhalter stehen.
    # Tritt auf wenn ein Unternehmensname durch eine frühere Regel nur teilweise
    # erfasst wurde (z.B. nur "GmbH" ohne den "& Co. KGaA"-Suffix).
    out = _ORPHAN_COMPANY_SUFFIX_RE.sub(r"\1", out)

    # FIX 6 (P2): Pipeline-weiter Nachname-Second-Pass – stellt sicher dass
    # alleinstehende Nachnamen auch dann maskiert werden, wenn der vollständige
    # Name durch einen früheren Pass (_apply_regex_redaction → PERSON_N) erfasst
    # wurde und pseudonymize_people() ihn daher nicht mehr sehen konnte.
    # Scannt das kombinierte Mapping nach PERSON/PARTEI-Platzhaltern, extrahiert
    # den Nachnamen und ersetzt verbleibende Klartext-Vorkommen.
    _person_mask_types_fix6 = {"PERSON", "PARTEI"}
    _surname_to_ph: dict[str, str] = {}
    for _ph, _orig in mapping.items():
        if _extract_mask_type(_ph) not in _person_mask_types_fix6:
            continue
        _name_clean = _TITLE_PREFIX_RE.sub("", _orig).strip()
        _parts = _name_clean.split()
        if len(_parts) >= 2:
            _surname = _parts[-1]
            if (
                len(_surname) >= 4
                and _surname[0].isupper()
                and _surname.lower() not in _NON_PERSON_TOKENS
                and _surname not in _surname_to_ph
            ):
                _surname_to_ph[_surname] = _ph
    for _surname, _ph in sorted(_surname_to_ph.items(), key=lambda x: -len(x[0])):
        out = re.compile(rf"\b{re.escape(_surname)}\b").sub(_ph, out)

    placeholder_keys = sorted(mask_counts.keys())
    stats = {
        "redaction_applied": bool(mask_counts),
        "entity_count": len(mapping),
        "mask_counts": dict(sorted(mask_counts.items(), key=lambda item: item[0])),
        "mask_types": dict(sorted(mask_types.items(), key=lambda item: item[0])),
        "placeholder_keys": placeholder_keys,
        "dictionary": dict_stats,
        "regex": regex_stats,
        "pseudonymization": pseudo_stats,
        "company": company_stats,
        "fuzzy_enabled": fuzzy,
        "fuzzy_threshold": threshold,
    }
    return RedactionContext(redacted_text=out, mapping=mapping, stats=stats)


def restore_from_llm(text: str, mapping: dict[str, str]) -> str:
    """
    Lokale Re-Identification: Platzhalter -> Original.
    """
    out = str(text or "")
    if not out or not mapping:
        return out

    def _restore_token(match: re.Match[str]) -> str:
        token = str(match.group(0) or "")
        # token is "[PARTEI_1]" but mapping keys are "PARTEI_1"
        key = token[1:-1] if token.startswith("[") and token.endswith("]") else token
        if key in mapping:
            return str(mapping[key] or "")
        if token in mapping:
            return str(mapping[token] or "")
        return token

    return _RESTORE_TOKEN_RE.sub(_restore_token, out)


def restore_payload_from_llm(payload: Any, mapping: dict[str, str]) -> Any:
    if isinstance(payload, str):
        return restore_from_llm(payload, mapping)
    if isinstance(payload, list):
        return [restore_payload_from_llm(item, mapping) for item in payload]
    if isinstance(payload, dict):
        return {key: restore_payload_from_llm(value, mapping) for key, value in payload.items()}
    return payload


def _external_mask_key(value: str) -> str:
    return _normalize_spaces(value).casefold()


def _external_org_token(value: str, state: _ExternalMaskState) -> str:
    key = _external_mask_key(value)
    if key not in state.org_by_key:
        state.org_counter += 1
        state.org_by_key[key] = f"[ORGMASK_{state.org_counter}]"
    return state.org_by_key[key]


def _external_addr_token(value: str, state: _ExternalMaskState) -> str:
    key = _external_mask_key(value)
    if key not in state.addr_by_key:
        state.addr_counter += 1
        state.addr_by_key[key] = f"[ADDRMASK_{state.addr_counter}]"
    return state.addr_by_key[key]


def _looks_like_address_line(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if "[" in value or "]" in value:
        return False
    if _MONEY_EXCLUDE_RE.search(value) or _DATE_EXCLUDE_RE.search(value) or _CASE_ID_EXCLUDE_RE.search(value):
        return False
    return bool(_ADDR_STREET_RE.search(value) and re.search(r"\d{1,4}[a-zA-Z]?", value)) or bool(
        _ADDR_PLZ_CITY_RE.search(value)
    )


def _mask_orgs_in_text(text: str, state: _ExternalMaskState, *, enabled: bool) -> str:
    if not enabled:
        return text

    def _sub(match: re.Match[str]) -> str:
        candidate = str(match.group(0) or "").strip()
        if not candidate or "[" in candidate or "]" in candidate:
            return candidate
        return _external_org_token(candidate, state)

    return _EXTERNAL_ORG_RE.sub(_sub, text)


def _mask_external_line(line: str, state: _ExternalMaskState) -> str:
    raw_line = str(line or "")
    if not raw_line.strip():
        return raw_line

    if ":" in raw_line:
        prefix, suffix = raw_line.split(":", 1)
        prefix_clean = prefix.strip()
        suffix_clean = suffix.strip()
        in_party_meta_context = bool(_EXTERNAL_PARTY_META_LINE_RE.match(prefix_clean))
        masked_suffix = _mask_orgs_in_text(suffix_clean, state, enabled=in_party_meta_context)
        if _looks_like_address_line(masked_suffix):
            masked_suffix = _external_addr_token(masked_suffix, state)
        return f"{prefix_clean}: {masked_suffix}"

    in_party_meta_context = bool(_EXTERNAL_PARTY_META_LINE_RE.match(raw_line))
    masked_line = _mask_orgs_in_text(raw_line, state, enabled=in_party_meta_context)
    if _looks_like_address_line(masked_line):
        return _external_addr_token(masked_line, state)
    return masked_line


def apply_strict_external_mask(payload: Any) -> Any:
    """
    Zusätzliche Härtung für externe Ausgabe:
    - Organisationsnamen in Parteien/Metadaten-Zeilen maskieren
    - komplette Anschriftzeilen maskieren
    - Quelle-Zeilen entfernen (können Adressfragmente neben Platzhaltern enthalten)
    - Klarnamen und Hausnummern direkt nach Platzhaltern entfernen
    """
    state = _ExternalMaskState()

    def _walk(value: Any) -> Any:
        if isinstance(value, str):
            lines = []
            for line in value.splitlines():
                if _EXTERNAL_SOURCE_LINE_RE.match(line):
                    continue  # Quelle-Zeilen im external-mode entfernen
                lines.append(_mask_external_line(line, state))
            result = "\n".join(lines)
            result = _EXT_NAME_AFTER_PLACEHOLDER_RE.sub(r'\1', result)
            result = _EXT_HAUSNR_AFTER_PLACEHOLDER_RE.sub(r'\1', result)
            return result
        if isinstance(value, list):
            return [_walk(item) for item in value]
        if isinstance(value, dict):
            return {key: _walk(item) for key, item in value.items()}
        return value

    return _walk(payload)


# ---------------------------------------------------------------------------
# Post-Redaction PII-Validator (fail-closed für Mandantenexport)
# ---------------------------------------------------------------------------


def _strip_placeholders(text: str) -> str:
    """Entfernt alle bekannten Redaction-Platzhalter vor PII-Validierung.
    Verhindert False Positives durch Zahlenfolgen in Platzhaltern."""
    return _VALIDATOR_PLACEHOLDER_PATTERN.sub("", text)


class RedactionLeakError(Exception):
    """Wird geworfen wenn nach Redaction noch PII erkannt wird."""

    def __init__(self, leak_count: int):
        self.leak_count = leak_count
        super().__init__(
            f"Redaction unvollständig: {leak_count} mögliche PII-Treffer. "
            f"Export gesperrt."
        )


def validate_mandantenexport(text: str) -> None:
    """Prüft ob Mandantenexport noch PII enthält.
    Wirft RedactionLeakError wenn ja.
    WICHTIG: Leak-Inhalte werden nie geloggt (kein PII in Logs)."""
    # Platzhalter entfernen vor Check (verhindert False Positives durch
    # Zahlenfolgen in [ADDR_x], [BIC_x] etc.)
    clean_text = _strip_placeholders(text)
    leak_count = 0
    for pattern, name in zip(_POST_REDACTION_PII_PATTERNS, _POST_REDACTION_PII_PATTERN_NAMES):
        matches = pattern.findall(clean_text)
        leak_count += len(matches)
    if leak_count > 0:
        raise RedactionLeakError(leak_count=leak_count)


def redact(
    text: str,
    entities: list[str] | None = None,
    fuzzy: bool = False,
    threshold: int = 92,
    validate: bool = False,
) -> str:
    """Kompatibilitäts-API: gibt redacted text zurück (ohne Re-Identification)."""
    ctx = prepare_for_llm(
        text,
        {
            "dictionary_entities": entities or [],
            "dictionary_fuzzy": bool(fuzzy),
            "dictionary_threshold": int(threshold),
            "pseudonymize_people": False,
        },
    )
    result = ctx.redacted_text
    if validate:
        validate_mandantenexport(result)
    return result


def redact_with_preview(
    text: str,
    entities: list[str] | None = None,
    fuzzy: bool = False,
    threshold: int = 92,
    validate: bool = False,
) -> tuple[str, list[dict], dict]:
    """
    Kompatibilitäts-API mit mask-only Findings (kein Klartext).

    Rückgabe: (anonymisierter_text, findings, dictionary_report)
    findings: Liste von {"replacement": "[MASK_x]", "placeholder": "[MASK_x]"}
    """
    ctx = prepare_for_llm(
        text,
        {
            "dictionary_entities": entities or [],
            "dictionary_fuzzy": bool(fuzzy),
            "dictionary_threshold": int(threshold),
            "pseudonymize_people": False,
        },
    )
    findings: list[dict[str, str]] = []
    for placeholder, count in (ctx.stats.get("mask_counts") or {}).items():
        for _ in range(int(count)):
            findings.append({"replacement": placeholder, "placeholder": placeholder})
    dictionary_report = dict(ctx.stats.get("dictionary") or {})
    dictionary_report.setdefault("matched_entities_count", 0)
    dictionary_report.setdefault("matched_masks", [])
    dictionary_report.setdefault("mask_counts", {})
    if validate:
        validate_mandantenexport(ctx.redacted_text)
    return ctx.redacted_text, findings, dictionary_report
