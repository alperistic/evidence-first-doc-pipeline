"""
core/export_render.py – Rendering-Logik für Zusammenfassung und Aktennotiz.

Enthält: Text-Rendering, Payload-Transformation, Citation-Aggregation,
apply_output_mode, alle Segment-Render-Funktionen.

Formatspezifischer Export (PDF, DOCX) liegt in core/export_format.py.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Literal

from core.citation import _normalize_item, summarize_citation_verification  # noqa: F401 – re-exported for shim compat
from core.config import get_config
from core.logging_config import get_logger

_log = get_logger(__name__)
from core.export_content import build_export_content  # noqa: F401 – re-exported for shim compat
from core.labels import (  # noqa: F401 – re-exported for shim compat
    _SUMMARY_LABELS,
    _collect_all_kaution_amounts,
    _dedupe_summary_items,
    _human_label_for_evidence,
    _norm_amount_canonical,
    _postprocess_eckdaten_items,
    _should_suppress_party_list,
    _summary_doc_type_key,
)
from core.models import _normalize_ws, clean_snippet  # noqa: F401 – re-exported for shim compat
from core.snippet import _GERMAN_MONTH_DATE_RE
from core.evidence import _is_doc_type_header
from governance.doc_class_policy import resolve_segment_header_document_type
from governance.redaction import apply_strict_external_mask, restore_from_llm

_EVIDENCE_MARKERS = ("zitat", "quelle", "beleg", "snippet", "citation", "source")
# Generische Platzhalter-Todos aus den Prompt-Schema-Beispielen — werden in der
# Ausgabe unterdrückt wenn das LLM sie unverändert zurückgibt.
_GENERIC_TODO_FRAGMENTS: tuple[str, ...] = (
    "frist aus dem dokument prüfen und ggf. notieren",
    "frist aus dem dokument notieren",
)


def _is_generic_todo(item: str) -> bool:
    """True wenn der Todo-Text ein unveränderter Prompt-Platzhalter ist."""
    item_l = item.lower()
    return any(f in item_l for f in _GENERIC_TODO_FRAGMENTS)
# Felder die nie in der Eckdaten-Ausgabe erscheinen sollen (z.B. unklare Beträge)
_SUPPRESSED_EVIDENCE_FIELDS = frozenset({"betraege_unklar"})
# BLOCKER 1: Fristen-Feld darf nur ISO-Daten oder den Fallback-String enthalten.
_FRISTEN_FIELDS = frozenset({"fristen_termine", "kuendigung_fristen", "datum_beginn"})
_FRISTEN_DATE_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")
_FRISTEN_FALLBACK = "nicht genannt (Rückfrage erforderlich)"
_EVIDENCE_CONTAINER_KEYS = ("citations", "citation_candidates", "evidence_map")
_PARTIES_SOURCE_TRACE_EMITTED: set[str] = set()
# P1-C: Sensible Daten (IBAN, BIC) in Quellentext-Snippets maskieren – auch in der Kanzleiakte.
_SNIPPET_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[\s\-]*\d){10,30}\b")
_SNIPPET_BIC_RE = re.compile(r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b")
_SNIPPET_MAX_DISPLAY_CHARS = 120


def _is_evidence_token(value: str) -> bool:
    norm = str(value or "").strip().lower()
    if not norm:
        return False
    return any(marker in norm for marker in _EVIDENCE_MARKERS)


def _is_evidence_key(key: str) -> bool:
    norm = str(key or "").strip().lower()
    if not norm:
        return False
    if norm in _EVIDENCE_CONTAINER_KEYS:
        return True
    return _is_evidence_token(norm)


def _transform_output_payload(
    payload: Any,
    mapping: dict[str, str],
    mode: Literal["internal", "external"],
    restore_evidence: bool,
    in_evidence_context: bool = False,
) -> Any:
    should_restore = mode == "internal"

    if isinstance(payload, str):
        if not should_restore:
            return payload
        if in_evidence_context and not restore_evidence:
            return payload
        return restore_from_llm(payload, mapping)

    if isinstance(payload, list):
        return [
            _transform_output_payload(
                item,
                mapping,
                mode,
                restore_evidence=restore_evidence,
                in_evidence_context=in_evidence_context,
            )
            for item in payload
        ]

    if isinstance(payload, dict):
        transformed: dict[str, Any] = {}
        for key, value in payload.items():
            key_norm = str(key or "")
            child_in_evidence_context = in_evidence_context or _is_evidence_key(key_norm)
            if str(key_norm).strip().lower() == "field" and isinstance(value, str) and _is_evidence_token(value):
                child_in_evidence_context = True
            transformed[key] = _transform_output_payload(
                value,
                mapping,
                mode,
                restore_evidence=restore_evidence,
                in_evidence_context=child_in_evidence_context,
            )
        return transformed
    return payload


def apply_output_mode(
    payload: Any,
    mapping: dict[str, str],
    mode: Literal["internal", "external"],
    restore_evidence: bool = False,
    strict_external_mask: bool = True,
) -> Any:
    """
    Export-Modus-Transformation für Kanzleiakte (internal) und Weitergabe (external).

    internal:
      - Restore für Summary/Aktennotiz-Inhalte
      - Evidence-Felder bleiben redacted, außer restore_evidence=True
    external:
      - Kein Restore, Payload bleibt redacted
    """
    if mode not in {"internal", "external"}:
        raise ValueError("mode must be 'internal' or 'external'")
    transformed = _transform_output_payload(
        payload,
        mapping or {},
        mode,
        restore_evidence=bool(restore_evidence),
        in_evidence_context=False,
    )
    if mode == "external" and strict_external_mask:
        return apply_strict_external_mask(transformed)
    return transformed


# ---------------------------------------------------------------------------
# Gemeinsame Hilfsfunktionen
# ---------------------------------------------------------------------------

def _parse_lines(summary: str):
    """
    Zerlegt den Summary-Text in typisierte Zeilen.
    Yields (type, text): type in {'header', 'bullet', 'text', 'empty'}
    """
    for line in summary.splitlines():
        stripped = line.strip()
        if not stripped:
            yield "empty", ""
        elif re.match(r"^\d+\.\s+\S", stripped):
            yield "header", _strip_md(stripped)
        elif re.match(r"^#{1,3}\s+\S", stripped):
            yield "header", _strip_md(re.sub(r"^#{1,3}\s+", "", stripped))
        elif stripped[:1] in ("–", "•") or (
            stripped[:1] == "-" and len(stripped) > 2 and stripped[1] == " "
        ):
            yield "bullet", _strip_md(stripped)
        elif re.match(r"^quelle\s+s\.\s*\S+\s*:", stripped, flags=re.IGNORECASE):
            yield "source", _strip_md(stripped)
        else:
            yield "text", _strip_md(stripped)


def _strip_md(text: str) -> str:
    """Entfernt einfache Markdown-Formatierungen (Bold, Italic)."""
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    return text


def _citation_suffix(citations: list[dict]) -> str:
    parts: list[str] = []
    for c in citations or []:
        page = c.get("page_number", "?")
        snippet = clean_snippet(str(c.get("quote_snippet", "") or ""), max_chars=180)
        if snippet:
            parts.append(f"(S. {page}: {snippet})")
    return f" {' '.join(parts)}" if parts else ""


def _normalize_display_text(text: str) -> str:
    out = _normalize_ws(text)
    out = re.sub(r"(?i)^fristen[_\s-]*termine\s*:\s*", "Frist/Termin: ", out)
    out = re.sub(
        r"(?i)\bunverified\b(?:\s*-\s*rückfrage erforderlich)?",
        "Rückfrage (Beleg im Segment nicht vorhanden)",
        out,
    )
    return out


def _short_source_excerpt(text: str, max_chars: int = 160) -> str:
    excerpt = _normalize_display_text(text)
    if len(excerpt) <= max_chars:
        return excerpt
    if max_chars <= 1:
        return "…"
    return excerpt[: max_chars - 1].rstrip() + "…"


def _document_meta_line(coverage: dict | None) -> str:
    pages_extracted = _safe_int((coverage or {}).get("pages_extracted")) or 0
    return f"Dokument: {pages_extracted} Seiten (extrahiert)"


def _normalize_pdf_missing_line(text: str) -> str:
    line = _normalize_display_text(text)
    if not line:
        return line
    line = re.sub(
        r"(?i)\bVertreter\s+Beklagter\s*:\s*missing\b",
        "Vertreter Beklagter: nicht genannt",
        line,
    )
    if re.search(
        r"(?i)\b(klägerin|klaegerin|kläger|klaeger|beklagter|beklagte)\s*:\s*missing\b",
        line,
    ):
        return line
    if re.match(r"^\s*\d+\.\s+", line) or re.match(r"^\s*[A-Z]\s+", line):
        return line
    if line.lstrip().startswith("– ") or ":" in line:
        line = re.sub(r"(?i)\bmissing\b", "nicht genannt", line)
    return line


def _display_citation_status(status: str | None) -> str:
    norm = _normalize_ws(status or "").lower()
    if norm == "verified":
        return "verified"
    return "nicht verifiziert"


def _line_has_missing_marker(text: str) -> bool:
    norm = _normalize_ws(text).lower()
    return norm in {"missing", "unverified", "fehlende angabe", "nicht erkennbar", ""}


def _trace_parties_source(source: str, *, debug: bool = False) -> None:
    env_debug = get_config().debug
    if not (debug or env_debug):
        return
    normalized = "structured" if source == "structured" else "missing"
    if normalized in _PARTIES_SOURCE_TRACE_EMITTED:
        return
    _PARTIES_SOURCE_TRACE_EMITTED.add(normalized)
    _log.debug("parties_source_trace", stage="export_render", parties_source=normalized)


def extract_document_type_for_render(parsed: dict | None) -> str:
    if not isinstance(parsed, dict):
        return "missing"

    # FIX 1: Check LLM-extracted type first — matches segment header priority and avoids
    # heuristic (dokumenttyp_parteien) overriding the LLM result.
    doc_type_extracted = _normalize_ws(str(parsed.get("document_type_extracted", "") or ""))
    if doc_type_extracted and not _line_has_missing_marker(doc_type_extracted) and doc_type_extracted != "Unklar":
        return _normalize_display_text(doc_type_extracted)

    document_type = _normalize_ws(str(parsed.get("document_type", "") or ""))
    if document_type and not _line_has_missing_marker(document_type):
        return _normalize_display_text(document_type)

    doktyp = str(parsed.get("dokumenttyp_parteien", "") or "")
    for raw_line in doktyp.splitlines():
        line = _normalize_ws(raw_line)
        if not line:
            continue
        match = re.match(r"(?i)^dokumenttyp\s*:\s*(.+)$", line)
        if match:
            value = _normalize_ws(match.group(1))
            if value and not _line_has_missing_marker(value):
                return _normalize_display_text(value)

    first_line = _normalize_ws(doktyp.splitlines()[0]) if doktyp.splitlines() else ""
    if first_line and not _line_has_missing_marker(first_line):
        return _normalize_display_text(first_line)
    return "missing"


def collect_structured_parties_for_render(
    parsed: dict | None,
    max_items: int = 4,
    *,
    debug: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(parsed, dict):
        _trace_parties_source("missing", debug=debug)
        return []
    raw_parties = list(parsed.get("parties", []) or [])
    if not isinstance(raw_parties, list):
        _trace_parties_source("missing", debug=debug)
        return []

    collected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    role_line_re = re.compile(r"^\s*([^:]{1,80})\s*:\s*(.+?)\s*$")

    for raw in raw_parties:
        item = _normalize_text_item(raw)
        text = _normalize_ws(str(item.get("text", "") or ""))
        if not text or _line_has_missing_marker(text):
            continue

        role = "Partei"
        value = text
        match = role_line_re.match(text)
        if match:
            role = _normalize_display_text(_normalize_ws(match.group(1))) or "Partei"
            value = _normalize_ws(match.group(2))
        value = _normalize_ws(value)
        if not value or _line_has_missing_marker(value):
            continue
        if _is_doc_type_header(value):
            continue

        key = (role.lower(), value.lower())
        if key in seen:
            continue
        seen.add(key)
        citations = [dict(c) for c in list(item.get("citations", []) or []) if isinstance(c, dict)]
        collected.append(
            {
                "role": role,
                "value": _normalize_display_text(value),
                "citations": citations,
            }
        )
        if len(collected) >= max_items:
            break

    _trace_parties_source("structured" if collected else "missing", debug=debug)
    return collected


def _role_has_value(dokumenttyp_parteien: str, role_pattern: str) -> bool:
    for line in str(dokumenttyp_parteien or "").splitlines():
        match = re.match(rf"^\s*{role_pattern}\s*:\s*(.+)$", line, flags=re.IGNORECASE)
        if not match:
            continue
        value = _normalize_ws(match.group(1))
        return not _line_has_missing_marker(value)
    return False


def _has_core_parties(parsed: dict) -> bool:
    text = str((parsed or {}).get("dokumenttyp_parteien", "") or "")
    has_klaeger = _role_has_value(text, r"(klägerin|klaegerin|kläger|klaeger)")
    has_beklagter = _role_has_value(text, r"(beklagter|beklagte|beklagten)")
    return has_klaeger and has_beklagter


def _is_party_missing_followup(text: str) -> bool:
    norm = _normalize_ws(text).lower()
    if not norm:
        return False
    if "parteienangabe" in norm:
        return True
    if "kläger" in norm or "klaeger" in norm or "beklagt" in norm:
        return ("missing" in norm) or ("unverified" in norm) or ("fehlende angabe" in norm)
    return False


def _append_citation_lines(lines: list[str], citations: list[dict], include_sources: bool = True) -> None:
    if not include_sources:
        return
    seen: set[tuple[str, str]] = set()
    for citation in citations or []:
        page = str(citation.get("page_number", "?"))
        snippet = _short_source_excerpt(str(citation.get("quote_snippet", "") or ""))
        key = (page, snippet)
        if snippet:
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"  Quelle S. {page}: {snippet}")


def _append_claim_with_citations(lines: list[str], text: str, citations: list[dict], include_sources: bool = True) -> None:
    lines.append(f"– {_normalize_display_text(text)}")
    _append_citation_lines(lines, citations, include_sources=include_sources)


def _has_structured_segment_analysis(parsed: dict | None) -> bool:
    if not isinstance(parsed, dict):
        return False
    keys = {
        "dokumenttyp_parteien",
        "kernaussagen",
        "auffaelligkeiten",
        "missing_info",
        "prueffragen",
        "todos",
    }
    return any(key in parsed for key in keys)


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_token_count(audit: dict) -> str:
    total_tokens = _safe_int(audit.get("total_tokens")) if isinstance(audit, dict) else None
    if total_tokens is not None and total_tokens > 0:
        return str(total_tokens)
    legacy_tokens = _safe_int(audit.get("token_count")) if isinstance(audit, dict) else None
    if legacy_tokens is not None and legacy_tokens > 0:
        return str(legacy_tokens)
    return "n/a"


def _segment_citation_rollup(segment_results: list[dict]) -> dict:
    verified_items = 0
    unverified_items = 0
    for seg in segment_results or []:
        parsed = seg.get("analysis", {}) if isinstance(seg, dict) else {}
        if not isinstance(parsed, dict):
            continue
        summary = summarize_citation_verification(
            parsed,
            ("kernaussagen", "auffaelligkeiten", "top3_findings"),
        )
        metrics = summary.get("citation_metrics", {})
        verified_items += max(_safe_int(metrics.get("verified_items")) or 0, 0)
        unverified_items += max(_safe_int(metrics.get("unverified_items")) or 0, 0)
    return {
        "citation_status": "verified" if verified_items > 0 else "unverified",
        "citation_metrics": {
            "verified_items": verified_items,
            "unverified_items": unverified_items,
        },
    }


def render_summary_text(parsed: dict, coverage: dict | None = None) -> str:
    lines: list[str] = []
    if coverage:
        lines += [_document_meta_line(coverage), ""]
    if parsed.get("kurzueberblick"):
        lines += ["0. KURZÜBERBLICK", parsed["kurzueberblick"], ""]
    lines += ["1. DOKUMENTTYP & PARTEIEN", f"Dokumenttyp: {extract_document_type_for_render(parsed)}"]
    _append_structured_parties(lines, parsed, max_items=4)
    lines.append("")

    kernaussagen = parsed.get("kernaussagen", [])
    if kernaussagen:
        lines.append("2. KERNAUSSAGEN")
        for item in kernaussagen:
            text = item.get("text", item) if isinstance(item, dict) else item
            citations = item.get("citations", []) if isinstance(item, dict) else []
            _append_claim_with_citations(lines, str(text), citations)
        lines.append("")

    fristen = parsed.get("fristen_termine", [])
    if fristen:
        lines.append("3. FRISTEN & TERMINE")
        lines += [f"– {_normalize_display_text(str(f))}" for f in fristen]
        lines.append("")

    referenzdaten = parsed.get("referenzdaten", [])
    if referenzdaten:
        lines.append("REFERENZDATEN")
        lines += [f"– {_normalize_display_text(str(f))}" for f in referenzdaten]
        lines.append("")

    auffaelligkeiten = parsed.get("auffaelligkeiten", [])
    if auffaelligkeiten:
        lines.append("4. AUFFÄLLIGKEITEN & UNKLARHEITEN")
        for item in auffaelligkeiten:
            text = item.get("text", item) if isinstance(item, dict) else item
            citations = item.get("citations", []) if isinstance(item, dict) else []
            _append_claim_with_citations(lines, str(text), citations)
        lines.append("")

    if parsed.get("prueffragen"):
        lines.append("5. PRÜFFRAGEN")
        lines += [f"– {_normalize_display_text(str(p))}" for p in parsed["prueffragen"]]
        lines.append("")
    return "\n".join(lines)


def render_aktenvermerk_text(parsed: dict, coverage: dict | None = None) -> str:
    lines: list[str] = [
        "AKTENNOTIZ (INTERN)",
        "Hinweis: Nur Extraktion/Strukturierung aus dem Dokument. Human-in-the-loop.",
    ]
    if coverage:
        lines.append(_document_meta_line(coverage))
    lines += [
        "",
        "A DOKUMENTTYP & PARTEIEN",
        f"Dokumenttyp: {extract_document_type_for_render(parsed)}",
    ]
    _append_structured_parties(lines, parsed, max_items=4)
    lines += ["", "B KERNAUSSAGEN"]
    for item in parsed.get("kernaussagen", []):
        _append_claim_with_citations(lines, str(item.get("text", "")), item.get("citations", []))
    lines += ["", "C FRISTEN / TERMINE"]
    for frist in parsed.get("fristen_termine", []):
        text = frist.get("text", "")
        if frist.get("berechnet"):
            text += f" [berechnet; Herleitung: {frist.get('herleitung') or 'nicht angegeben'}]"
        _append_claim_with_citations(lines, text, frist.get("citations", []))
    lines += ["", "D WIDERSPRÜCHE / UNKLARHEITEN (ZU PRÜFEN)"]
    for item in parsed.get("widersprueche", []):
        _append_claim_with_citations(lines, str(item.get("text", "")), item.get("citations", []))
    lines += ["", "E FEHLENDE ANGABEN / RÜCKFRAGEN"]
    for item in parsed.get("fehlende_angaben", []):
        lines.append(f"– {_normalize_display_text(str(item))}")
    lines += ["", "F TO-DOS"]
    for item in parsed.get("todos", []):
        if not _is_generic_todo(str(item)):
            lines.append(f"– {_normalize_display_text(str(item))}")
    return "\n".join(lines)


def _redact_snippet_sensitive(text: str, *, max_chars: int = _SNIPPET_MAX_DISPLAY_CHARS) -> str:
    """P1-C: Maskiert IBAN/BIC in Quellentext-Snippets und kürzt auf max_chars."""
    out = _SNIPPET_IBAN_RE.sub("[IBAN]", str(text or ""))
    out = _SNIPPET_BIC_RE.sub("[BIC]", out)
    out = _normalize_ws(out)
    if len(out) > max_chars:
        out = out[:max_chars].rstrip()
        # Am Wortende kürzen
        last_space = out.rfind(" ")
        if last_space > max_chars // 2:
            out = out[:last_space]
        out += "…"
    return out


def _sanitize_fristen_value(field: str, value: str) -> str:
    """BLOCKER 1: Fristen-Felder dürfen nur Datumswerte enthalten."""
    if field.strip().lower() not in _FRISTEN_FIELDS:
        return value
    if not value or value.strip().lower() == "missing":
        return _FRISTEN_FALLBACK
    if _FRISTEN_DATE_RE.search(value):
        return value
    if _GERMAN_MONTH_DATE_RE.search(value):  # Defense in depth: Long-Format-Datum akzeptieren
        return value
    return _FRISTEN_FALLBACK


def _filter_fallback_fristen_if_real_exist(items: list[dict]) -> list[dict]:
    """Fix 3b: Entfernt Fristen-Fallback-Items wenn valide Datums-Fristen vorhanden sind.

    Verhindert dass 'nicht genannt' neben echten Fristen erscheint.
    """
    _fristen_f = frozenset({"fristen_termine", "kuendigung_fristen", "datum_beginn"})
    fristen_items = [ev for ev in items if str(ev.get("field", "")).lower() in _fristen_f]
    if not fristen_items:
        return items
    has_real = any(
        _FRISTEN_DATE_RE.search(str(ev.get("value", "") or ""))
        or _GERMAN_MONTH_DATE_RE.search(str(ev.get("value", "") or ""))
        for ev in fristen_items
    )
    if not has_real:
        return items
    return [
        ev for ev in items
        if str(ev.get("field", "")).lower() not in _fristen_f
        or _FRISTEN_DATE_RE.search(str(ev.get("value", "") or ""))
        or _GERMAN_MONTH_DATE_RE.search(str(ev.get("value", "") or ""))
    ]


def _sanitize_fristen_label(field: str, label: str) -> str:
    """BLOCKER 1: Label für Fristen-Felder normalisieren."""
    field_key = field.strip().lower()
    if field_key not in _FRISTEN_FIELDS:
        return label
    # Erlaubte Labels aus _SUMMARY_LABELS beibehalten
    allowed = {"Frist/Termin", "Kündigungsfrist", "Zahlungsfrist", "Beginn",
               "Mietbeginn", "Kaution fällig bis", "Verfahrensfrist", "Referenzdatum"}
    if label in allowed:
        return label
    return _SUMMARY_LABELS.get(field_key, "Frist/Termin")


_LLM_ECKDATEN_NON_FRIST_LABELS: tuple[str, ...] = (
    # Geburtsdaten
    "geburtsdatum", "geburtstag", "geboren",
    # Beschäftigungsbeginn / Eintrittsdaten
    "eintrittsdatum", "eintritt", "beschäftigungsbeginn", "beschaeftigungsbeginn",
    "arbeitsbeginn", "anstellungsdatum",
    # Gründungs-/Registrierungsdaten
    "gründungsdatum", "gruendungsdatum", "eintragungsdatum", "registrierungsdatum",
    # Baujahr / historische Daten ohne Handlungsbedarf
    "baujahr",
)


# FIX 4: Generic labels that should be suppressed when LLM already covers the same amount.
_GENERIC_ECKDATEN_LABELS: frozenset[str] = frozenset({
    "miete", "betrag", "zahlung", "mietzahlung",
})


def _merge_llm_regex_eckdaten(
    llm_items: list[dict],
    evidence_items: list[dict],
    doc_type: str,
) -> list[dict]:
    """FIX 4: Merge LLM-eckdaten with specific-label regex evidence items.

    - LLM items are always kept.
    - Regex items with generic labels ('Miete', 'Betrag', 'Zahlung') are suppressed
      when an LLM item already covers the same canonical amount.
    - Regex items with specific labels ('Rückstand', 'Schadensersatz', 'Kaution')
      are added even if LLM covers the same amount.
    - Returns [] when llm_items is empty → caller should use regex-only fallback.
    """
    if not llm_items:
        return []

    llm_amounts: set[str] = set()
    for item in llm_items:
        canonical = _norm_amount_canonical(str(item.get("wert", "") or ""))
        if canonical:
            llm_amounts.add(canonical)

    # Build a set of (canonical_amount, label_lower) for LLM items to detect exact duplicates.
    llm_label_amounts: set[tuple[str, str]] = set()
    for item in llm_items:
        ca = _norm_amount_canonical(str(item.get("wert", "") or ""))
        lbl = str(item.get("label", "") or "").lower()
        if ca and lbl:
            llm_label_amounts.add((ca, lbl))

    # FIX 2: Relabel "Mietbeginn" → "Beginn" when doc is not a Miet/Räumung document.
    doc_type_l = str(doc_type or "").lower()
    _is_miet_doc = any(k in doc_type_l for k in ("miet", "räum", "raeumung"))
    merged = []
    for item in llm_items:
        if str(item.get("label", "") or "") == "Mietbeginn" and not _is_miet_doc:
            item = dict(item)
            item["label"] = "Beginn"
        merged.append(item)
    for ev in evidence_items or []:
        field = str(ev.get("field", "") or "").lower()
        value = str(ev.get("value", "") or "").strip()
        if not value or value == "missing":
            continue
        label = _human_label_for_evidence(
            field, value, str(ev.get("quote", "") or ""), doc_type
        )
        if not label:
            continue
        label_l = label.lower()
        # Skip generic labels entirely when LLM eckdaten exist (LLM is authoritative for those).
        if label_l in _GENERIC_ECKDATEN_LABELS:
            continue
        canonical = _norm_amount_canonical(value)
        # Skip exact duplicates: same label + same canonical amount already in LLM items.
        if canonical and (canonical, label_l) in llm_label_amounts:
            continue
        merged.append({
            "label": label,
            "wert": value,
            "quelle": f"S. {ev.get('page', '?')}",
        })

    return merged


def _filter_llm_eckdaten(items: list[dict]) -> list[dict]:
    """FIX 1/3/6: Filtert und dedupliziert llm_eckdaten-Items.

    1. Entfernt Items deren Label auf Geburtsdaten/Beschäftigungsbeginn/Gründungsdaten hindeutet.
    2. Dedupliziert Items mit identischem EUR-Betragswert (FIX 6): bei gleichem Betrag gewinnt
       der erste Eintrag (LLM-Reihenfolge), folgende Einträge mit demselben Betrag werden entfernt.
    """
    # Step 1: Filter non-Frist labels
    filtered = []
    for item in items or []:
        label_l = str(item.get("label", "") or "").strip().lower()
        if any(kw in label_l for kw in _LLM_ECKDATEN_NON_FRIST_LABELS):
            continue
        filtered.append(item)

    # Step 2: Deduplicate by canonical EUR amount (FIX 6)
    # Normalize: strip currency symbol/whitespace, compare numeric form
    seen_amounts: set[str] = set()
    deduped = []
    for item in filtered:
        wert = str(item.get("wert", "") or "").strip()
        canonical = _norm_amount_canonical(wert)
        # Only dedup non-empty numeric amounts (ignore pure date values)
        if canonical and re.search(r"\d", canonical):
            if canonical in seen_amounts:
                continue
            seen_amounts.add(canonical)
        deduped.append(item)
    return deduped


def _evidence_line(item: dict, doc_type: str, include_sources: bool = True) -> str:
    field = str(item.get("field", "") or "")
    value = str(item.get("value", "") or "")
    page = item.get("page")
    full_quote = str(item.get("quote", "") or "")
    display_quote = _redact_snippet_sensitive(_short_source_excerpt(full_quote))
    label = _human_label_for_evidence(field, value, full_quote, doc_type)
    if not label:
        return ""
    # BLOCKER 1: Fristen-Wert und -Label validieren
    value = _sanitize_fristen_value(field, value)
    label = _sanitize_fristen_label(field, label)
    if page is None or not include_sources:
        return f"– {label}: {_normalize_display_text(value)}"
    return f"– {label}: {_normalize_display_text(value)}\n  Quelle S. {page}: {display_quote}"


def _akten_evidence_line(item: dict, include_sources: bool = True) -> str:
    field = str(item.get("field", "") or "")
    value = str(item.get("value", "") or "")
    page = item.get("page")
    quote = _redact_snippet_sensitive(_short_source_excerpt(str(item.get("quote", "") or "")))
    field_key = field.strip().lower()
    label = _SUMMARY_LABELS.get(field_key, field)
    # BLOCKER 1: Fristen-Wert und -Label validieren
    value = _sanitize_fristen_value(field, value)
    label = _sanitize_fristen_label(field, label)
    if page is None or not include_sources:
        return f"– {label}: {_normalize_display_text(value)}"
    return f"– {label}: {_normalize_display_text(value)}\n  Quelle S. {page}: {quote}"


def _normalize_text_item(raw: dict | str) -> dict:
    if isinstance(raw, dict):
        item = dict(raw)
        if "text" not in item and "title" in item:
            item["text"] = item.get("title", "")
        item["text"] = _normalize_display_text(str(item.get("text", "") or ""))
        item.setdefault("citations", [])
        return item
    return {"text": _normalize_display_text(str(raw)), "citations": []}


def _verified_items(parsed: dict, field: str, max_items: int) -> list[dict]:
    out: list[dict] = []
    for raw in list(parsed.get(field, []) or []):
        item = _normalize_text_item(raw)
        citations = item.get("citations", []) if isinstance(item.get("citations"), list) else []
        if citations:
            out.append(item)
        if len(out) >= max_items:
            break
    return out


def _collect_evidence_values(evidence_map: list[dict], fields: tuple[str, ...], max_items: int) -> list[dict]:
    out: list[dict] = []
    for ev in evidence_map or []:
        field = str(ev.get("field", "") or "")
        if field in _SUPPRESSED_EVIDENCE_FIELDS:
            continue
        if field not in fields:
            continue
        if str(ev.get("value", "") or "") == "missing":
            continue
        out.append(ev)
        if len(out) >= max_items:
            break
    return out


def _collect_unclear_items(parsed: dict, max_items: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    suppress_party_followups = _has_core_parties(parsed)
    for raw in list(parsed.get("missing_info", []) or []):
        text = _normalize_ws(str(raw))
        if suppress_party_followups and _is_party_missing_followup(text):
            continue
        norm = text.casefold()
        if text and norm not in seen:
            seen.add(norm)
            out.append(_normalize_display_text(text))
        if len(out) >= max_items:
            return out
    for raw in list(parsed.get("auffaelligkeiten", []) or []):
        item = _normalize_text_item(raw)
        citations = item.get("citations", []) if isinstance(item.get("citations"), list) else []
        if citations:
            continue
        text = _normalize_ws(str(item.get("text", "")))
        if suppress_party_followups and _is_party_missing_followup(text):
            continue
        norm = text.casefold()
        if text and norm not in seen:
            seen.add(norm)
            out.append(_normalize_display_text(text))
        if len(out) >= max_items:
            break
    return out


def _append_structured_parties(
    lines: list[str],
    parsed: dict,
    max_items: int = 4,
    *,
    debug: bool = False,
    include_sources: bool = True,
) -> None:
    parties = collect_structured_parties_for_render(parsed, max_items=max_items, debug=debug)
    if not parties:
        lines.append("– nicht genannt")
        return
    for party in parties:
        role = _normalize_display_text(str(party.get("role", "Partei") or "Partei"))
        value = _normalize_display_text(str(party.get("value", "") or ""))
        lines.append(f"– {role}: {value}")
        _append_citation_lines(lines, list(party.get("citations", []) or []), include_sources=include_sources)


def render_segmented_summary_text(
    segment_results: list[dict],
    coverage: dict | None = None,
    mode: str = "internal",
) -> str:
    include_sources = mode != "external"
    lines: list[str] = ["ZUSAMMENFASSUNG (NEUTRAL)"]
    if coverage:
        lines += [_document_meta_line(coverage), ""]

    # Cross-segment Kaution-Guard: alle Kautions-Beträge aus allen Segmenten vorsammeln
    known_kaution_amounts = _collect_all_kaution_amounts(list(segment_results or []))
    for idx, seg in enumerate(segment_results or [], start=1):
        meta = seg.get("segment_meta", {}) if isinstance(seg, dict) else {}
        parsed = seg.get("analysis", {}) if isinstance(seg, dict) else {}
        title = meta.get("title_guess", "Dokumentsegment")
        display_doc_type, _ = resolve_segment_header_document_type(
            segment=meta if isinstance(meta, dict) else {},
            analysis_payload=parsed if isinstance(parsed, dict) else {},
        )
        start = meta.get("page_start", meta.get("start_page", "?"))
        end = meta.get("page_end", meta.get("end_page", "?"))
        evidence_map = list((seg.get("evidence_map") if isinstance(seg, dict) else None) or [])
        # Beweismittel aus LLM-Output als Evidence-Items synthetisieren (kein Regex-Pfad)
        if isinstance(parsed, dict):
            for _bm in list(parsed.get("beweismittel", []) or []):
                if not isinstance(_bm, dict):
                    continue
                _bm_text = _normalize_ws(str(_bm.get("text", "") or ""))
                if not _bm_text:
                    continue
                for _cit in list(_bm.get("citations", []) or []):
                    if not isinstance(_cit, dict):
                        continue
                    evidence_map.append({
                        "field": "beweismittel",
                        "value": _bm_text[:200],
                        "page": _cit.get("page_number"),
                        "quote": str(_cit.get("quote_snippet", "") or "")[:200],
                    })
        doc_type = _summary_doc_type_key(
            _normalize_ws(parsed.get("dokumenttyp_parteien", "")) if isinstance(parsed, dict) else "",
            str(title),
        )
        lines.append(f"DOKUMENT {idx} (SEITE {start}-{end}): {display_doc_type}")

        if not _has_structured_segment_analysis(parsed):
            lines += ["– Segment-Ergebnis nicht strukturiert verfügbar.", ""]
            continue

        lines += [
            "1. DOKUMENTTYP & PARTEIEN",
            f"Dokumenttyp: {extract_document_type_for_render(parsed)}",
        ]
        _append_structured_parties(lines, parsed, max_items=4, include_sources=include_sources)
        lines += [
            "",
            "2. ECKDATEN (BETRÄGE / FRISTEN / TERMINE)",
        ]
        # FIX 1/4: LLM-eckdaten haben Vorrang; spezifische Regex-Items ergänzen (FIX 4 merge).
        llm_eckdaten = _filter_llm_eckdaten(
            list((seg.get("llm_eckdaten") if isinstance(seg, dict) else None) or [])
        )
        _regex_ev_items = _collect_evidence_values(
            evidence_map,
            ("betraege", "fristen_termine", "datum_beginn", "kuendigung_fristen", "miete", "nebenkosten", "kaution", "rueckstand", "schadensersatz", "streitwert", "aktenzeichen", "gericht", "beweismittel"),
            max_items=20,
        )
        merged_eckdaten = _merge_llm_regex_eckdaten(llm_eckdaten, _regex_ev_items, doc_type)
        if merged_eckdaten:
            for item in merged_eckdaten:
                label = _normalize_ws(str(item.get("label", "") or ""))
                wert = _normalize_ws(str(item.get("wert", "") or ""))
                if label and wert:
                    lines.append(f"– {label}: {wert}")
        else:
            eckdaten = _filter_fallback_fristen_if_real_exist(_dedupe_summary_items(_postprocess_eckdaten_items(
                _regex_ev_items,
                doc_type,
                known_kaution_amounts=known_kaution_amounts,
            )))
            eckdaten_rendered = [line for ev in eckdaten if (line := _evidence_line(ev, doc_type, include_sources=include_sources))]
            if eckdaten_rendered:
                lines.extend(eckdaten_rendered)
            else:
                lines.append("– missing (Rückfrage erforderlich)")
        lines.append("")

        lines.append("3. KURZÜBERBLICK (MAX. 7, NUR BELEGTE FAKTEN)")
        short_facts = _verified_items(parsed, "kernaussagen", max_items=7)
        if short_facts:
            for item in short_facts:
                _append_claim_with_citations(lines, str(item.get("text", "")), item.get("citations", []), include_sources=include_sources)
        else:
            lines.append("– missing (Rückfrage erforderlich)")
        lines.append("")

        lines.append("4. UNKLARHEITEN IM DOKUMENT")
        unclear_items = _collect_unclear_items(parsed, max_items=10)
        if unclear_items:
            for text in unclear_items:
                lines.append(f"– {_normalize_display_text(text)}")
        else:
            lines.append("– keine")
        lines.append("")

    return "\n".join(lines)


def render_segmented_aktenvermerk_text(
    segment_results: list[dict],
    coverage: dict | None = None,
    mode: str = "internal",
) -> str:
    include_sources = mode != "external"
    lines: list[str] = [
        "AKTENNOTIZ (INTERN)",
        "Hinweis: Nur Extraktion/Strukturierung aus dem Dokument. Human-in-the-loop.",
    ]
    if coverage:
        lines.append(_document_meta_line(coverage))
    lines.append("")

    for idx, seg in enumerate(segment_results or [], start=1):
        meta = seg.get("segment_meta", {}) if isinstance(seg, dict) else {}
        parsed = seg.get("analysis", {}) if isinstance(seg, dict) else {}
        evidence_map = seg.get("evidence_map", []) if isinstance(seg, dict) else []
        title = meta.get("title_guess", "Dokumentsegment")
        display_doc_type, _ = resolve_segment_header_document_type(
            segment=meta if isinstance(meta, dict) else {},
            analysis_payload=parsed if isinstance(parsed, dict) else {},
        )
        start = meta.get("page_start", meta.get("start_page", "?"))
        end = meta.get("page_end", meta.get("end_page", "?"))
        doc_type = _summary_doc_type_key(
            _normalize_ws(parsed.get("dokumenttyp_parteien", "")) if isinstance(parsed, dict) else "",
            str(title),
        )
        lines.append(f"DOKUMENT {idx} (SEITE {start}-{end}): {display_doc_type}")

        if not _has_structured_segment_analysis(parsed):
            lines += ["– Segment-Ergebnis nicht strukturiert verfügbar.", ""]
            continue

        lines += [
            "A DOKUMENTTYP & PARTEIEN",
            f"Dokumenttyp: {extract_document_type_for_render(parsed)}",
        "",
            "B BELEGTE FAKTEN (MAX. 10)",
        ]
        _append_structured_parties(lines, parsed, max_items=4, include_sources=include_sources)
        lines.append("")
        facts = _verified_items(parsed, "kernaussagen", max_items=10)
        if facts:
            for item in facts:
                _append_claim_with_citations(lines, str(item.get("text", "")), item.get("citations", []), include_sources=include_sources)
        else:
            lines.append("– missing (Rückfrage erforderlich)")

        lines += ["", "C FRISTEN / TERMINE (DOKUMENTBASIERT)"]
        fristen = _filter_fallback_fristen_if_real_exist(_dedupe_summary_items(_collect_evidence_values(
            evidence_map,
            ("fristen_termine", "datum_beginn", "kuendigung_fristen"),
            max_items=12,
        )))
        if fristen:
            for ev in fristen:
                lines.append(_akten_evidence_line(ev, include_sources=include_sources))
        else:
            lines.append("– missing (Rückfrage erforderlich)")

        lines += ["", "D UNKLARHEITEN (DOKUMENTBEZOGEN)"]
        unklar = list(parsed.get("auffaelligkeiten", []) or [])
        d_texts: list[str] = []
        if unklar:
            for raw in unklar[:10]:
                item = _normalize_text_item(raw)
                text_d = str(item.get("text", ""))
                _append_claim_with_citations(lines, text_d, item.get("citations", []), include_sources=include_sources)
                if text_d:
                    d_texts.append(text_d.casefold())
        else:
            lines.append("– keine")

        lines += ["", "E RÜCKFRAGEN AN MANDANT (MISSING INFO)"]
        suppress_party_followups = _has_core_parties(parsed)
        missing_info = []
        for raw in list(parsed.get("missing_info", []) or []):
            text = _normalize_ws(str(raw))
            if not text:
                continue
            if suppress_party_followups and _is_party_missing_followup(text):
                continue
            # FIX 3: Skip E-items that are already covered in D (fuzzy threshold 0.60)
            text_cf = text.casefold()
            if any(SequenceMatcher(None, text_cf, d).ratio() >= 0.60 for d in d_texts):
                continue
            missing_info.append(text)
        if missing_info:
            for item in missing_info[:10]:
                lines.append(f"– {_normalize_display_text(str(item))}")
        else:
            lines.append("– keine")

        lines += ["", "F TO-DOS (ARBEITSAUFGABEN, OHNE RECHTSRAT)"]
        todos = [str(x).strip() for x in list(parsed.get("todos", []) or []) if str(x).strip() and not _is_generic_todo(str(x))]
        if todos:
            for item in todos[:10]:
                lines.append(f"– {_normalize_display_text(str(item))}")
        else:
            lines.append("– keine")

        prueffragen = []
        for raw in list(parsed.get("prueffragen", []) or []):
            text = _normalize_ws(str(raw))
            if not text:
                continue
            if suppress_party_followups and _is_party_missing_followup(text):
                continue
            prueffragen.append(text)
        if prueffragen:
            lines += ["", "G PRÜFFRAGEN (ZU PRÜFEN)"]
            for item in prueffragen[:8]:
                lines.append(f"– {_normalize_display_text(str(item))}")
        lines.append("")

    return "\n".join(lines)
