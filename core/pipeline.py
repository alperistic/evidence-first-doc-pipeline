"""core/pipeline.py - Orchestration and helper logic for the document analysis pipeline.

No streamlit import. Used by app.py as a pure logic layer.
"""

import re
import difflib
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from typing import Any

from core.logging_config import get_logger
from core.config import get_config

_log = get_logger(__name__)
from core.models import LLMResult, clean_snippet
from core.evidence import build_evidence_map
from core.citation import summarize_citation_verification
from core.analyzer import analyze_document, generate_aktenvermerk, maybe_verify_citations_for_fields
from core.prompts import DOC_CLASS_HINTS, get_aktenvermerk_prompt
from governance.doc_class_policy import (
    normalize_doc_class,
    resolve_display_document_type,
    resolve_doc_class_fields,
    segment_doc_class_from_segment,
)
from core.legacy_deadlines import build_legacy_deadlines, build_reference_dates
from governance.redaction import restore_payload_from_llm
from core.utils import (
    _page_no,
    _clean_quote,
    _safe_text,
    _dedupe_str_list,
    _sum_present_tokens,
    _stitch_pages_with_tags,
    _split_tagged_pages,
    _doc_type_from_block,
    _build_marker_segments,
)

# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------
_INTERNAL_EXPORT_PLACEHOLDER_RE = re.compile(
    r"\[(?:DICT|PERSON|ADDR|EMAIL|IBAN|PARTEI|AKTENZEICHEN|PHONE|TAXID|FIRMA)_[0-9]+\]"
)
_PARTY_PSEUDONYM_LINE_RE = re.compile(
    r"\b(kläger(?:in)?|klaeger(?:in)?|beklagt(?:e|er|en)?|"
    r"vermieter(?:in)?|mieter(?:in)?|arbeitgeber|arbeitnehmer|"
    r"käufer(?:in)?|kaeufer(?:in)?|verkäufer(?:in)?|verkaeufer(?:in)?)\b"
    r"[\s\S]{0,140}?(\[(?:PARTEI|PERSON)_[A-Z0-9]+\])",
    flags=re.IGNORECASE,
)
_DEADLINE_FACT_DATE_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b")
_DEADLINE_FACT_TRIGGER_RE = re.compile(
    r"\b("
    r"bis\s+spa?etestens|spätestens|zahlungsfrist|zahlbar\s+bis|"
    r"fällig|faellig"
    r")\b",
    flags=re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _segment_text_for_analysis(pages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for idx, page in enumerate(pages, start=1):
        page_no = _page_no(page) or idx
        page_text = str(page.get("text", "") or "").strip()
        if page_text:
            parts.append(f"[Seite {page_no}]\n{page_text}")
        else:
            parts.append(f"[Seite {page_no}]")
    return "\n\n".join(parts).strip()


def _document_analysis_prompt(
    *,
    segment_doc_class: str,
    allow_llm_doc_class_guess: bool,
    doc_class: str | None = None,
) -> str:
    segment_class = normalize_doc_class(segment_doc_class)
    if allow_llm_doc_class_guess:
        class_instruction = (
            "Dokumentklassen-Schätzung ist optional aktiviert. "
            "Wenn erkennbar, setze `document_type` auf deine beste Schätzung."
        )
    else:
        class_instruction = (
            f'Dokumentklassifizierung ist deaktiviert. Setze `document_type` exakt auf "{segment_class}". '
            "Keine Neuklassifizierung durchführen."
        )
    resolved_class = normalize_doc_class(doc_class) if doc_class else segment_class
    class_hint = DOC_CLASS_HINTS.get(resolved_class, "")
    doc_class_section = (
        f"\nDokumentklassen-Hinweise ({resolved_class}): {class_hint}"
        if class_hint
        else ""
    )
    return (
        "Du analysierst genau EIN juristisches Dokumentsegment. "
        "Führe die strukturierte Extraktion in einem Schritt aus. "
        "Antworte ausschließlich mit validem JSON ohne Markdown.\n"
        "Schema:\n"
        "{"
        "\"metadata\": {\"document_id\": \"...\", \"aktenzeichen\": \"...\", \"parties\": [\"...\"], \"source_pages\": [1,2]},"
        "\"document_type\": \"...\","
        "\"facts\": [{\"text\": \"...\", \"citations\": [{\"page_number\": 1, \"quote_snippet\": \"max 20 Wörter exakt\", \"section_hint\": \"optional\"}]}],"
        "\"dates\": [{\"text\": \"...\", \"date_category\": \"Frist|Termin|Referenzdatum\", \"citations\": [{\"page_number\": 1, \"quote_snippet\": \"max 20 Wörter exakt\", \"section_hint\": \"optional\"}]}],"
        "\"issues\": [{\"text\": \"...\", \"citations\": [{\"page_number\": 1, \"quote_snippet\": \"max 20 Wörter exakt\", \"section_hint\": \"optional\"}]}],"
        "\"parties\": [{\"text\": \"...\", \"citations\": [{\"page_number\": 1, \"quote_snippet\": \"max 20 Wörter exakt\", \"section_hint\": \"optional\"}]}],"
        "\"amounts\": [{\"text\": \"...\", \"citations\": [{\"page_number\": 1, \"quote_snippet\": \"max 20 Wörter exakt\", \"section_hint\": \"optional\"}]}],"
        "\"key_facts\": [\"...\"],"
        "\"deadlines\": [\"...\"],"
        "\"summary_neutral\": \"...\","
        "\"uncertainties\": [\"...\"],"
        "\"todos\": [\"Hinweis: Frist aus dem Dokument prüfen und ggf. notieren\"],"
        "\"evidence\": [{\"page_number\": 1, \"quote_snippet\": \"max 20 Wörter\", \"section_hint\": \"optional\"}]"
        "}\n"
        "Regeln: neutral, keine Rechtsberatung, keine Erfindungen, fehlende Angaben klar markieren. "
        "Alle Aussagen nur aus dem Segmenttext. Für facts/dates/issues/parties/amounts immer citations angeben; "
        "wenn kein Beleg im Segment vorhanden ist: citations=[].\n"
        "FRISTEN-KLASSIFIZIERUNG — ENTSCHEIDUNGSREGEL: Stelle dir die Frage: 'Muss jetzt oder zukünftig gehandelt werden?'\n"
        "JA (Handlungsbedarf) → FRIST/TERMIN → in dates aufnehmen. NEIN (vergangenes Ereignis, bloße Tatsache, Ortsangabe) → REFERENZDATUM → NIEMALS in dates.\n"
        "AKTIONS-TEST: Wenn der Satz eine abgeschlossene Handlung beschreibt ('gewährt am X', 'ausgezahlt am X', 'per Überweisung am X', 'eingereicht am X', 'Hamburg, den X'), ist es KEIN FRIST.\n"
        "NIEMALS in dates: Klageeinreichungsdatum/Briefdatum ('Hamburg, den X'), "
        "Darlehensgewährungsdatum ('am X ein Darlehen/Kredit gewährt'), "
        "durchgeführte Überweisung/Auszahlung ('am X per Überweisung ausgezahlt').\n"
        "IMMER in dates (auch wenn in facts): Zahlungsziel/Rückzahlungsfrist ('Rückzahlung für den X vereinbart', 'Zahlungsfrist bis X'), "
        "Mahnfrist/Fristsetzung ('Fristsetzung bis X'), "
        "Verzugsbeginn/Zinsbeginn ('Verzugszinsen ab X', 'Zinsen seit dem X'), "
        "Kündigungsfristen, Vertragsfristen.\n"
        "dates darf nicht leer sein wenn ein Datum mit Fristcharakter im Segment vorkommt.\n"
        "date_category unterscheidet drei Typen:\n"
        "- \"Frist\" = Datum bis zu dem gehandelt werden MUSS (Zahlungsfrist, Kündigungsfrist, Rückzahlungsfrist, Verzugsbeginn)\n"
        "- \"Termin\" = konkreter Termin mit Handlungsbedarf (Übergabetermin, Mietbeginn, Verhandlungstermin)\n"
        "- \"Referenzdatum\" = reines Bezugsdatum ohne Handlungsbedarf (Briefdatum, Klagedatum, Darlehensauszahlungsdatum, Überweisungsdatum, Rechnungsdatum, Vertragsdatum)\n"
        "todos: neutrale Arbeitshinweise ausschließlich im Format 'Hinweis: ...', 'Zu prüfen: ...' "
        "oder 'Bitte klären: ...' (z.B. 'Hinweis: Frist notieren', 'Zu prüfen: Vollständigkeit der Unterlagen'). "
        "Keine Rechtsberatung, keine Rechtsbewertungen, keine Handlungsempfehlungen.\n"
        "ECKDATEN (BETRÄGE) – Pflichtregeln für das amounts-Feld:\n"
        "- Monatsmiete: Als Monatsmiete-Betrag (value) ist ausschließlich der Betrag zu extrahieren, "
        "der direkt nach 'Gesamte monatliche Zahllast', 'monatliche Zahllast' oder 'Gesamtmiete' steht. "
        "'Grundmiete' und 'Betriebskosten' sind Teilbeträge — deren Beträge NIEMALS als Monatsmiete-value ausgeben. "
        "Beispiel: 'Grundmiete EUR 4.200 + Betriebskosten EUR 620 = Gesamte monatliche Zahllast EUR 4.820' → value='EUR 4.820,00'. "
        "Betrag im Kontext von 'offen', 'Rückstand', 'nicht geleistet', 'Verzug' → label 'Rückstand'. "
        "Nie denselben Betrag mit beiden Labels ausgeben.\n"
        "- Kaution vs. Rückstand: Betrag im Kontext von 'Kaution verwerten', 'Kaution i.H.v.', "
        "'Mietsicherheit' → label 'Kaution', NIEMALS 'Rückstand' — auch wenn 'Rückstand' im selben "
        "Satz vorkommt (z.B. in Mahnung: 'Kaution verwerten wegen Mietrückstand').\n"
        "- Schadensersatz vs. Miete: Betrag als Summe von Schadensposten ('Gesamtschaden: EUR X') "
        "→ label 'Schadensersatz', nicht 'Miete' — auch bei Mietstreit.\n"
        "- Doppelte Beträge: Identisches Label + identischer Betrag nur einmal ausgeben; "
        "bei mehreren Quellen erste Quelle behalten.\n"
        "- PARTEIENREGEL: Dokumenttitel und Seitenheader (MÄNGELANZEIGE, KLAGESCHRIFT, URTEIL, "
        "MAHNUNG, KÜNDIGUNG, ANTWORTSCHREIBEN) NICHT als Parteienname ausgeben. "
        "Partei = natürliche oder juristische Person.\n"
        f"{class_instruction}"
        f"{doc_class_section}"
    )


def _parse_dictionary_entities(raw_value: str) -> list[str]:
    return [line.strip() for line in str(raw_value or "").splitlines() if line.strip()]


def _dictionary_state_key(entities: list[str], fuzzy: bool, threshold: int) -> tuple:
    normalized = tuple(
        sorted(
            {
                re.sub(r"\s+", " ", entity).strip().casefold()
                for entity in entities
                if str(entity).strip()
            }
        )
    )
    return normalized, bool(fuzzy), int(threshold)


def _build_reverse_mapping(mapping: dict[str, str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for placeholder, original in mapping.items():
        if not placeholder or not original:
            continue
        pairs.append((str(original), str(placeholder)))
    return sorted(pairs, key=lambda item: len(item[0]), reverse=True)


def _apply_reverse_mapping(text: str, reverse_mapping: list[tuple[str, str]]) -> str:
    out = str(text or "")
    for original, placeholder in reverse_mapping:
        out = out.replace(original, placeholder)
    return out


# ---------------------------------------------------------------------------
# Pipeline functions
# ---------------------------------------------------------------------------

def _build_coverage_metrics(
    pages: list[dict],
    chunks_sent: int,
    citation_status: str,
    citation_metrics: dict | None,
    citation_check_ran: bool | None = None,
    citation_verify_mode: str | None = None,
) -> dict:
    pages_total = len(pages)
    pages_extracted = sum(1 for p in pages if str(p.get("text", "")).strip())
    chars_total = sum(len(str(p.get("text", "") or "")) for p in pages)
    metrics = citation_metrics or {}
    claim_verified = int(metrics.get("claim_verified_items", metrics.get("verified_items", 0)) or 0)
    claim_unverified = int(metrics.get("claim_unverified_items", metrics.get("unverified_items", 0)) or 0)
    total_items = int(metrics.get("total_items", claim_verified + claim_unverified) or 0)
    claim_verified_pct = round((claim_verified / total_items) * 100) if total_items else 0
    presence_verified = int(metrics.get("presence_verified_items", claim_verified) or 0)
    presence_verified = min(max(presence_verified, 0), max(total_items, 0))
    presence_verified_pct = round((presence_verified / total_items) * 100) if total_items else 0
    return {
        "pages_total": pages_total,
        "pages_extracted": pages_extracted,
        "chars_total": chars_total,
        "chunks_sent": chunks_sent,
        "citation_status": citation_status,
        "citations_verified_pct": claim_verified_pct,
        "claim_verified_pct": claim_verified_pct,
        "presence_verified_pct": presence_verified_pct,
        "citation_check_ran": bool(citation_check_ran),
        "citation_verify_mode": str(citation_verify_mode or "disabled"),
    }


def _build_redaction_summary(mask_counts: dict[str, int] | None) -> dict[str, int]:
    summary: dict[str, int] = {}
    for placeholder, count in (mask_counts or {}).items():
        key = str(placeholder).strip() or "[MASKIERT]"
        summary[key] = summary.get(key, 0) + int(count or 0)
    return dict(sorted(summary.items(), key=lambda x: x[0]))


def _error_segment_result(
    seg: dict,
    *,
    error_msg: str,
    segment_doc_class: str,
    seg_pages_for_llm: list[dict],
    evidence_map: list[dict],
    context_mapping: dict[str, str],
    auto_classify: bool,
    citation_verify_mode: str,
    timings_ms: dict[str, int],
) -> dict:
    """Minimal, valid segment result when analyze_document() raises unexpectedly.

    Prevents a single LLM failure from aborting the entire multi-segment analysis.
    analysis_output_type is set to "api_error" so the UI can surface the failure.
    The payload contains no plaintext of the underlying error - only its repr().
    """
    empty_payload: dict[str, Any] = {
        # Leave empty: the LLM did not respond, so no llm_doc_class_guess must be
        # emitted into the audit. resolve_doc_class_fields() reads document_type via
        # llm_doc_class_guess_from_payload() - a non-empty value here would write a
        # false guess into the audit.
        "document_type": "",
        "kurzueberblick": "[Analysefehler: LLM nicht erreichbar]",
        "kernaussagen": [],
        "top3_findings": [],
        "top3_questions": [],
        "auffaelligkeiten": [],
        "fristen_termine": [],
        "facts": [],
        "dates": [],
        "issues": [],
        "parties": [],
        "amounts": [],
        "todos": [],
        "uncertainties": [],
        "citation_status": "unchecked",
        "citation_check_ran": False,
        "citation_metrics": {},
        "chunks_sent": 0,
        "citation_verify_mode": citation_verify_mode,
        "parse_error": True,
    }
    doc_class_fields = resolve_doc_class_fields(
        segment_doc_class=segment_doc_class,
        allow_llm_doc_class_guess=auto_classify,
        analysis_payload=empty_payload,
    )
    seg_coverage = _build_coverage_metrics(
        pages=seg_pages_for_llm,
        chunks_sent=0,
        citation_status="unchecked",
        citation_metrics={},
        citation_check_ran=False,
        citation_verify_mode=citation_verify_mode,
    )
    restored_evidence_map = restore_payload_from_llm(evidence_map, context_mapping)
    return {
        "segment_meta": {k: v for k, v in seg.items() if k != "pages"},
        "analysis": empty_payload,
        "analysis_redacted": empty_payload,
        "analysis_raw": empty_payload,
        "analysis_raw_redacted": empty_payload,
        "analysis_output_type": "api_error",
        "evidence_map": restored_evidence_map,
        "evidence_map_redacted": evidence_map,
        "coverage": seg_coverage,
        "model": "unknown",
        "doc_class_fields": doc_class_fields,
        "token_count": 0,
        "has_token_usage": False,
        "prompt_tokens": None,
        "completion_tokens": None,
        "timings_ms": timings_ms,
    }


def _build_redacted_export_payload(segment_results: list[dict]) -> list[dict]:
    selected: list[dict] = []
    for seg in segment_results:
        copy_seg = dict(seg)
        if "analysis_redacted" in seg:
            copy_seg["analysis"] = seg.get("analysis_redacted")
        if "evidence_map_redacted" in seg:
            copy_seg["evidence_map"] = seg.get("evidence_map_redacted")
        selected.append(copy_seg)
    return selected


def _find_internal_export_placeholder_hits(payload: dict[str, Any]) -> list[str]:
    seen: list[str] = []

    def _walk(value: Any) -> None:
        if isinstance(value, str):
            for match in _INTERNAL_EXPORT_PLACEHOLDER_RE.findall(value):
                if match not in seen:
                    seen.append(match)
            return
        if isinstance(value, list):
            for item in value:
                _walk(item)
            return
        if isinstance(value, dict):
            for item in value.values():
                _walk(item)

    _walk(payload)
    return seen


def _inject_pseudonym_party_evidence(evidence_map: list[dict], pages_for_llm: list[dict]) -> list[dict]:
    """
    Adds party evidence from roles + pseudonyms so parties don't show up as missing.
    """
    merged = list(evidence_map or [])
    seen = {
        (str(item.get("field", "")).strip(), str(item.get("value", "")).strip(), item.get("page"))
        for item in merged
    }

    for page in pages_for_llm:
        page_no = _page_no(page)
        page_text = str(page.get("text", "") or "")
        for match in _PARTY_PSEUDONYM_LINE_RE.finditer(page_text):
            role = match.group(1).strip().capitalize()
            label = match.group(2).strip()
            value = f"{role}: {label}"
            key = ("parteien", value, page_no)
            if key in seen:
                continue
            snippet = page_text[max(0, match.start() - 20) : min(len(page_text), match.end() + 20)]
            seen.add(key)
            merged.append(
                {
                    "field": "parteien",
                    "value": value,
                    "page": page_no,
                    "quote": clean_snippet(snippet, max_chars=180),
                }
            )
    has_party_value = any(
        str(item.get("field", "")).strip() == "parteien"
        and str(item.get("value", "")).strip() not in {"", "missing"}
        for item in merged
    )
    if has_party_value:
        merged = [
            item
            for item in merged
            if not (
                str(item.get("field", "")).strip() == "parteien"
                and str(item.get("value", "")).strip() == "missing"
            )
        ]
    return merged


def _aggregate_document_chunk_results(chunk_results: list[LLMResult]) -> LLMResult:
    if not chunk_results:
        return LLMResult(
            parsed={"parse_error": True, "message": "Keine Analyse verfügbar."},
            token_count=0,
            model=get_config().azure_openai_deployment,
            output_type="json_fallback",
            input_chars=0,
            input_truncated=False,
            has_token_usage=False,
        )

    ok_payloads = [
        result.parsed
        for result in chunk_results
        if result.output_type == "json_ok" and isinstance(result.parsed, dict)
    ]
    if not ok_payloads:
        first = chunk_results[0]
        return LLMResult(
            parsed=dict(first.parsed or {}),
            token_count=sum(int(result.token_count or 0) for result in chunk_results),
            model=str(first.model or get_config().azure_openai_deployment),
            output_type=str(first.output_type),
            input_chars=sum(int(result.input_chars or 0) for result in chunk_results),
            input_truncated=any(bool(result.input_truncated) for result in chunk_results),
            prompt_tokens=_sum_present_tokens(
                [int(result.prompt_tokens) for result in chunk_results if isinstance(result.prompt_tokens, int)]
            ),
            completion_tokens=_sum_present_tokens(
                [int(result.completion_tokens) for result in chunk_results if isinstance(result.completion_tokens, int)]
            ),
            has_token_usage=any(bool(result.has_token_usage) for result in chunk_results),
        )

    merged_metadata: dict[str, Any] = {}
    document_type = "Unklar"
    summary_neutral = "missing"
    key_facts: list[str] = []
    deadlines: list[str] = []
    uncertainties: list[str] = []
    evidence: list[dict[str, Any]] = []

    for payload in ok_payloads:
        metadata = payload.get("metadata", {})
        if isinstance(metadata, dict):
            for key, value in metadata.items():
                if key not in merged_metadata or merged_metadata.get(key) in {"", None, [], {}}:
                    merged_metadata[key] = value
        if document_type == "Unklar":
            candidate_type = str(payload.get("document_type", "") or "").strip()
            if candidate_type:
                document_type = candidate_type
        if summary_neutral in {"", "missing"}:
            candidate_summary = str(payload.get("summary_neutral", "") or "").strip()
            if candidate_summary:
                summary_neutral = candidate_summary
        key_facts.extend([str(item) for item in list(payload.get("key_facts", []) or [])])
        deadlines.extend([str(item) for item in list(payload.get("deadlines", []) or [])])
        uncertainties.extend([str(item) for item in list(payload.get("uncertainties", []) or [])])
        for item in list(payload.get("evidence", []) or []):
            if not isinstance(item, dict):
                continue
            evidence.append(
                {
                    "page_number": item.get("page_number"),
                    "quote_snippet": _clean_quote(str(item.get("quote_snippet", "") or "")),
                    "section_hint": item.get("section_hint"),
                }
            )

    deduped_evidence: list[dict[str, Any]] = []
    seen_evidence: set[tuple[Any, str]] = set()
    for item in evidence:
        key = (item.get("page_number"), str(item.get("quote_snippet", "")).casefold())
        if key in seen_evidence:
            continue
        seen_evidence.add(key)
        deduped_evidence.append(item)

    merged_payload = {
        "metadata": merged_metadata,
        "document_type": document_type,
        "key_facts": _dedupe_str_list(key_facts, limit=40),
        "deadlines": _dedupe_str_list(deadlines, limit=25),
        "summary_neutral": summary_neutral,
        "uncertainties": _dedupe_str_list(uncertainties, limit=25),
        "evidence": deduped_evidence[:40],
    }

    has_usage = any(bool(result.has_token_usage) for result in chunk_results)
    return LLMResult(
        parsed=merged_payload,
        token_count=sum(int(result.token_count or 0) for result in chunk_results),
        model=next(
            (
                str(result.model)
                for result in reversed(chunk_results)
                if str(result.model or "").strip()
            ),
            get_config().azure_openai_deployment,
        ),
        output_type="json_ok",
        input_chars=sum(int(result.input_chars or 0) for result in chunk_results),
        input_truncated=any(bool(result.input_truncated) for result in chunk_results),
        prompt_tokens=_sum_present_tokens(
            [int(result.prompt_tokens) for result in chunk_results if isinstance(result.prompt_tokens, int)]
        ),
        completion_tokens=_sum_present_tokens(
            [int(result.completion_tokens) for result in chunk_results if isinstance(result.completion_tokens, int)]
        ),
        has_token_usage=has_usage,
    )


def _normalize_claim_items_with_citations(
    raw_items: Any,
    fallback_items: Any = None,
    default_candidates: list[dict[str, Any]] | None = None,
    seed_from_default_candidates: bool = False,
) -> list[dict[str, Any]]:
    source = raw_items if isinstance(raw_items, list) else fallback_items if isinstance(fallback_items, list) else []
    candidates = list(default_candidates or [])
    out: list[dict[str, Any]] = []

    for raw in source:
        text = ""
        citations: list[dict[str, Any]] = []
        if isinstance(raw, dict):
            text = str(raw.get("text") or raw.get("value") or raw.get("title") or "").strip()
            raw_citations = raw.get("citations")
            if isinstance(raw_citations, list):
                citations = [dict(item) for item in raw_citations if isinstance(item, dict)]
        else:
            text = str(raw or "").strip()
        if not text:
            continue
        if not citations and candidates and seed_from_default_candidates:
            citations = [dict(item) for item in candidates[:3]]
        item_out: dict[str, Any] = {"text": text, "citations": citations}
        if isinstance(raw, dict):
            if "citation_status" in raw:
                item_out["citation_status"] = str(raw.get("citation_status", "") or "").strip()
            if isinstance(raw.get("citation_candidates"), list):
                item_out["citation_candidates"] = [
                    dict(item) for item in list(raw.get("citation_candidates", [])) if isinstance(item, dict)
                ]
            if isinstance(raw.get("citations_verified"), list):
                item_out["citations_verified"] = [
                    dict(item) for item in list(raw.get("citations_verified", [])) if isinstance(item, dict)
                ]
            if isinstance(raw.get("best_evidence"), dict):
                item_out["best_evidence"] = dict(raw.get("best_evidence", {}))
            if str(raw.get("citation_reuse_guard", "")).strip():
                item_out["citation_reuse_guard"] = str(raw.get("citation_reuse_guard"))
            if str(raw.get("date_category", "")).strip():
                item_out["date_category"] = str(raw.get("date_category", "")).strip()
        out.append(item_out)
    return out


def _claim_item_context_text(item: dict[str, Any]) -> str:
    text = str(item.get("text", "") or "").strip()
    citation_snippets: list[str] = []
    for citation in list(item.get("citations", []) or []):
        if not isinstance(citation, dict):
            continue
        quote = str(citation.get("quote_snippet", "") or "").strip()
        if quote:
            citation_snippets.append(quote)
    return " ".join([text] + citation_snippets).strip()


def _deadline_candidate_key(item: dict[str, Any]) -> tuple[str, str]:
    context = _claim_item_context_text(item)
    text_key = re.sub(r"\s+", " ", str(item.get("text", "") or "")).strip().casefold()
    date_match = _DEADLINE_FACT_DATE_RE.search(context)
    date_key = date_match.group(0) if date_match else ""
    return text_key, date_key


def _has_payment_deadline_context(item: dict[str, Any]) -> bool:
    context = _claim_item_context_text(item)
    if not _DEADLINE_FACT_DATE_RE.search(context):
        return False
    label = str(item.get("label", "") or "").strip().casefold()
    if "zahlungsfrist" in label:
        return True
    return bool(_DEADLINE_FACT_TRIGGER_RE.search(context))


def _extend_dates_with_deadline_fact_candidates(
    date_items: list[dict[str, Any]],
    fact_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = [dict(item) for item in date_items if isinstance(item, dict)]
    if any(_has_payment_deadline_context(item) for item in merged):
        return merged
    seen = {_deadline_candidate_key(item) for item in merged}

    for item in fact_items:
        if not isinstance(item, dict):
            continue
        context = _claim_item_context_text(item)
        date_match = _DEADLINE_FACT_DATE_RE.search(context)
        if not date_match or not _DEADLINE_FACT_TRIGGER_RE.search(context):
            continue
        candidate = {
            "text": date_match.group(0),
            "label": "Zahlungsfrist",
            "citations": [
                dict(citation)
                for citation in list(item.get("citations", []) or [])
                if isinstance(citation, dict)
            ],
        }
        key = _deadline_candidate_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        merged.append(candidate)
    return merged


def _to_local_verification_payload(analysis_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "facts": _normalize_claim_items_with_citations(
            analysis_payload.get("facts"),
            fallback_items=analysis_payload.get("key_facts"),
        ),
        "dates": _normalize_claim_items_with_citations(
            analysis_payload.get("dates"),
            fallback_items=analysis_payload.get("deadlines"),
        ),
        "issues": _normalize_claim_items_with_citations(
            analysis_payload.get("issues"),
            fallback_items=analysis_payload.get("uncertainties"),
        ),
        "parties": _normalize_claim_items_with_citations(
            analysis_payload.get("parties"),
        ),
        "amounts": _normalize_claim_items_with_citations(
            analysis_payload.get("amounts"),
        ),
    }


def _to_legacy_segment_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    def _debug_shorten(value: Any, limit: int = 200) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(text) <= limit:
            return text
        return f"{text[:limit]}..."

    def _debug_date_items_snapshot(items: list[dict[str, Any]], limit: int = 10) -> list[dict[str, str]]:
        snapshot: list[dict[str, str]] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            snapshot.append(
                {
                    "text": _debug_shorten(item.get("text", ""), limit=200),
                    "label": _debug_shorten(item.get("label", ""), limit=80),
                }
            )
        if len(items) > limit:
            snapshot.append({"text": f"... +{len(items) - limit} weitere", "label": ""})
        return snapshot

    payload_obj = payload if isinstance(payload, dict) else {}
    analysis_payload = payload_obj.get("analysis_payload")
    analysis_payload = analysis_payload if isinstance(analysis_payload, dict) else payload_obj

    metadata = analysis_payload.get("metadata", payload_obj.get("metadata", {}))
    metadata = metadata if isinstance(metadata, dict) else {}
    document_type = str(analysis_payload.get("document_type", payload_obj.get("document_type", "Unklar")) or "Unklar")
    document_type_extracted = str(
        analysis_payload.get("document_type_extracted", payload_obj.get("document_type_extracted", "")) or ""
    ).strip()
    fact_items = _normalize_claim_items_with_citations(
        analysis_payload.get("facts"),
        fallback_items=analysis_payload.get("key_facts"),
    )
    date_items = _normalize_claim_items_with_citations(
        analysis_payload.get("dates"),
        fallback_items=analysis_payload.get("deadlines"),
    )
    debug_deadlines_enabled = get_config().debug_deadlines
    debug_is_mahnung = False
    date_items_before_debug: list[dict[str, str]] = []
    if debug_deadlines_enabled:
        segment_text_for_debug = str(analysis_payload.get("segment_text", payload_obj.get("segment_text", "")) or "")
        facts_text_for_debug = " ".join(
            str(item.get("text", "") or "") for item in fact_items if isinstance(item, dict)
        )
        debug_is_mahnung = (
            str(document_type or "").strip().casefold() == "mahnung"
            or "mahnung" in facts_text_for_debug.casefold()
            or "mahnung" in segment_text_for_debug.casefold()
        )
        if debug_is_mahnung:
            date_items_before_debug = _debug_date_items_snapshot(date_items)
    date_items = _extend_dates_with_deadline_fact_candidates(date_items, fact_items)
    date_items_after_debug = (
        _debug_date_items_snapshot(date_items) if debug_deadlines_enabled and debug_is_mahnung else []
    )
    _issues_raw = analysis_payload.get("issues")
    _uncertainties_raw = analysis_payload.get("uncertainties")
    issue_items = _normalize_claim_items_with_citations(
        _issues_raw,
        fallback_items=_uncertainties_raw,
    )
    # fehlende_angaben are merged into uncertainties by _process_segment (~line 1015).
    # When issues=[] the fallback is blocked ([] is a valid list, so source=[] and
    # fallback is never used). When issues has content, uncertainties are silently
    # dropped. Always merge both when issues was provided as a list.
    if isinstance(_issues_raw, list) and isinstance(_uncertainties_raw, list):
        unc_items = _normalize_claim_items_with_citations(_uncertainties_raw)
        seen = {item.get("text", "") for item in issue_items}
        issue_items = issue_items + [
            item for item in unc_items if item.get("text", "") not in seen
        ]
    key_facts = _dedupe_str_list([str(item.get("text", "") or "") for item in fact_items], limit=40)
    deadlines = build_legacy_deadlines(date_items, limit=25)
    reference_dates = build_reference_dates(date_items, limit=10)
    if debug_deadlines_enabled and debug_is_mahnung:
        facts_head = [
            _debug_shorten(item.get("text", ""), limit=200)
            for item in fact_items[:2]
            if isinstance(item, dict)
        ]
        _log.debug(
            "debug_deadlines",
            stage="deadlines",
            doc_type=document_type,
            facts_count=len(fact_items),
            dates_count=len(date_items),
            facts_head=facts_head,
            date_items_before=date_items_before_debug,
            date_items_after=date_items_after_debug,
            deadlines_out=_debug_shorten(deadlines, limit=1200),
        )
    uncertainties = _dedupe_str_list([str(item.get("text", "") or "") for item in issue_items], limit=25)
    summary_neutral = str(analysis_payload.get("summary_neutral", "missing") or "missing")
    structured_parties = _normalize_claim_items_with_citations(analysis_payload.get("parties"))

    doc_id = str(metadata.get("document_id", "") or "").strip()
    az = str(metadata.get("aktenzeichen", "") or "").strip()

    meta_lines = [f"Dokumenttyp: {document_type}"]
    if doc_id:
        meta_lines.append(f"Dokument-ID: {doc_id}")
    if az:
        meta_lines.append(f"Az: {az}")

    top3 = key_facts[:3] if key_facts else uncertainties[:3]
    top3_source_items = fact_items[:3] if fact_items else issue_items[:3]
    top3_findings: list[dict[str, Any]] = []
    for idx, item in enumerate(top3):
        source = top3_source_items[idx] if idx < len(top3_source_items) else {"citations": []}
        top3_findings.append(
            {
                "title": item,
                "severity": "mittel",
                "why_relevant": "Relevanter dokumentierter Hinweis.",
                "question": "Zu prüfen.",
                "citations": list(source.get("citations", []) or []),
            }
        )

    _fa_akten = [
        str(x).strip()
        for x in (analysis_payload.get("fehlende_angaben_akten") or [])
        if str(x).strip()
    ]
    legacy_payload = {
        "document_type": document_type,
        "dokumenttyp_parteien": "\n".join(meta_lines),
        "kurzueberblick": summary_neutral,
        "top3_findings": top3_findings,
        "kernaussagen": fact_items,
        "auffaelligkeiten": issue_items,
        "widersprueche": list(analysis_payload.get("widersprueche_akten") or []),
        "parties": structured_parties,
        "prueffragen": _fa_akten,
        "todos": _dedupe_str_list(
            [str(x).strip() for x in list(analysis_payload.get("todos") or []) if str(x).strip()],
            limit=10,
        ),
        "missing_info": uncertainties,
        "fristen_termine": deadlines,
        "referenzdaten": reference_dates,
    }
    if document_type_extracted:
        legacy_payload["document_type_extracted"] = document_type_extracted
    if isinstance(analysis_payload.get("citation_metrics"), dict):
        legacy_payload["citation_metrics"] = dict(analysis_payload.get("citation_metrics", {}))
    if str(analysis_payload.get("citation_status", "")).strip():
        legacy_payload["citation_status"] = str(analysis_payload.get("citation_status"))
    if "citation_check_ran" in analysis_payload:
        legacy_payload["citation_check_ran"] = bool(analysis_payload.get("citation_check_ran"))
    return legacy_payload


def _prepare_pages_for_llm(
    pages: list[dict],
    reverse_mapping: list[tuple[str, str]],
    apply_redaction: bool = True,
) -> list[dict]:
    llm_pages: list[dict] = []
    for page in pages:
        text = str(page.get("text", "") or "")
        if apply_redaction:
            text = _apply_reverse_mapping(text, reverse_mapping)
        prepared_page = dict(page)
        prepared_page["text"] = text
        llm_pages.append(prepared_page)
    return llm_pages


# ---------------------------------------------------------------------------
# Post-processing: clean up aktenvermerk fields
# ---------------------------------------------------------------------------

def _normalize_for_filter(text: str) -> str:
    """Lowercase + collapse whitespace + strip punctuation."""
    t = text.lower().strip()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _text_similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio on normalized texts."""
    na, nb = _normalize_for_filter(a), _normalize_for_filter(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _filter_akten_sections(akten_payload: dict) -> dict:
    """Cleans up widersprueche and fehlende_angaben after the LLM call (3 filters).

    Filter 1 - dedupe within each field (>85% similarity -> first entry wins).
    Filter 2 - cross-dedupe against kernaussagen (>80% -> remove).
    Filter 3 - fehlende_angaben against widersprueche (identical text -> remove from fehlende_angaben).

    Does NOT mutate the original dict; returns a shallow copy.
    """
    out = dict(akten_payload)

    kern_texts = [
        str(item.get("text", "") or "") if isinstance(item, dict) else str(item)
        for item in list(out.get("kernaussagen") or [])
    ]

    # --- Filter 1+2: widersprueche ---
    raw_w = list(out.get("widersprueche") or [])
    clean_w: list[dict] = []
    for item in raw_w:
        text = str(item.get("text", "") or "") if isinstance(item, dict) else str(item)
        if not text.strip():
            continue
        # Filter 1: dedupe against already-accepted widersprueche
        if any(_text_similarity(text, str(c.get("text", "") or "")) > 0.60 for c in clean_w):
            continue
        # Filter 2: cross-dedupe against kernaussagen
        if any(_text_similarity(text, k) > 0.80 for k in kern_texts if k.strip()):
            continue
        clean_w.append(item if isinstance(item, dict) else {"text": text, "citations": []})
    out["widersprueche"] = clean_w

    # --- Filter 1+2: fehlende_angaben ---
    raw_f = list(out.get("fehlende_angaben") or [])
    clean_f: list[str] = []
    for raw in raw_f:
        text = str(raw).strip()
        if not text:
            continue
        # Filter 1: dedupe against already-accepted fehlende_angaben
        if any(_text_similarity(text, c) > 0.60 for c in clean_f):
            continue
        # Filter 2: cross-dedupe against kernaussagen
        if any(_text_similarity(text, k) > 0.80 for k in kern_texts if k.strip()):
            continue
        clean_f.append(text)

    # --- Filter 3: fehlende_angaben against widersprueche (identical text) ---
    w_texts = [str(c.get("text", "") or "") for c in clean_w]
    clean_f = [
        f for f in clean_f
        if not any(_normalize_for_filter(f) == _normalize_for_filter(w) for w in w_texts if w.strip())
    ]
    out["fehlende_angaben"] = clean_f
    return out


# ---------------------------------------------------------------------------
# Core orchestration: _process_segment
# ---------------------------------------------------------------------------

def _process_segment(
    seg: dict,
    *,
    reverse_mapping: list[tuple[str, str]],
    context_mapping: dict[str, str],
    auto_classify: bool,
    citation_verify_enabled: bool,
    citation_verify_mode: str,
    source_pages_by_no: dict[int, dict],
) -> dict:
    seg_pages = seg.get("pages", [])
    segment_doc_class = segment_doc_class_from_segment(seg)
    analysis_prompt = _document_analysis_prompt(
        segment_doc_class=segment_doc_class,
        allow_llm_doc_class_guess=auto_classify,
        doc_class=segment_doc_class,
    )
    seg_pages_for_llm = _prepare_pages_for_llm(
        seg_pages,
        reverse_mapping,
        False,
    )
    segment_timings_ms: dict[str, int] = {}

    evidence_map = build_evidence_map(seg_pages_for_llm)
    evidence_map = _inject_pseudonym_party_evidence(evidence_map, seg_pages_for_llm)
    segment_text = _segment_text_for_analysis(seg_pages_for_llm)
    seg_pages_source: list[dict[str, Any]] = []
    for idx, seg_page in enumerate(seg_pages_for_llm, start=1):
        page_no = _page_no(seg_page) or idx
        source_page = source_pages_by_no.get(page_no)
        if isinstance(source_page, dict):
            seg_pages_source.append(source_page)
        else:
            seg_pages_source.append(seg_page)
    rubrum_source_text = _segment_text_for_analysis(seg_pages_source)
    _log.info("segment_size", stage="pipeline", chars=len(segment_text))

    # Start both LLM calls in parallel - both are I/O-bound (HTTP) and
    # independent of each other (same input: segment_text). The GIL is not
    # an obstacle here. generate_aktenvermerk does not depend on the result
    # of analyze_document, so true parallelism is possible.
    _parallel_started = perf_counter()

    def _call_structure() -> tuple:
        t0 = perf_counter()
        result = analyze_document(
            segment_text,
            analysis_prompt,
            rubrum_source_text=rubrum_source_text,
            reverse_mapping=reverse_mapping,
        )
        return result, int((perf_counter() - t0) * 1000)

    def _call_akten() -> tuple:
        t0 = perf_counter()
        result = generate_aktenvermerk(segment_text, get_aktenvermerk_prompt())
        return result, int((perf_counter() - t0) * 1000)

    _structure_exc: Exception | None = None
    _akten_exc: Exception | None = None
    summary_result = None
    akten_result = None
    _llm_structure_ms = 0
    _llm_akten_ms = 0

    with ThreadPoolExecutor(max_workers=2) as _executor:
        _structure_future = _executor.submit(_call_structure)
        _akten_future = _executor.submit(_call_akten)
        try:
            summary_result, _llm_structure_ms = _structure_future.result()
        except Exception as exc:
            _llm_structure_ms = int((perf_counter() - _parallel_started) * 1000)
            _structure_exc = exc
        try:
            akten_result, _llm_akten_ms = _akten_future.result()
        except Exception as exc:
            _llm_akten_ms = 0
            _akten_exc = exc

    segment_timings_ms["llm_structure"] = _llm_structure_ms

    if _structure_exc is not None:
        exc = _structure_exc
        # Configuration errors (EnvironmentError, ModuleNotFoundError) and
        # runtime violations (RuntimeError) are permanent - no per-segment
        # retry will help. Re-raise so the caller sees the real error instead
        # of a silent "api_error" fallback.
        # Only transient LLM errors (timeout, rate limit, temporary API outage)
        # may be caught here and handled as a soft fallback.
        if isinstance(exc, (EnvironmentError, ModuleNotFoundError, RuntimeError)):
            raise exc
        _log.error("analyze_document_failed", stage="pipeline", doc_class=segment_doc_class, error=repr(exc))
        return _error_segment_result(
            seg,
            error_msg=repr(exc),
            segment_doc_class=segment_doc_class,
            seg_pages_for_llm=seg_pages_for_llm,
            evidence_map=evidence_map,
            context_mapping=context_mapping,
            auto_classify=auto_classify,
            citation_verify_mode=citation_verify_mode,
            timings_ms=segment_timings_ms,
        )
    chunks_sent = 1

    segment_has_token_usage = bool(getattr(summary_result, "has_token_usage", False))
    segment_token_count = int(summary_result.token_count) if segment_has_token_usage else 0
    segment_prompt_tokens = (
        int(summary_result.prompt_tokens)
        if isinstance(summary_result.prompt_tokens, int)
        else 0
    )
    segment_completion_tokens = (
        int(summary_result.completion_tokens)
        if isinstance(summary_result.completion_tokens, int)
        else 0
    )
    segment_has_prompt_tokens = isinstance(summary_result.prompt_tokens, int)
    segment_has_completion_tokens = isinstance(summary_result.completion_tokens, int)

    analysis_payload = summary_result.parsed if isinstance(summary_result.parsed, dict) else {}
    if isinstance(analysis_payload, dict):
        extracted_doc_type = normalize_doc_class(
            analysis_payload.get("document_type"),
            fallback="",
        )
        if extracted_doc_type and extracted_doc_type != "Unklar":
            analysis_payload["document_type_extracted"] = extracted_doc_type
        chosen_doc_type, chosen_doc_type_source = resolve_display_document_type(
            segment_doc_class=segment_doc_class,
            allow_llm_doc_class_guess=auto_classify,
            analysis_payload=analysis_payload,
            segment_confidence=seg.get("confidence"),
        )
        analysis_payload["document_type"] = chosen_doc_type
        analysis_payload["document_type_source"] = chosen_doc_type_source
        metadata_obj = analysis_payload.get("metadata")
        if not isinstance(metadata_obj, dict):
            metadata_obj = {}
        metadata_obj["source_pages"] = sorted({_page_no(page) for page in seg_pages_for_llm if _page_no(page)})
        analysis_payload["metadata"] = metadata_obj
        analysis_payload["evidence"] = list(analysis_payload.get("evidence", []) or [])
        analysis_payload["chunks_sent"] = chunks_sent
        analysis_payload["citation_verify_mode"] = citation_verify_mode
        if citation_verify_enabled:
            verify_started = perf_counter()
            try:
                verified_payload = maybe_verify_citations_for_fields(
                    parsed=_to_local_verification_payload(analysis_payload),
                    pages=seg_pages_for_llm,
                    fields=("facts", "dates", "issues", "parties", "amounts"),
                    enabled=True,
                )
                segment_timings_ms["llm_verify"] = int((perf_counter() - verify_started) * 1000)
                for field in ("facts", "dates", "issues", "parties", "amounts"):
                    analysis_payload[field] = list(verified_payload.get(field, []) or [])
                analysis_payload["citation_status"] = str(
                    verified_payload.get("citation_status", "unverified")
                )
                citation_metrics = verified_payload.get("citation_metrics", {})
                analysis_payload["citation_metrics"] = (
                    dict(citation_metrics) if isinstance(citation_metrics, dict) else {}
                )
                analysis_payload["citation_check_ran"] = bool(
                    verified_payload.get("citation_check_ran", False)
                )
            except Exception as exc:
                _log.warning("citation_verify_failed", stage="pipeline", error=repr(exc))
                segment_timings_ms["llm_verify"] = int((perf_counter() - verify_started) * 1000)
                analysis_payload["citation_status"] = "verify_failed"
                analysis_payload["citation_check_ran"] = False
                analysis_payload["citation_metrics"] = {}
        else:
            segment_timings_ms["llm_verify"] = 0
            analysis_payload["citation_check_ran"] = False
            analysis_payload["citation_status"] = "unchecked"
        summary_result.parsed = analysis_payload
    else:
        segment_timings_ms["llm_verify"] = 0
    doc_class_fields = resolve_doc_class_fields(
        segment_doc_class=segment_doc_class,
        allow_llm_doc_class_guess=auto_classify,
        analysis_payload=analysis_payload,
    )

    # Aktenvermerk: evaluate and merge the result from the parallel call.
    # Fields are merged into analysis_payload so _to_legacy_segment_analysis picks them up.
    if _akten_exc is not None:
        _log.warning("aktenvermerk_failed", stage="pipeline", error=repr(_akten_exc))
        # Fail-open: aktenvermerk errors do not block the main export
    elif akten_result is not None and akten_result.output_type == "json_ok" and isinstance(akten_result.parsed, dict):
        akten_payload = _filter_akten_sections(akten_result.parsed)
        # Pass the filtered widersprueche through for _to_legacy_segment_analysis
        analysis_payload["widersprueche_akten"] = list(akten_payload.get("widersprueche") or [])
        # todos: merge with the LLM todos from analyze_document
        akten_todos = [
            str(x).strip() for x in list(akten_payload.get("todos") or []) if str(x).strip()
        ]
        existing_todos = list(analysis_payload.get("todos") or [])
        analysis_payload["todos"] = _dedupe_str_list(existing_todos + akten_todos, limit=10)
        # fehlende_angaben -> merge into uncertainties (-> missing_info in the legacy dict)
        # Store the filtered list separately for prueffragen generation
        akten_offene = [
            str(x).strip() for x in list(akten_payload.get("fehlende_angaben") or []) if str(x).strip()
        ]
        analysis_payload["fehlende_angaben_akten"] = akten_offene
        existing_unc = list(analysis_payload.get("uncertainties") or [])
        analysis_payload["uncertainties"] = _dedupe_str_list(existing_unc + akten_offene, limit=25)
        # fristen_termine -> merge as TextItems into dates
        for frist in list(akten_payload.get("fristen_termine") or []):
            frist_text = str((frist or {}).get("text", "")).strip() if isinstance(frist, dict) else str(frist).strip()
            if not frist_text:
                continue
            analysis_payload.setdefault("dates", [])
            analysis_payload["dates"].append({
                "text": frist_text,
                "citations": list((frist or {}).get("citations", []) or []) if isinstance(frist, dict) else [],
            })
        # FIX 1: eckdaten -> LLM-extracted amounts/dates with correct labels
        # Validated items: label + wert must be present
        _raw_eckdaten = list(akten_payload.get("eckdaten") or [])
        analysis_payload["llm_eckdaten"] = [
            {
                "label": str(item.get("label", "") or "").strip(),
                "wert": str(item.get("wert", "") or "").strip(),
                "quelle": str(item.get("quelle", "") or "").strip(),
            }
            for item in _raw_eckdaten
            if isinstance(item, dict)
            and str(item.get("label", "") or "").strip()
            and str(item.get("wert", "") or "").strip()
        ]
        _log.info(
            "eckdaten_source",
            stage="pipeline",
            llm_count=len(analysis_payload["llm_eckdaten"]),
            path="llm" if analysis_payload["llm_eckdaten"] else "regex_fallback",
        )
    segment_timings_ms["llm_aktenvermerk"] = _llm_akten_ms

    legacy_analysis = _to_legacy_segment_analysis(analysis_payload)

    citation_summary = summarize_citation_verification(
        legacy_analysis if isinstance(legacy_analysis, dict) else {},
        ("kernaussagen", "auffaelligkeiten", "top3_findings"),
    )
    citation_summary["citation_check_ran"] = bool(
        citation_verify_enabled and citation_summary.get("citation_check_ran", False)
    )
    if not citation_verify_enabled:
        citation_summary["citation_status"] = "unchecked"
    citation_summary["citation_verify_mode"] = citation_verify_mode
    legacy_analysis["chunks_sent"] = chunks_sent
    legacy_analysis["citation_status"] = citation_summary["citation_status"]
    # A verify error takes precedence over the summarize_citation_verification default ("unverified")
    if analysis_payload.get("citation_status") == "verify_failed":
        legacy_analysis["citation_status"] = "verify_failed"
    legacy_analysis["citation_metrics"] = citation_summary["citation_metrics"]
    legacy_analysis["citation_check_ran"] = citation_summary["citation_check_ran"]
    legacy_analysis["citation_verify_mode"] = citation_verify_mode

    effective_citation_status = str(legacy_analysis["citation_status"])
    seg_coverage = _build_coverage_metrics(
        pages=seg_pages_for_llm,
        chunks_sent=chunks_sent,
        citation_status=effective_citation_status,
        citation_metrics=citation_summary["citation_metrics"],
        citation_check_ran=bool(citation_summary["citation_check_ran"]),
        citation_verify_mode=citation_verify_mode,
    )

    restored_analysis = restore_payload_from_llm(legacy_analysis, context_mapping)
    restored_evidence_map = restore_payload_from_llm(evidence_map, context_mapping)
    restored_raw_analysis = restore_payload_from_llm(analysis_payload, context_mapping)
    # FIX 1: pass LLM eckdaten through with redaction restore applied
    _llm_eckdaten_raw = list(analysis_payload.get("llm_eckdaten") or [])
    restored_llm_eckdaten = restore_payload_from_llm(_llm_eckdaten_raw, context_mapping)

    return {
        "segment_meta": {k: v for k, v in seg.items() if k != "pages"},
        "analysis": restored_analysis,
        "analysis_redacted": legacy_analysis,
        "analysis_raw": restored_raw_analysis,
        "analysis_raw_redacted": analysis_payload,
        "analysis_output_type": summary_result.output_type,
        "evidence_map": restored_evidence_map,
        "evidence_map_redacted": evidence_map,
        "llm_eckdaten": restored_llm_eckdaten,
        "coverage": seg_coverage,
        "model": summary_result.model,
        "doc_class_fields": doc_class_fields,
        "token_count": segment_token_count if segment_has_token_usage else 0,
        "has_token_usage": segment_has_token_usage,
        "prompt_tokens": segment_prompt_tokens if segment_has_prompt_tokens else None,
        "completion_tokens": segment_completion_tokens if segment_has_completion_tokens else None,
        "timings_ms": segment_timings_ms,
    }
