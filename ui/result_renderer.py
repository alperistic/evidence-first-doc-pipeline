"""
ui/result_renderer.py – Haupt-Ergebnis-Rendering für LATE.

render_results() kapselt den gesamten Ergebnis-Block (Coverage, Segment-Expander,
Human-in-the-Loop-Hinweis, Redaction-Summary, Export-Buttons), der zuvor in app.py stand.
Nur Streamlit-Rendering – keine Business-Logik.
"""

import streamlit as st

from governance.doc_class_policy import (
    normalize_doc_class,
    resolve_segment_header_document_type,
)
from governance.redaction import RedactionLeakError
from core.export_render import (
    _filter_llm_eckdaten,
    _merge_llm_regex_eckdaten,
    collect_structured_parties_for_render,
    extract_document_type_for_render,
)
from core.pipeline import _build_redaction_summary
from core.utils import _clean_quote, _safe_text
from services.export_service import build_exports
from ui.components import (
    _collect_all_kaution_amounts,
    _dedupe_summary_items,
    _evidence_by_fields,
    _format_summary_evidence_line,
    _normalize_item,
    _postprocess_eckdaten_items,
    _render_aktenvermerk_block,
    _render_claim_items,
    _render_evidence_map,
    _summary_doc_type_key,
    _verified_claims,
)


def render_results(
    segment_results: list[dict],
    coverage: dict,
    audit_entry: dict,
    *,
    redaction_stats: dict,
    dictionary_report: dict,
    dictionary_entities: list,
    context_mapping: dict,
    doc_hash: str,
    is_short_doc: bool,
    debug_mode: bool,
    redact_required: bool = True,
) -> None:
    """Rendert den vollständigen Ergebnis-Block nach der Segmentanalyse."""

    citation_verify_mode = coverage.get("citation_verify_mode", "disabled")
    claim_verified_pct = int(coverage.get("claim_verified_pct", 0))
    presence_verified_pct = int(coverage.get("presence_verified_pct", 0))
    checks_ran = sum(
        1
        for seg in segment_results
        if bool((seg.get("coverage", {}) or {}).get("citation_check_ran", False))
    )

    # -----------------------------------------------------------------------
    # Coverage-Anzeige
    # -----------------------------------------------------------------------
    if citation_verify_mode == "disabled" or checks_ran == 0:
        st.info(
            f"Coverage: {coverage['pages_total']} Seiten total | "
            f"{coverage['pages_extracted']} extrahiert | "
            f"{coverage['chunks_sent']} Chunks | {len(segment_results)} Segmente | "
            f"claim-verified: n/a (verification disabled) | "
            f"presence-verified: n/a (verification disabled) | "
            f"citation_check_ran: false ({checks_ran}/{len(segment_results)}) | "
            f"mode: {citation_verify_mode}"
        )
    else:
        st.info(
            f"Coverage: {coverage['pages_total']} Seiten total | "
            f"{coverage['pages_extracted']} extrahiert | "
            f"{coverage['chunks_sent']} Chunks | {len(segment_results)} Segmente | "
            f"{claim_verified_pct}% claim-verified | {presence_verified_pct}% presence-verified | "
            f"citation_check_ran: {str(checks_ran > 0).lower()} ({checks_ran}/{len(segment_results)}) | "
            f"mode: {citation_verify_mode}"
        )
    if (
        coverage["pages_extracted"] < max(1, int(coverage["pages_total"] * 0.7))
        or coverage["chars_total"] < max(500, coverage["pages_total"] * 80)
    ):
        st.warning(
            "WARNUNG: Nur Teil des Dokuments wurde verarbeitet. Ergebnisse sind unvollständig.",
            icon="⚠️",
        )
    if debug_mode:
        st.sidebar.caption(
            f"pages_extracted={coverage['pages_extracted']} | "
            f"chars_total={coverage['chars_total']} | "
            f"chunks_sent={coverage['chunks_sent']}"
        )

    st.success("Segmentanalyse abgeschlossen – ohne Vermischung über Dokumentgrenzen.")
    st.subheader("Segmentierte Ergebnisse (einheitliche Struktur)")
    if is_short_doc:
        st.caption("Kurzmodus aktiv: Fokus auf Metadaten, Evidence Map und neutrale Zusammenfassung.")

    # -----------------------------------------------------------------------
    # Segment-Expander-Loop
    # -----------------------------------------------------------------------
    known_kaution_amounts = _collect_all_kaution_amounts(segment_results)
    for idx, seg in enumerate(segment_results, start=1):
        meta = seg.get("segment_meta", {})
        analysis = seg.get("analysis", {})
        analysis_raw = seg.get("analysis_raw", {})
        evidence_map = seg.get("evidence_map", [])
        seg_cov = seg.get("coverage", {})
        title = meta.get("title_guess") or "Dokumentsegment"
        doc_type_guess = meta.get("doc_type_guess") or title or "Unklar"
        display_doc_type, display_doc_type_source = resolve_segment_header_document_type(
            segment=meta if isinstance(meta, dict) else {},
            analysis_payload=analysis_raw if isinstance(analysis_raw, dict) else {},
        )
        start_page = meta.get("page_start", meta.get("start_page", "?"))
        end_page = meta.get("page_end", meta.get("end_page", "?"))
        confidence = meta.get("confidence", 0.0)
        reason = meta.get("reason", "Heuristische Segmentierung")

        with st.expander(
            f"Dokument {idx} (Seite {start_page}-{end_page}): {display_doc_type}",
            expanded=(idx == 1),
        ):
            if not isinstance(analysis, dict) or seg.get("analysis_output_type") != "json_ok":
                st.warning("Segment-Ergebnis konnte nicht strukturiert erzeugt werden.")
                continue

            st.markdown("**1) Dokument-Metadaten**")
            segment_guess_display = (
                doc_type_guess
                if normalize_doc_class(doc_type_guess) != "Unklar"
                else "aus Segment nicht eindeutig"
            )
            st.caption(
                f"Doc-Typ (Segment-Guess): {segment_guess_display} | "
                f"Doc-Typ (Anzeige): {display_doc_type} | "
                f"Doc-Typ (Quelle): {display_doc_type_source} | "
                f"Doc-Typ (Extraktion): {_safe_text(str(analysis.get('dokumenttyp_parteien', '')))} | "
                f"Segment-Seitenrange: {start_page}-{end_page} | "
                f"Confidence: {confidence:.2f} | Citation-Status: {analysis.get('citation_status', 'n/a')}"
            )
            st.caption(f"Heuristik: {reason}")
            if debug_mode:
                boundary_score = meta.get("boundary_score")
                boundary_reasons = [str(r) for r in list(meta.get("boundary_reasons", []) or [])]
                if boundary_score is not None:
                    st.caption(f"Boundary-Score: {float(boundary_score):.2f}")
                if boundary_reasons:
                    st.caption(f"Boundary-Reasons: {' | '.join(boundary_reasons[:6])}")
            st.caption(
                f"Coverage Segment: {seg_cov.get('pages_total', 0)} Seiten | {seg_cov.get('chunks_sent', 0)} Chunks"
            )

            summary_tab, aktennotiz_tab, evidence_tab = st.tabs(
                ["Zusammenfassung", "Aktennotiz (intern)", "Belege / Evidence"]
            )
            with summary_tab:
                st.markdown("**Zusammenfassung (neutral)**")
                st.markdown("**Dokumenttyp + Parteien (nur belegte Fakten)**")
                st.markdown(
                    f"– Dokumenttyp: {_safe_text(extract_document_type_for_render(analysis))}"
                )
                doc_type = _summary_doc_type_key(
                    str(analysis.get("dokumenttyp_parteien", "") or ""),
                    str(title or ""),
                )
                structured_parties = collect_structured_parties_for_render(
                    analysis,
                    max_items=4,
                    debug=debug_mode,
                )
                if structured_parties:
                    for party in structured_parties:
                        role = _safe_text(str(party.get("role", "Partei") or "Partei"))
                        value = _safe_text(str(party.get("value", "") or ""))
                        st.markdown(f"– {role}: {value}")
                        citations = [
                            dict(c)
                            for c in list(party.get("citations", []) or [])
                            if isinstance(c, dict)
                        ]
                        if citations:
                            citation = citations[0]
                            page = citation.get("page_number", "?")
                            quote = _safe_text(
                                _clean_quote(str(citation.get("quote_snippet", "") or ""))
                            )
                            if quote:
                                st.markdown(f"  Quelle S. {page}: {quote}")
                else:
                    st.markdown("– nicht genannt")

                st.markdown("**Eckdaten (Beträge / Fristen / Termine)**")
                # FIX 1/4: LLM-eckdaten haben Vorrang; spezifische Regex-Items ergänzen (FIX 4 merge).
                llm_eckdaten = _filter_llm_eckdaten(list(seg.get("llm_eckdaten") or []))
                _regex_ev_fields = (
                    "betraege", "fristen_termine", "datum_beginn", "kuendigung_fristen",
                    "miete", "nebenkosten", "kaution", "rueckstand", "schadensersatz", "streitwert",
                )
                _regex_ev_items = _evidence_by_fields(evidence_map, _regex_ev_fields, max_items=10)
                merged_eckdaten = _merge_llm_regex_eckdaten(llm_eckdaten, _regex_ev_items, doc_type)
                if merged_eckdaten:
                    for item in merged_eckdaten:
                        label = _safe_text(str(item.get("label", "") or ""))
                        wert = _safe_text(str(item.get("wert", "") or ""))
                        if label and wert:
                            st.markdown(f"– {label}: {wert}")
                else:
                    eckdaten = _dedupe_summary_items(
                        _postprocess_eckdaten_items(
                            _regex_ev_items,
                            doc_type,
                            known_kaution_amounts=known_kaution_amounts,
                        )
                    )
                    if eckdaten:
                        for item in eckdaten:
                            st.markdown(_format_summary_evidence_line(item, doc_type))
                    else:
                        st.markdown("– missing (Rückfrage erforderlich)")

                st.markdown("**Kurzüberblick (max. 7 Bulletpoints, nur belegte Fakten)**")
                verified_overview = _verified_claims(
                    list(analysis.get("kernaussagen", [])), max_items=7
                )
                if verified_overview:
                    _render_claim_items(verified_overview, max_items=7)
                else:
                    st.markdown("– missing (Rückfrage erforderlich)")

                st.markdown("**Unklarheiten im Dokument**")
                unclear_rendered = False
                for item in list(analysis.get("missing_info", []))[:10]:
                    st.markdown(f"– {item}")
                    unclear_rendered = True
                for raw in list(analysis.get("auffaelligkeiten", []))[:10]:
                    a_item = _normalize_item(raw)
                    citations = (
                        a_item.get("citations", [])
                        if isinstance(a_item.get("citations"), list)
                        else []
                    )
                    if citations:
                        continue
                    text = str(a_item.get("text", "")).strip()
                    if text:
                        st.markdown(f"– {text}")
                        unclear_rendered = True
                if not unclear_rendered:
                    st.markdown("– keine")

            with aktennotiz_tab:
                if is_short_doc:
                    with st.expander("Aktennotiz (intern) (optional)", expanded=False):
                        _render_aktenvermerk_block(analysis, evidence_map, show_title=False)
                else:
                    _render_aktenvermerk_block(analysis, evidence_map, show_title=True)

            with evidence_tab:
                st.markdown("**Evidence Map (Tabelle)**")
                _render_evidence_map(evidence_map)

    # -----------------------------------------------------------------------
    # Human-in-the-Loop-Hinweis
    # -----------------------------------------------------------------------
    st.info(
        "**Human-in-the-Loop:** Diese Zusammenfassung ist ein KI-generierter Vorschlag. "
        "Alle Angaben sind anwaltlich zu verifizieren, bevor Maßnahmen ergriffen werden."
    )

    # -----------------------------------------------------------------------
    # Redaction-Summary
    # -----------------------------------------------------------------------
    with st.expander("Redaction-Summary", expanded=False):
        dict_masks = list((dictionary_report or {}).get("matched_masks", []))
        dict_counts = dict((dictionary_report or {}).get("mask_counts", {}) or {})
        dict_entity_hits = int((dictionary_report or {}).get("matched_entities_count", 0))
        if dict_entity_hits > 0:
            st.caption(f"{dict_entity_hits} Entities zusätzlich maskiert:")
            st.table(
                [{"Maskierung": mask, "Anzahl": dict_counts.get(mask, 0)} for mask in dict_masks]
            )
        elif dictionary_entities:
            st.caption("Keine zusätzlichen Dictionary-Entity-Treffer.")

        red_summary = _build_redaction_summary(redaction_stats.get("mask_counts"))
        if red_summary:
            st.caption("Vor LLM-Übertragung maskierte Muster:")
            st.table(
                [
                    {"Maskierung": label, "Anzahl": count}
                    for label, count in red_summary.items()
                ]
            )
            placeholders = list(redaction_stats.get("placeholder_keys") or [])
            if placeholders:
                st.caption("Platzhalter-Keys:")
                st.code(", ".join(placeholders), language=None)
            mask_types = dict(redaction_stats.get("mask_types") or {})
            if mask_types:
                st.caption("Mask types (counts):")
                st.table([{"Typ": k, "Anzahl": v} for k, v in mask_types.items()])
        else:
            st.caption("Keine Maskierungen erkannt.")

    # -----------------------------------------------------------------------
    # Export-Buttons
    # -----------------------------------------------------------------------
    st.subheader("Export")
    filename_stem = f"LATE_Segmente_{doc_hash[:8]}"
    export_selection = st.radio(
        "Export-Inhalt",
        options=["Zusammenfassung", "Aktennotiz", "Beides"],
        index=2,
        horizontal=True,
    )
    try:
        exports = build_exports(
            segment_results,
            context_mapping,
            coverage,
            audit_entry,
            export_selection,
            redact_required=redact_required,
        )
    except RedactionLeakError as e:
        st.error(
            f"⚠️ Export gesperrt: Redaction unvollständig ({e.leak_count} Treffer). "
            f"Dokument wird nicht ausgegeben. Bitte Support kontaktieren."
        )
        st.stop()

    internal_content_suffix = {
        "Zusammenfassung": "Zusammenfassung",
        "Aktennotiz": "Aktennotiz",
        "Beides": "Beides",
    }[export_selection]
    external_content_suffix = "Zusammenfassung"

    pdf_col_internal, pdf_col_external = st.columns(2)
    with pdf_col_internal:
        st.download_button(
            label="PDF – Kanzleiakte (intern, Klartext)",
            data=exports.internal_pdf_bytes,
            file_name=f"{filename_stem}_Kanzleiakte_{internal_content_suffix}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    with pdf_col_external:
        external_pdf_clicked = st.download_button(
            label="PDF – Mandantenfassung (redigiert)",
            data=exports.external_pdf_bytes,
            file_name=f"{filename_stem}_Mandantenfassung_redigiert_{external_content_suffix}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
        if external_pdf_clicked and export_selection != "Zusammenfassung":
            st.info("Für Mandantenfassung wird nur die Zusammenfassung exportiert.")

    docx_col_internal, docx_col_external = st.columns(2)
    with docx_col_internal:
        st.download_button(
            label="DOCX – Kanzleiakte (intern, Klartext)",
            data=exports.internal_docx_bytes,
            file_name=f"{filename_stem}_Kanzleiakte_{internal_content_suffix}.docx",
            mime=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            use_container_width=True,
        )
    with docx_col_external:
        external_docx_clicked = st.download_button(
            label="DOCX – Mandantenfassung (redigiert)",
            data=exports.external_docx_bytes,
            file_name=f"{filename_stem}_Mandantenfassung_redigiert_{external_content_suffix}.docx",
            mime=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            use_container_width=True,
        )
        if external_docx_clicked and export_selection != "Zusammenfassung":
            st.info("Für Mandantenfassung wird nur die Zusammenfassung exportiert.")

    if exports.internal_placeholder_hits:
        hit_preview = ", ".join(exports.internal_placeholder_hits[:8])
        st.warning(
            f"Hinweis: {len(exports.internal_placeholder_hits)} Platzhalter konnten nicht restauriert werden: "
            f"{hit_preview}"
        )
