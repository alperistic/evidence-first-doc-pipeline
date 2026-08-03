"""
app.py – LATE Demo: Dokumenten-Zusammenfassung für Rechtsanwaltskanzleien
§203 StGB-konform: Kein Dokument wird gespeichert, kein Klartext geloggt.
"""

from dotenv import load_dotenv
load_dotenv()
from time import perf_counter

from health import start_health_server as _start_health
_start_health()

import streamlit as st

from governance.audit import get_recent_logs
from core.extractor import compute_sha256, extract_text_with_meta
from governance.policy import load_policy
from governance.redaction import prepare_for_llm
from core.config import get_config
from core.utils import _page_no
from core.pipeline import (
    _build_coverage_metrics,
    _build_redaction_summary,
    _parse_dictionary_entities,
    _build_reverse_mapping,
    _prepare_pages_for_llm,
)
from core.citation import summarize_citation_verification
from ui.result_renderer import render_results
from workflows.doc_summary import DocSummaryWorkflow

# ---------------------------------------------------------------------------
# Seiten-Konfiguration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="LATE – Dokument-Analyse",
    page_icon="⚖️",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Verschwiegenheitshinweis (Kern-Anforderung)
# ---------------------------------------------------------------------------
st.warning(
    "**Verschwiegenheitshinweis:** Dieses System verarbeitet mandantenbezogene Unterlagen. "
    "Es werden ausschließlich Dokument-Hashes (SHA-256) gespeichert – kein Volltext, "
    "kein Dateiname. Die Übertragung erfolgt verschlüsselt an Azure OpenAI (EU-Region, AVV). "
    "§203-gerecht ausgestaltet über Berufsgeheimnisträger-Zusatzvereinbarung. "
    "Eingabe-Anonymisierung ist optional konfigurierbar (Sidebar). "
    "Die generierten Zusammenfassungen sind Vorschläge und ersetzen keine anwaltliche Prüfung.",
    icon="⚠️",
)

st.title("LATE – Rechtsdokument-Analyse")

loaded_policy = load_policy()
policy = loaded_policy.config
workflow = DocSummaryWorkflow()

# ---------------------------------------------------------------------------
# Sidebar: Einstellungen
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Einstellungen")
    profile_labels = {
        "balanced": "Sicher (Standard)",
        "strict": "Maximal sicher (minimiert)",
    }
    can_override_profile = policy.dev_mode and policy.allow_ui_override
    if can_override_profile:
        override_options = ["balanced", "strict"]
        default_profile = workflow.resolve_effective_profile(loaded_policy)
        if default_profile not in override_options:
            default_profile = "balanced"
        selected_profile = st.selectbox(
            "Modus (DEV-Override)",
            override_options,
            index=override_options.index(default_profile),
            format_func=lambda p: profile_labels.get(p, p),
            help="Nur im DEV-Modus übersteuerbar. Fast ist nicht in der Produkt-UI verfügbar.",
        )
        st.caption("DEV-Override aktiv: Modus wird aus Sidebar übernommen.")
    else:
        selected_profile = None
    effective_profile = workflow.resolve_effective_profile(
        loaded_policy,
        profile_override=selected_profile,
    )
    if not can_override_profile:
        if effective_profile == "strict":
            st.info("Modus: Maximal sicher (minimiert)")
        else:
            st.info("Modus: Sicher (Standard) – nur pseudonymisierte Inhalte werden verarbeitet.")
        st.caption(f"Read-only aus {loaded_policy.source_path.name}.")

    enable_redaction = st.toggle(
        "Eingabe-Anonymisierung (Redaction)",
        value=False,
        help=(
            "Maskiert Personen, Adressen und Entities vor dem LLM-Call. "
            "Default OFF: §203-Compliance läuft über den Azure-Vertrag (AVV), "
            "nicht über Input-Redaction."
        ),
    )

    advanced_expander = st.expander("Erweitert (DEV)", expanded=False)
    with advanced_expander:
        debug_mode = st.toggle("Debug anzeigen", value=False)
        auto_classify = True  # FIX 4: always active – LLM doc class guess ist fester Pipeline-Bestandteil
        dictionary_entities_input = st.text_area(
            "Zusätzliche Entities sicher maskieren (optional)",
            value="",
            help="Eine Entity pro Zeile, z. B. Namen, Organisationen oder Anschriften.",
            height=120,
        )
        env_enable_fuzzy = get_config().enable_fuzzy
        dictionary_fuzzy = st.toggle(
            "Fuzzy Matching (approx.)",
            value=env_enable_fuzzy,
            help="Bei aktivem Fuzzy-Matching werden ähnliche Schreibweisen mit maskiert.",
        )
        dictionary_threshold = st.slider(
            "Fuzzy-Threshold",
            min_value=85,
            max_value=95,
            value=92,
            step=1,
            disabled=not dictionary_fuzzy,
        )
    dictionary_entities = _parse_dictionary_entities(dictionary_entities_input)
    runtime_config = workflow.build_runtime_config(
        loaded_policy=loaded_policy,
        profile_override=selected_profile,
        auto_classify=auto_classify,
        dictionary_entities=dictionary_entities,
        dictionary_fuzzy=dictionary_fuzzy,
        dictionary_threshold=dictionary_threshold,
    )
    allow_plaintext_preview = runtime_config.allow_plaintext_preview
    citation_verify_enabled = runtime_config.citation_verify_enabled
    citation_verify_mode = runtime_config.citation_verify_mode
    must_use_redacted = runtime_config.must_use_redacted
    use_redaction = runtime_config.use_redaction

    if enable_redaction:
        if must_use_redacted and runtime_config.redact_required:
            st.success("✅ Azure EU-Region · SHA-256 Audit-Trail · Pseudonymisierung aktiv")
        elif not must_use_redacted:
            st.warning("LLM-Eingabe: Profil fast aktiv (nur DEV/Policy).")
    else:
        st.info("✅ Azure EU-Region · SHA-256 Audit-Trail · Eingabe-Anonymisierung deaktiviert (AVV)")

    with advanced_expander:
        st.caption(
            "classify_max_chars "
            f"(legacy/separate classify call): {runtime_config.classify_max_chars_legacy}"
        )
        st.caption(
            "segment_max_pages "
            f"(legacy/no hard split): {runtime_config.segment_max_pages_legacy} | "
            f"boundary_threshold: {runtime_config.segment_boundary_threshold}"
        )
        st.caption(
            f"redact_required: {runtime_config.redact_required} | "
            f"fail_closed: {runtime_config.fail_closed}"
        )
        st.caption(f"citation_verify_mode: {citation_verify_mode} (LATE_CITATION_VERIFY)")
        if debug_mode and not allow_plaintext_preview:
            st.caption("Plaintext-Preview bleibt deaktiviert (ALLOW_PLAINTEXT_PREVIEW=0).")

    st.divider()
    st.caption("LATE v0.1 – MVP Demo")

# ---------------------------------------------------------------------------
# Session-State initialisieren
# ---------------------------------------------------------------------------
for key, default in [
    ("state_key", None),
    ("confirmed", False),
    ("result", None),
    ("audit_entry", None),
    ("detected_class", None),
    ("classify_used_chars", 0),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ---------------------------------------------------------------------------
# Hauptbereich: Upload
# ---------------------------------------------------------------------------
uploaded_file = st.file_uploader(
    "Dokument hochladen (PDF oder DOCX)",
    type=["pdf", "docx"],
    help="Das Dokument wird nicht gespeichert. Nur der SHA-256-Hash wird im Audit-Log erfasst.",
)

if uploaded_file is not None:
    file_bytes = uploaded_file.read()
    doc_hash = compute_sha256(file_bytes)

    # Neues Dokument oder geänderte Policy/Profil-Einstellung → State zurücksetzen
    current_state_key = (
        doc_hash,
        runtime_config.cache_scope_key,
    )
    if st.session_state.state_key != current_state_key:
        st.session_state.state_key = current_state_key
        st.session_state.confirmed = False
        st.session_state.result = None
        st.session_state.audit_entry = None
        st.session_state.detected_class = None
        st.session_state.classify_used_chars = 0

    with st.expander("Dokument-Metadaten", expanded=False):
        st.code(f"SHA-256: {doc_hash}", language=None)
        st.caption(
            f"Dateiname (nur lokal): {uploaded_file.name} | "
            f"Größe: {len(file_bytes):,} Bytes"
        )

    # Textextraktion
    try:
        extract_t0 = perf_counter()
        extraction = extract_text_with_meta(file_bytes, uploaded_file.name)
        extract_ms = int((perf_counter() - extract_t0) * 1000)
        raw_text = extraction.text
        pages = extraction.pages
        segments: list[dict] = []
        segment_ms = 0
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    if not raw_text.strip():
        st.error(
            "Kein lesbarer Text im Dokument gefunden. "
            "Bei gescannten PDFs bitte OCR-Fallback konfigurieren "
            "(AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT / AZURE_DOCUMENT_INTELLIGENCE_KEY)."
        )
        st.stop()

    if extraction.ocr_used:
        st.info(
            "Dokument wurde per OCR verarbeitet (gescannte PDF erkannt). "
            "Bitte Ergebnis anwaltlich besonders sorgfältig gegenprüfen."
        )
    else:
        st.caption("Textextraktion: Direkter Dokumenttext (kein OCR-Fallback).")

    dictionary_report: dict = {"matched_entities_count": 0, "matched_masks": [], "mask_counts": {}}
    redaction_stats: dict = {}
    redacted_text = ""
    context_mapping: dict[str, str] = {}
    reverse_mapping: list[tuple[str, str]] = []
    redaction_applied = False
    pseudonymization_applied = False
    classify_used_chars = 0

    if enable_redaction:
        try:
            context = prepare_for_llm(
                raw_text,
                {
                    "dictionary_entities": list(runtime_config.dictionary_entities),
                    "dictionary_fuzzy": runtime_config.dictionary_fuzzy,
                    "dictionary_threshold": runtime_config.dictionary_threshold,
                    "pseudonymize_people": True,
                },
            )
        except Exception as exc:
            if must_use_redacted and runtime_config.redact_required and runtime_config.fail_closed:
                st.error(
                    "Hard Gate aktiv: Redaction fehlgeschlagen, Analyse wird aus Sicherheitsgründen abgebrochen. "
                    f"Fehler: {exc}"
                )
                st.stop()
            st.error(
                "Redaction fehlgeschlagen. Aus Datenschutzgründen wird kein LLM-Call ausgeführt. "
                f"Fehler: {exc}"
            )
            st.stop()

        redacted_text = str(context.redacted_text or "")
        context_mapping = dict(context.mapping or {})
        reverse_mapping = _build_reverse_mapping(context_mapping)
        redaction_stats = dict(context.stats or {})
        dictionary_report = dict(redaction_stats.get("dictionary") or dictionary_report)
        redaction_applied = bool(redaction_stats.get("redaction_applied"))
        pseudonymization_applied = bool((redaction_stats.get("pseudonymization") or {}).get("entity_count", 0))

        text_for_llm = redacted_text
        if must_use_redacted:
            if not isinstance(context_mapping, dict) or not context_mapping:
                st.error("Hard Gate aktiv: Mapping fehlt oder ist ungültig. LLM-Call wird blockiert.")
                st.stop()
            if not text_for_llm.strip():
                st.error(
                    "Hard Gate aktiv: redacted_text ist leer. "
                    "Es wird kein LLM-Call ausgeführt (fail_closed=true)."
                )
                st.stop()
        elif not text_for_llm.strip():
            st.warning(
                "redacted_text ist leer; es wird eine minimierte Ersatznutzlast verwendet "
                "(kein Fallback auf Originaltext)."
            )
            text_for_llm = "[REDACTION_EMPTY]"
    else:
        text_for_llm = raw_text

    redacted_pages = _prepare_pages_for_llm(
        pages,
        reverse_mapping,
        apply_redaction=enable_redaction,
    )
    segment_t0 = perf_counter()
    segments = workflow.build_segments(redacted_pages, runtime_config=runtime_config)
    segment_ms = int((perf_counter() - segment_t0) * 1000)

    if segments:
        avg_pages_per_segment = round(len(redacted_pages) / max(len(segments), 1), 2)
        first_page = segments[0].get("page_start", segments[0].get("start_page", "?"))
        last_page = segments[-1].get("page_end", segments[-1].get("end_page", "?"))
        st.caption(
            f"Segmentierung: Segmente: {len(segments)} (dokumentbasiert), "
            f"Durchschnitt: {avg_pages_per_segment:.2f} Seiten/Segment "
            f"(S. {first_page}-{last_page})."
        )
        with st.expander("Erkannte Segmente", expanded=False):
            rows: list[dict[str, str]] = []
            for seg in segments:
                row: dict[str, str] = {
                    "Segment": str(seg.get("segment_id")),
                    "Seiten": (
                        f"{seg.get('page_start', seg.get('start_page', '?'))}-"
                        f"{seg.get('page_end', seg.get('end_page', '?'))}"
                    ),
                    "Typ": str(seg.get("doc_type_guess", "Unklar")),
                    "Confidence": f"{float(seg.get('confidence', 0.0)):.2f}",
                }
                if debug_mode:
                    boundary_score = seg.get("boundary_score")
                    row["Boundary-Score"] = "n/a" if boundary_score is None else f"{float(boundary_score):.2f}"
                    row["Boundary-Reasons"] = " | ".join(
                        [str(reason) for reason in list(seg.get("boundary_reasons", []) or [])[:3]]
                    )
                rows.append(row)
            st.table(
                rows
            )

    if not runtime_config.auto_classify:
        classify_used_chars = 0

    # -----------------------------------------------------------------------
    # Redaction Preview + Bestätigung
    # -----------------------------------------------------------------------
    if use_redaction and not st.session_state.confirmed:
        st.subheader("Anonymisierungs-Vorschau")
        if st.button("Analyse starten", type="primary", key="start_after_redaction"):
            st.session_state.confirmed = True
            st.rerun()

        dict_masks = list((dictionary_report or {}).get("matched_masks", []))
        dict_counts = dict((dictionary_report or {}).get("mask_counts", {}) or {})
        dict_entity_hits = int((dictionary_report or {}).get("matched_entities_count", 0))
        if dict_entity_hits > 0:
            st.success(f"{dict_entity_hits} Entities zusätzlich maskiert.", icon="✅")
            st.table(
                [{"Maskierung": mask, "Anzahl": dict_counts.get(mask, 0)} for mask in dict_masks]
            )
        elif dictionary_entities:
            st.info("Keine zusätzlichen Entity-Treffer im Text gefunden.", icon="ℹ️")

        red_summary = _build_redaction_summary(redaction_stats.get("mask_counts"))
        total_masks = sum(red_summary.values())
        if red_summary:
            st.success(
                f"Regex/Heuristik-Maskierungen: {total_masks}",
                icon="✅",
            )
            st.table(
                [{"Maskierung": label, "Anzahl": count} for label, count in red_summary.items()]
            )
            placeholders = list(redaction_stats.get("placeholder_keys") or [])
            if placeholders:
                st.caption("Verwendete Platzhalter")
                st.code(", ".join(placeholders), language=None)
            mask_types = dict(redaction_stats.get("mask_types") or {})
            if mask_types:
                st.caption("Mask types (counts)")
                st.table([{"Typ": k, "Anzahl": v} for k, v in mask_types.items()])
        else:
            st.info(
                "Keine anonymisierbaren Datenpunkte (IBAN, Aktenzeichen, "
                "Telefon, E-Mail) im Dokument erkannt.",
                icon="ℹ️",
            )
        if debug_mode and allow_plaintext_preview:
            st.warning("Plaintext-Debug aktiv (nur lokal, nicht exportiert/geloggt).", icon="⚠️")
            st.text_area("Plaintext Preview (Debug)", value=raw_text[:2000], height=180, disabled=True)
        st.stop()

    # -----------------------------------------------------------------------
    # Analyse starten
    # -----------------------------------------------------------------------
    if st.session_state.result is None:
        if st.button("Analyse starten", type="primary"):
            source_pages_by_no = {
                _page_no(page): page
                for page in pages
                if _page_no(page)
            }

            with st.spinner("Analyse läuft …"):
                try:
                    analysis = workflow.run_analysis(
                        segments,
                        doc_hash=doc_hash,
                        runtime_config=runtime_config,
                        reverse_mapping=reverse_mapping,
                        context_mapping=context_mapping,
                        source_pages_by_no=source_pages_by_no,
                        redaction_applied=redaction_applied,
                        pseudonymization_applied=pseudonymization_applied,
                        classify_used_chars=classify_used_chars,
                        extract_ms=extract_ms,
                        segment_ms=segment_ms,
                    )
                except EnvironmentError as exc:
                    st.error(str(exc))
                    st.stop()

            if analysis["cache_hit"]:
                st.info("Ergebnis aus Cache geladen (gleiches Dokument + Runtime-Konfiguration + Modell).")
            st.session_state.result = analysis["result_bundle"]
            st.session_state.audit_entry = analysis["audit_entry"]

    # -----------------------------------------------------------------------
    # Ergebnis anzeigen
    # -----------------------------------------------------------------------
    if st.session_state.result is not None:
        result_bundle = st.session_state.result
        audit_entry = st.session_state.audit_entry
        segment_results = result_bundle.get("segment_results", [])
        overall_chunks = sum(int(seg.get("coverage", {}).get("chunks_sent", 0)) for seg in segment_results)
        citation_summaries = [
            summarize_citation_verification(
                seg.get("analysis", {}) if isinstance(seg.get("analysis"), dict) else {},
                ("kernaussagen", "auffaelligkeiten", "top3_findings"),
            )
            for seg in segment_results
        ]
        claim_verified_items = sum(
            int((summary.get("citation_metrics", {}) or {}).get("claim_verified_items", (summary.get("citation_metrics", {}) or {}).get("verified_items", 0)))
            for summary in citation_summaries
        )
        claim_unverified_items = sum(
            int((summary.get("citation_metrics", {}) or {}).get("claim_unverified_items", (summary.get("citation_metrics", {}) or {}).get("unverified_items", 0)))
            for summary in citation_summaries
        )
        presence_verified_items = sum(
            int((summary.get("citation_metrics", {}) or {}).get("presence_verified_items", (summary.get("citation_metrics", {}) or {}).get("verified_items", 0)))
            for summary in citation_summaries
        )
        total_claims = max(claim_verified_items + claim_unverified_items, 0)
        claim_verified_pct = round((claim_verified_items / total_claims) * 100) if total_claims else 0
        presence_verified_pct = round((presence_verified_items / total_claims) * 100) if total_claims else 0
        checks_ran = sum(
            1 for seg in segment_results if bool((seg.get("coverage", {}) or {}).get("citation_check_ran", False))
        )
        coverage = _build_coverage_metrics(
            pages=pages,
            chunks_sent=overall_chunks,
            citation_status=(
                "verified"
                if total_claims > 0 and claim_unverified_items == 0
                else "partial"
                if claim_verified_items > 0
                else "unverified"
            ),
            citation_metrics={
                "verified_items": claim_verified_items,
                "unverified_items": claim_unverified_items,
                "claim_verified_items": claim_verified_items,
                "claim_unverified_items": claim_unverified_items,
                "presence_verified_items": presence_verified_items,
                "presence_unverified_items": max(total_claims - presence_verified_items, 0),
                "total_items": total_claims,
            },
            citation_check_ran=checks_ran > 0,
            citation_verify_mode=citation_verify_mode,
        )

        is_short_doc = (
            int(getattr(extraction, "pages_total", 0)) <= 2
            or int(getattr(extraction, "chars_total", 0)) < 3000
        )
        render_results(
            segment_results,
            coverage,
            audit_entry,
            redaction_stats=redaction_stats,
            dictionary_report=dictionary_report,
            dictionary_entities=dictionary_entities,
            context_mapping=context_mapping,
            doc_hash=doc_hash,
            is_short_doc=is_short_doc,
            debug_mode=debug_mode,
            redact_required=runtime_config.redact_required,
        )


# ---------------------------------------------------------------------------
# Audit-Log Ansicht
# ---------------------------------------------------------------------------
with st.expander("Audit-Log (letzte 50 Einträge)", expanded=False):
    logs = get_recent_logs()
    if logs:
        st.dataframe(
            logs,
            use_container_width=True,
            column_config={
                "doc_hash": st.column_config.TextColumn("Dokument-Hash (SHA-256)", width="large"),
                "timestamp": st.column_config.TextColumn("Zeitstempel (UTC)"),
                "doc_class": st.column_config.TextColumn("Klasse"),
                "policy_profile": st.column_config.TextColumn("Policy-Profil"),
                "redaction_applied": st.column_config.NumberColumn("Redaction angewandt"),
                "pseudonymization_applied": st.column_config.NumberColumn("Pseudonymisierung angewandt"),
                "classify_used_chars": st.column_config.NumberColumn("Classify-Zeichen"),
                "token_count": st.column_config.NumberColumn("Tokens"),
                "model": st.column_config.TextColumn("Modell"),
                "citation_status": st.column_config.TextColumn("Citation-Status"),
            },
        )
    else:
        st.caption("Noch keine Einträge vorhanden.")
