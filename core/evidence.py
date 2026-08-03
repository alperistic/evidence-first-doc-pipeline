"""
core/evidence.py – Facade: re-exports party_extractor + evidence_map + guardrails
Extracted from llm.py (Phase 1, Step 2 – Strangler Fig).
"""

import re
from typing import Any

from core.models import (
    _normalize_ws,
    clean_snippet,
)
from core.snippet import (
    _normalize_for_match,
    _to_short_citation_quote,
    _clip_quote_200,
)
from core.citation import (
    _normalize_item,
    _coerce_non_negative_int,
    summarize_citation_verification,
)
from core.party_extractor import (  # noqa: F401 – re-export
    _PARTY_ROLE_HINTS,
    _STOPWORD_ENTITIES,
    _RHETORIC_PATTERNS,
    _ARTIFACT_VALUES,
    _DOC_TYPE_HEADER_KEYWORDS,
    _ORG_SUFFIX_RE,
    _PERSON_RE,
    _is_doc_type_header,
    _is_artifact_value,
    _dedupe_str_keep_order,
    _looks_like_person,
    _looks_like_org,
    _is_rhetoric_value,
    _extract_party_candidates,
    _extract_party_values_from_text,
    _collect_structured_party_evidence,
    _collect_regex_party_evidence,
    ensure_party_evidence_consistency,
)
from core.evidence_map import (  # noqa: F401 – re-export
    MIN_BETRAG_CONFIDENCE,
    _REPEATING_EDGE_PAGE_RE,
    _REPEATING_EDGE_RATIO_RE,
    _REPEATING_EDGE_DATE_RE,
    _REPEATING_EDGE_TIME_RE,
    _REQUIRED_EVIDENCE_FIELDS,
    _EVIDENCE_TOPK_BY_FIELD,
    _DEADLINE_DURATION_RE,
    _KUENDIGUNG_WORD_RE,
    _YEAR_AMOUNT_RE,
    _FORBIDDEN_REPLACEMENTS,
    _normalize_repeating_edge_line,
    strip_repeating_header_footer_lines,
    _searchable_pages_without_repeating_edges,
    _is_year_like_amount,
    _neutralize_legal_tone,
    _get_page_no,
    _dedupe_keep_order,
    _normalize_evidence_item,
    _dedupe_evidence,
    _select_top_evidence,
    _normalize_evidence_map,
    build_evidence_map,
    build_evidence_context,
    attach_evidence_map_to_parsed,
)

# ---------------------------------------------------------------------------
# Guardrail constants (remain in the facade)
# ---------------------------------------------------------------------------

_LEGAL_GUARD_REPLACEMENTS: list[tuple[str, str]] = [
    (r"\bschl[üu]ssig\b", "zu prüfen / Abgleich erforderlich"),
    (r"\bausreichend\b", "zu prüfen / Abgleich erforderlich"),
    (r"\berschweren\b", "offen / unklar / Rückfrage"),
    (r"\bbelegen\s+k[oö]nnen\b", "im Dokument genannt / Beweismittel genannt"),
    (r"\bproblematisch\b", "zur Prüfung markieren"),
    (r"\bung[eö]w[oö]hnlich\s+streng\b", "zur Prüfung markieren"),
    (r"\bbelastung\b", "im Dokument genannt"),
    (r"\berfolgsaussicht(?:en)?\b", "offen / unklar / Rückfrage"),
    (r"\baussichten?\b", "offen / unklar / Rückfrage"),
    (r"\bdurchsetzbar\b", "offen / unklar / Rückfrage"),
]
_GENERIC_DOC_TYPE = "generisch"
_DOC_TYPE_ALLOWED_TOPICS: dict[str, set[str]] = {
    "mietvertrag": {
        "parteien", "betrag", "miete", "nebenkosten", "kaution", "mietbeginn",
        "kuendigung", "tierhaltung", "schoenheitsreparaturen", "nebenkostenabrechnung",
        "fristen_termine",
    },
    "arbeitsvertrag": {
        "parteien", "betrag", "datum_beginn", "gehalt", "arbeitszeit", "urlaub",
        "probezeit", "kuendigung", "ueberstunden", "geheimhaltung", "wettbewerb",
        "fristen_termine",
    },
    "klageschrift": {
        "parteien", "betrag", "antraege", "sachverhalt", "beweismittel", "fristen_termine",
    },
    "mahnung": {
        "parteien", "betrag", "fristsetzung", "aktenzeichen", "rechnungsnummer", "fristen_termine",
    },
    _GENERIC_DOC_TYPE: {
        "parteien", "betrag", "fristen_termine", "datum_beginn", "kuendigung",
        "aktenzeichen", "beweismittel",
    },
}
_TOPIC_PATTERNS: dict[str, tuple[str, ...]] = {
    "parteien": ("partei", "kläger", "klaeger", "beklag", "vermieter", "mieter", "arbeitgeber", "arbeitnehmer"),
    "miete": ("miete", "kaltmiete", "warmmiete"),
    "nebenkosten": ("nebenkosten", "betriebskosten"),
    "kaution": ("kaution",),
    "mietbeginn": ("mietbeginn",),
    "kuendigung": ("kündigung", "kuendigung", "kündigungsfrist", "kuendigungsfrist"),
    "tierhaltung": ("tierhaltung", "haustier"),
    "schoenheitsreparaturen": ("schönheitsreparatur", "schoenheitsreparatur"),
    "nebenkostenabrechnung": ("nebenkostenabrechnung",),
    "datum_beginn": ("beginn", "beginn-datum", "vertragsbeginn"),
    "gehalt": ("gehalt", "vergütung", "verguetung", "lohn"),
    "arbeitszeit": ("arbeitszeit",),
    "urlaub": ("urlaub",),
    "probezeit": ("probezeit",),
    "ueberstunden": ("überstunden", "ueberstunden", "mehrarbeit"),
    "geheimhaltung": ("geheimhaltung", "vertraulichkeit"),
    "wettbewerb": ("wettbewerb", "wettbewerbsverbot", "konkurrenz"),
    "betrag": ("betrag", "eur", "euro", "€", "forderung", "summe"),
    "antraege": ("antrag", "anträge", "antraege"),
    "sachverhalt": ("sachverhalt",),
    "beweismittel": ("beweis", "beweismittel", "zeug", "urkunde"),
    "fristsetzung": ("fristsetzung", "frist"),
    "aktenzeichen": ("aktenzeichen",),
    "rechnungsnummer": ("rechnungsnummer", "rechnung"),
    "fristen_termine": ("frist", "termin", "fällig", "faellig", "spätestens", "spaetestens"),
}
_LEAK_TERMS_BY_DOC_TYPE: dict[str, tuple[str, ...]] = {
    "arbeitsvertrag": ("mieter", "nebenkosten", "schönheitsreparaturen", "schoenheitsreparaturen", "kaution")
}

# ---------------------------------------------------------------------------
# Guardrail functions
# ---------------------------------------------------------------------------


def _find_evidence_for_text(text: str, evidence_map: list[dict[str, Any]], field: str | None = None) -> list[dict[str, Any]]:
    text_norm = _normalize_for_match(text)
    text_tokens = set(text_norm.split())
    matches: list[dict[str, Any]] = []

    for ev in evidence_map:
        if field and ev.get("field") != field:
            continue
        if ev.get("value") == "missing":
            continue
        quote = str(ev.get("quote", "") or "")
        quote_norm = _normalize_for_match(quote)
        overlap = text_tokens & set(quote_norm.split())
        needed = 1 if len(text_tokens) <= 4 else 2
        if len(overlap) >= needed:
            matches.append(ev)

    return matches[:3]


def apply_evidence_guardrails(
    parsed: dict,
    evidence_map: list[dict[str, Any]],
    item_fields: tuple[str, ...],
) -> dict:
    """
    Fills in missing citations from the evidence map and neutrally flags unsupported claims.
    """
    out = dict(parsed or {})
    out = attach_evidence_map_to_parsed(out, evidence_map)

    for field in item_fields:
        items = out.get(field, [])
        if not isinstance(items, list):
            continue
        normalized_items: list[dict[str, Any]] = []
        for raw in items:
            item = _normalize_item(raw)
            text = str(item.get("text", "") or "")
            citations = item.get("citations", []) if isinstance(item.get("citations"), list) else []
            if not citations and text:
                ev_hits = _find_evidence_for_text(text, evidence_map)
                if ev_hits:
                    citations = [
                        {
                            "page_number": h.get("page"),
                            "quote_snippet": _to_short_citation_quote(str(h.get("quote", ""))),
                            "section_hint": f"evidence:{h.get('field')}",
                        }
                        for h in ev_hits
                        if h.get("page") is not None
                    ]
            item["citations"] = citations
            if citations:
                item["citation_status"] = item.get("citation_status", "verified")
            else:
                item["citation_status"] = "unverified"
                if text and not re.search(r"\b(unverified|missing)\b", text, flags=re.IGNORECASE):
                    item["text"] = f"{text} (unverified - Rückfrage erforderlich)"
                if "question" in item:
                    question = str(item.get("question", "") or "").strip()
                    if "beleg fehlt" not in question.lower():
                        item["question"] = (
                            f"{question} | Beleg fehlt: Bitte Fundstelle im Segment benennen."
                            if question
                            else "Beleg fehlt: Bitte Fundstelle im Segment benennen."
                        )
            normalized_items.append(item)
        out[field] = normalized_items

    missing_info = [str(x).strip() for x in list(out.get("missing_info", []) or []) if str(x).strip()]
    required_missing_followups = {
        "parteien": "Rückfrage: Parteienangabe im Segment nicht eindeutig belegbar (fehlende Angabe).",
        "betraege": "Rückfrage: Betrag im Segment nicht eindeutig belegbar (fehlende Angabe).",
        "fristen_termine": "Rückfrage: Frist/Termin im Segment nicht eindeutig belegbar (fehlende Angabe).",
    }
    for ev in evidence_map:
        field = str(ev.get("field", "") or "")
        value = str(ev.get("value", "") or "")
        if value != "missing":
            continue
        follow_up = required_missing_followups.get(field)
        if follow_up:
            missing_info.append(follow_up)
    out["missing_info"] = _dedupe_keep_order(missing_info)[:30]
    return out


def _resolve_doc_type_key(parsed: dict[str, Any], doc_type_hint: str | None = None) -> str | None:
    joined = " ".join(
        [
            _normalize_ws(str(doc_type_hint or "")),
            _normalize_ws(str(parsed.get("dokumenttyp_parteien", ""))),
        ]
    ).lower()
    if "mietvertrag" in joined:
        return "mietvertrag"
    if "arbeitsvertrag" in joined:
        return "arbeitsvertrag"
    if "klageschrift" in joined:
        return "klageschrift"
    if "mahnung" in joined or "mahnschreiben" in joined:
        return "mahnung"
    return None


def _extract_topics_from_text(text: str) -> set[str]:
    text_l = _normalize_for_match(text)
    topics: set[str] = set()
    for topic, tokens in _TOPIC_PATTERNS.items():
        if any(token in text_l for token in tokens):
            topics.add(topic)
    return topics


def _is_allowed_for_doc_type(text: str, doc_type_key: str) -> bool:
    topics = _extract_topics_from_text(text)
    if not topics:
        return True
    allowed = _DOC_TYPE_ALLOWED_TOPICS.get(doc_type_key, _DOC_TYPE_ALLOWED_TOPICS[_GENERIC_DOC_TYPE])
    return bool(topics & allowed)


def apply_doc_type_consistency_gate(parsed: dict, doc_type_hint: str | None = None) -> dict:
    """
    Flags possible cross-document leaks and moves them from facts into
    ambiguities/follow-up questions.
    """
    out = dict(parsed or {})
    doc_type_key = _resolve_doc_type_key(out, doc_type_hint=doc_type_hint)
    leak_terms = _LEAK_TERMS_BY_DOC_TYPE.get(doc_type_key or "")
    if not leak_terms:
        return out

    unklarheiten = list(out.get("auffaelligkeiten", []) or [])
    rueckfragen = list(out.get("prueffragen", []) or [])

    def _find_leak_term(text: str) -> str | None:
        text_l = text.lower()
        for term in leak_terms:
            if re.search(rf"\b{re.escape(term)}\b", text_l):
                return term
        return None

    kernaussagen_neu: list[dict[str, Any]] = []
    for raw in list(out.get("kernaussagen", []) or []):
        item = _normalize_item(raw)
        text = str(item.get("text", "") or "")
        leak_term = _find_leak_term(text)
        if not leak_term:
            kernaussagen_neu.append(item)
            continue
        unklarheiten.append(
            {
                "text": f"Hinweis: mögliche Segment-Vermischung ({leak_term}) – zu prüfen.",
                "citations": [],
                "citation_status": "unverified",
                "leak_suspected": True,
            }
        )
        rueckfragen.append(
            f"Rückfrage: Begriff '{leak_term}' passt nicht zum Segmenttyp {doc_type_key}; Segmentquelle zu prüfen."
        )
    out["kernaussagen"] = kernaussagen_neu

    findings_neu: list[dict[str, Any]] = []
    for raw in list(out.get("top3_findings", []) or []):
        finding = dict(raw) if isinstance(raw, dict) else {"title": str(raw)}
        text = str(finding.get("title", "") or "")
        leak_term = _find_leak_term(text)
        if not leak_term:
            findings_neu.append(finding)
            continue
        unklarheiten.append(
            {
                "text": f"Hinweis: mögliche Segment-Vermischung ({leak_term}) – zu prüfen.",
                "citations": [],
                "citation_status": "unverified",
                "leak_suspected": True,
            }
        )
        rueckfragen.append(
            f"Rückfrage: Auffälligkeit mit '{leak_term}' wirkt segmentfremd; Zuordnung zu prüfen."
        )
    out["top3_findings"] = findings_neu
    out["auffaelligkeiten"] = unklarheiten
    out["prueffragen"] = _dedupe_keep_order([str(x).strip() for x in rueckfragen if str(x).strip()])[:30]
    return out


def finalize_evidence_first_output(parsed: dict, item_fields: tuple[str, ...]) -> dict:
    out = dict(parsed or {})
    follow_up = list(out.get("prueffragen", []) or [])
    missing = list(out.get("missing_info", []) or [])

    for field in item_fields:
        items = out.get(field, [])
        if not isinstance(items, list):
            continue
        verified_items: list[dict[str, Any]] = []
        unverified_items: list[dict[str, Any]] = []
        for raw in items:
            item = _normalize_item(raw)
            citations = item.get("citations", []) if isinstance(item.get("citations"), list) else []
            if citations:
                item["citation_status"] = "verified"
                verified_items.append(item)
            else:
                item["citation_status"] = "unverified"
                unverified_items.append(item)

        # Evidence-first: if supported claims exist, move unsupported claims out of core fields.
        if verified_items and unverified_items:
            for item in unverified_items:
                claim_text = str(item.get("text", "") or item.get("title", "")).strip()
                if claim_text:
                    follow_up.append(f"Rückfrage ({field}): {claim_text} – Beleg fehlt im Segment.")
            out[field] = verified_items
        elif not verified_items and unverified_items:
            # If nothing is verified, keep only a single neutral note.
            kept = dict(unverified_items[0])
            text = str(kept.get("text", "") or kept.get("title", "")).strip()
            if text and "unverified" not in text.lower():
                kept["text"] = f"{text} (unverified - Rückfrage erforderlich)"
            out[field] = [kept]
            for dropped in unverified_items[1:]:
                claim_text = str(dropped.get("text", "") or dropped.get("title", "")).strip()
                if claim_text:
                    follow_up.append(f"Rückfrage ({field}): {claim_text} – Beleg fehlt im Segment.")
        else:
            out[field] = verified_items

    out["prueffragen"] = _dedupe_keep_order([str(x).strip() for x in follow_up if str(x).strip()])[:20]
    out["missing_info"] = _dedupe_keep_order([str(x).strip() for x in missing if str(x).strip()])[:20]

    citation_summary = summarize_citation_verification(out, item_fields)
    out["citation_status"] = citation_summary["citation_status"]
    out["citation_metrics"] = citation_summary["citation_metrics"]
    out["citation_check_ran"] = citation_summary["citation_check_ran"]
    return _neutralize_legal_tone(out)


def _apply_legal_guard_text(text: str) -> str:
    out = str(text or "")
    for pattern, replacement in _LEGAL_GUARD_REPLACEMENTS:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    out = re.sub(r"\b(?:zu prüfen|pruefen)\b", "zur Prüfung markieren", out, flags=re.IGNORECASE)
    out = re.sub(r"\bangabe fehlt\b", "Angabe fehlt/mehrdeutig - Rückfrage erforderlich", out, flags=re.IGNORECASE)
    return _normalize_ws(out)


def apply_no_legal_advice_guard(
    parsed: dict[str, Any],
    *,
    doc_type_hint: str | None = None,
) -> dict[str, Any]:
    """
    Neutralizes evaluative legal-advice-style phrasing in to-dos/ambiguities/
    follow-up questions before UI rendering and export.
    """
    out = dict(parsed or {})
    doc_type_key = _resolve_doc_type_key(out, doc_type_hint=doc_type_hint) or _GENERIC_DOC_TYPE

    for key in ("todos", "missing_info", "prueffragen", "unklarheiten"):
        raw_values = out.get(key, [])
        if not isinstance(raw_values, list):
            continue
        sanitized: list[str] = []
        for item in raw_values:
            text = _apply_legal_guard_text(str(item))
            if not text:
                continue
            if not _is_allowed_for_doc_type(text, doc_type_key):
                continue
            sanitized.append(text)
        out[key] = _dedupe_keep_order([item for item in sanitized if item])[:30]

    auffaelligkeiten_raw = out.get("auffaelligkeiten", [])
    if isinstance(auffaelligkeiten_raw, list):
        auffaelligkeiten_sanitized: list[dict[str, Any]] = []
        for raw in auffaelligkeiten_raw:
            item = _normalize_item(raw)
            text = _apply_legal_guard_text(str(item.get("text", "") or ""))
            if not text:
                continue
            if not _is_allowed_for_doc_type(text, doc_type_key):
                continue
            item["text"] = text
            auffaelligkeiten_sanitized.append(item)
        out["auffaelligkeiten"] = auffaelligkeiten_sanitized

    return out
