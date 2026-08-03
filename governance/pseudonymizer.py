"""governance/pseudonymizer.py – Personen- und Firmen-Pseudonymisierung.

Extrahiert aus governance/redaction.py (W2 God-File-Split).
Enthält pseudonymize_people(), _apply_pseudonymization(),
_apply_company_redaction(), apply_pseudonym_map() und alle
zugehörigen Hilfsfunktionen.
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
# Hilfsfunktionen
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
    # Stoplist: bekannte juristische Begriffe / Boilerplate
    if normalized.casefold() in _PERSON_STOPLIST:
        return False
    # Zeilen die nur aus Großbuchstaben/Ziffern/Sonderzeichen bestehen → kein Name
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
    """Prüft ob (start, end) mit irgendeinem bereits reservierten Span überlappt."""
    return any(not (end <= s or start >= e) for s, e in seen_spans)

# ---------------------------------------------------------------------------
# Hauptfunktionen
# ---------------------------------------------------------------------------


def pseudonymize_people(text: str, max_labels: int = 10) -> tuple[str, dict[str, str]]:
    """
    Ersetzt erkannte Personennamen deterministisch durch Pseudonyme.

    Rückgabe: (pseudonymisierter_text, mapping_original_auf_label)
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

    # P1-A / FIX 1: ORG-Counter und -Map vorab initialisieren, damit sowohl
    # die _PARTY_PERSON_RE-Schleife (bei ORG-Suffix-Erkennung) als auch die
    # spätere Standalone-ORG-Schleife denselben Counter verwenden.
    _existing_org_nums = [int(m.group(1)) for m in re.finditer(r"\[ORG_(\d+)\]", text)]
    _org_counter: int = max(_existing_org_nums) if _existing_org_nums else 0
    _org_key_to_label: dict[str, str] = {}

    # PR11 P9: Firmennamen nach Rollenbezeichnung zuerst scannen, damit der
    # vollständige Firmenname (inkl. GmbH-Suffix) den partiellen Person-Match
    # (nur 2 Wörter via _PARTY_PERSON_RE) via Overlap-Schutz verhindert.
    # WICHTIG: _assign_label() wird erst NACH dem Overlap-Check aufgerufen,
    # damit kein Label-Slot für geskippte Partial-Matches verbraucht wird.
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
        # P1-A: Prüfen ob nach dem gematchten Namen ein Rechtsform-Suffix folgt
        # → wenn ja, ist es ein Firmenname (ORG), nicht eine Person.
        suffix_match = _TRAILING_ORG_SUFFIX_RE.match(text[name_end:])
        if suffix_match:
            # Span auf den vollständigen Firmennamen inkl. Suffix erweitern
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

    # Bug 1 (P1): Standalone-ORG-Detection – Unternehmensname OHNE Rollenkontext.
    # Läuft nach rollenbasierter Erkennung (_PARTY_COMPANY_RE / _PARTY_PERSON_RE),
    # damit role-context-Firmen ihre [PARTEI_N]-Labels behalten. Läuft VOR
    # _PERSON_CANDIDATE_RE, damit kein Teilname als PERSON maskiert wird und
    # der Rechtsform-Suffix ("GmbH & Co. KG") im Klartext bleibt.
    # _org_counter / _org_key_to_label wurden oben vor der _PARTY_PERSON_RE-Schleife
    # initialisiert (P1-A), damit beide Schleifen denselben Counter verwenden.
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
        # P1-A: Auch bei kontextfreien Kandidaten auf Rechtsform-Suffix prüfen
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
    # Längere Spans haben Vorrang bei gleichem Startpunkt (Safety-Netz).
    for start, end, label in sorted(replacements, key=lambda item: (item[0], -item[1])):
        if start < cursor:
            continue
        out_parts.append(text[cursor:start])
        out_parts.append(label)
        cursor = end
    out_parts.append(text[cursor:])
    out = "".join(out_parts)

    # FIX 6 (P2): Zweiter Durchgang – maskiert alleinstehende Nachnamen wenn
    # der vollständige Name bereits im Dokument erfasst wurde.
    # Beispiel: "Kevin Stark" → [PARTEI_1]; danach bleibt "Stark" allein im
    # Text ungemaskiert. Zweiter Pass ersetzt es mit demselben Platzhalter.
    # Nur Nachnamen mit ≥4 Zeichen, Großschreibung und kein Stoppwort.
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
    """Pseudonymisiert Firmennamen als [FIRMA_N]."""
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
    Wendet ein bestehendes Pseudonym-Mapping auf beliebigen Text an.
    Erwartet mapping: original -> placeholder.
    """
    if not text or not mapping:
        return text
    out = text
    for original, label in sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True):
        if not original:
            continue
        out = out.replace(original, label)
    return out
