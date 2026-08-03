"""
ui/result_renderer.py – Main result rendering for the document analysis pipeline.

render_results() encapsulates the entire result block (coverage, segment
expanders, human-in-the-loop notice, redaction summary, export buttons)
that previously lived in app.py. Streamlit rendering only - no business logic.
"""

from html import escape

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


_SOURCE_QUOTE_STYLE = (
    "border-left:3px solid #1f4e79;background:#f4f6f8;padding:6px 10px;"
    "margin:2px 0 10px 18px;font-size:0.85rem;color:#31353b;"
)


def _render_source_quote(page: object, quote: str) -> None:
    """Render a citation as a visually distinct source block.

    The quote comes from an uploaded document, so it is escaped before being
    placed into markup.
    """
    st.markdown(
        f"<div style='{_SOURCE_QUOTE_STYLE}'>"
        f"<strong>p.&nbsp;{escape(str(page))}</strong> &middot; &bdquo;{escape(quote)}&ldquo;"
        f"</div>",
        unsafe_allow_html=True,
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
    """Renders the full result block after segment analysis."""

    citation_verify_mode = coverage.get("citation_verify_mode", "disabled")
    claim_verified_pct = int(coverage.get("claim_verified_pct", 0))
    presence_verified_pct = int(coverage.get("presence_verified_pct", 0))
    checks_ran = sum(
        1
        for seg in segment_results
        if bool((seg.get("coverage", {}) or {}).get("citation_check_ran", False))
    )

    # -----------------------------------------------------------------------
    # Coverage display
    # -----------------------------------------------------------------------
    # Headline numbers first; the raw counters stay available under "debug".
    verification_ran = citation_verify_mode != "disabled" and checks_ran > 0
    cov_cols = st.columns(4)
    cov_cols[0].metric(
        "Pages processed",
        f"{coverage['pages_extracted']}/{coverage['pages_total']}",
    )
    cov_cols[1].metric("Documents found", len(segment_results))
    cov_cols[2].metric(
        "Claims with verified source",
        f"{claim_verified_pct}%" if verification_ran else "n/a",
        help=(
            "Share of extracted statements whose citation was matched back to "
            "the source text."
            if verification_ran
            else "Citation verification is disabled (DOCPIPE_CITATION_VERIFY)."
        ),
    )
    cov_cols[3].metric(
        "Quotes located in source",
        f"{presence_verified_pct}%" if verification_ran else "n/a",
        help=(
            "Share of quoted snippets found verbatim on the cited page."
            if verification_ran
            else "Citation verification is disabled (DOCPIPE_CITATION_VERIFY)."
        ),
    )
    if debug_mode:
        st.caption(
            f"chunks_sent: {coverage['chunks_sent']} | "
            f"citation_check_ran: {str(checks_ran > 0).lower()} "
            f"({checks_ran}/{len(segment_results)}) | "
            f"mode: {citation_verify_mode}"
        )
    if (
        coverage["pages_extracted"] < max(1, int(coverage["pages_total"] * 0.7))
        or coverage["chars_total"] < max(500, coverage["pages_total"] * 80)
    ):
        st.warning(
            "WARNING: only part of the document was processed. Results are incomplete.",
            icon="⚠️",
        )
    if debug_mode:
        st.sidebar.caption(
            f"pages_extracted={coverage['pages_extracted']} | "
            f"chars_total={coverage['chars_total']} | "
            f"chunks_sent={coverage['chunks_sent']}"
        )

    st.success("Segment analysis complete - no mixing across document boundaries.")
    st.subheader("Segmented results (unified structure)")
    if is_short_doc:
        st.caption("Short-document mode active: focus on metadata, evidence map, and neutral summary.")

    # -----------------------------------------------------------------------
    # Segment expander loop
    # -----------------------------------------------------------------------
    known_kaution_amounts = _collect_all_kaution_amounts(segment_results)
    for idx, seg in enumerate(segment_results, start=1):
        meta = seg.get("segment_meta", {})
        analysis = seg.get("analysis", {})
        analysis_raw = seg.get("analysis_raw", {})
        evidence_map = seg.get("evidence_map", [])
        seg_cov = seg.get("coverage", {})
        title = meta.get("title_guess") or "Document segment"
        doc_type_guess = meta.get("doc_type_guess") or title or "Unclear"
        display_doc_type, display_doc_type_source = resolve_segment_header_document_type(
            segment=meta if isinstance(meta, dict) else {},
            analysis_payload=analysis_raw if isinstance(analysis_raw, dict) else {},
        )
        start_page = meta.get("page_start", meta.get("start_page", "?"))
        end_page = meta.get("page_end", meta.get("end_page", "?"))
        confidence = meta.get("confidence", 0.0)
        reason = meta.get("reason", "Heuristic segmentation")

        with st.expander(
            f"Document {idx} (page {start_page}-{end_page}): {display_doc_type}",
            expanded=(idx == 1),
        ):
            if not isinstance(analysis, dict) or seg.get("analysis_output_type") != "json_ok":
                st.warning("Segment result could not be generated in structured form.")
                continue

            meta_cols = st.columns(3)
            meta_cols[0].caption(f"**Pages**  \n{start_page}-{end_page}")
            meta_cols[1].caption(f"**Segmentation confidence**  \n{confidence:.0%}")
            meta_cols[2].caption(
                f"**Citation status**  \n{analysis.get('citation_status', 'n/a')}"
            )
            if debug_mode:
                segment_guess_display = (
                    doc_type_guess
                    if normalize_doc_class(doc_type_guess) != "Unklar"
                    else "not clear from segment"
                )
                st.caption(
                    f"Doc type (segment guess): {segment_guess_display} | "
                    f"Doc type (display): {display_doc_type} | "
                    f"Doc type (source): {display_doc_type_source} | "
                    f"Doc type (extraction): "
                    f"{_safe_text(str(analysis.get('dokumenttyp_parteien', '')))}"
                )
                st.caption(f"Heuristic: {reason}")
                boundary_score = meta.get("boundary_score")
                boundary_reasons = [str(r) for r in list(meta.get("boundary_reasons", []) or [])]
                if boundary_score is not None:
                    st.caption(f"Boundary score: {float(boundary_score):.2f}")
                if boundary_reasons:
                    st.caption(f"Boundary reasons: {' | '.join(boundary_reasons[:6])}")
                st.caption(
                    f"Segment coverage: {seg_cov.get('pages_total', 0)} pages | "
                    f"{seg_cov.get('chunks_sent', 0)} chunks"
                )
            st.divider()

            summary_tab, aktennotiz_tab, evidence_tab = st.tabs(
                ["Summary", "Internal note", "Evidence"]
            )
            with summary_tab:
                st.markdown("##### Document type and parties")
                st.caption("Verified facts only - every value below carries a source citation.")
                st.markdown(
                    f"- Document type: {_safe_text(extract_document_type_for_render(analysis))}"
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
                        role = _safe_text(str(party.get("role", "Party") or "Party"))
                        value = _safe_text(str(party.get("value", "") or ""))
                        st.markdown(f"- {role}: {value}")
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
                                _render_source_quote(page, quote)
                else:
                    st.markdown("- not stated")

                st.markdown("##### Key facts")
                st.caption("Amounts, deadlines and dates.")
                # LLM-derived key facts take precedence; specific regex items are merged in as a supplement.
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
                            st.markdown(f"- {label}: {wert}")
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
                        st.markdown("- missing (follow-up required)")

                st.markdown("##### Key statements")
                st.caption("Up to 7 points, each backed by a citation.")
                verified_overview = _verified_claims(
                    list(analysis.get("kernaussagen", [])), max_items=7
                )
                if verified_overview:
                    _render_claim_items(verified_overview, max_items=7)
                else:
                    st.markdown("- missing (follow-up required)")

                st.markdown("##### Open questions")
                st.caption("Gaps and irregularities noted for human review.")
                unclear_rendered = False
                for item in list(analysis.get("missing_info", []))[:10]:
                    st.markdown(f"- {item}")
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
                        st.markdown(f"- {text}")
                        unclear_rendered = True
                if not unclear_rendered:
                    st.markdown("- none")

            with aktennotiz_tab:
                if is_short_doc:
                    with st.expander("Internal note (optional)", expanded=False):
                        _render_aktenvermerk_block(analysis, evidence_map, show_title=False)
                else:
                    _render_aktenvermerk_block(analysis, evidence_map, show_title=True)

            with evidence_tab:
                st.markdown("**Evidence map (table)**")
                _render_evidence_map(evidence_map)

    # -----------------------------------------------------------------------
    # Human-in-the-loop notice
    # -----------------------------------------------------------------------
    st.info(
        "**Human-in-the-loop:** this summary is an AI-generated suggestion. "
        "All statements should be verified by a qualified professional before any action is taken."
    )

    # -----------------------------------------------------------------------
    # Redaction summary
    # -----------------------------------------------------------------------
    with st.expander("Redaction summary", expanded=False):
        dict_masks = list((dictionary_report or {}).get("matched_masks", []))
        dict_counts = dict((dictionary_report or {}).get("mask_counts", {}) or {})
        dict_entity_hits = int((dictionary_report or {}).get("matched_entities_count", 0))
        if dict_entity_hits > 0:
            st.caption(f"{dict_entity_hits} additional entities masked:")
            st.table(
                [{"Mask": mask, "Count": dict_counts.get(mask, 0)} for mask in dict_masks]
            )
        elif dictionary_entities:
            st.caption("No additional dictionary-entity matches.")

        red_summary = _build_redaction_summary(redaction_stats.get("mask_counts"))
        if red_summary:
            st.caption("Patterns masked before LLM transmission:")
            st.table(
                [
                    {"Mask": label, "Count": count}
                    for label, count in red_summary.items()
                ]
            )
            placeholders = list(redaction_stats.get("placeholder_keys") or [])
            if placeholders:
                st.caption("Placeholder keys:")
                st.code(", ".join(placeholders), language=None)
            mask_types = dict(redaction_stats.get("mask_types") or {})
            if mask_types:
                st.caption("Mask types (counts):")
                st.table([{"Type": k, "Count": v} for k, v in mask_types.items()])
        else:
            st.caption("No masks detected.")

    # -----------------------------------------------------------------------
    # Export buttons
    # -----------------------------------------------------------------------
    st.subheader("Export")
    filename_stem = f"Segments_{doc_hash[:8]}"
    # NOTE: these three option values ("Zusammenfassung", "Aktennotiz", "Beides")
    # are a string-based contract consumed by core.export_content.build_export_content()
    # and asserted directly in tests - they are data, not just display labels, so
    # they are intentionally left untranslated. The button labels below are English.
    export_selection = st.radio(
        "Export content",
        options=["Zusammenfassung", "Aktennotiz", "Beides"],
        index=2,
        horizontal=True,
        format_func=lambda v: {
            "Zusammenfassung": "Summary",
            "Aktennotiz": "Internal note",
            "Beides": "Both",
        }.get(v, v),
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
            f"⚠️ Export blocked: {e.leak_count} unmasked item(s) found in the export "
            f"while anonymization was active. The document was not produced - this is "
            f"the fail-closed check working as intended. Review the redaction summary "
            f"above, or add the missed terms under \"Additional entities to mask\"."
        )
        st.stop()

    internal_content_suffix = {
        "Zusammenfassung": "Summary",
        "Aktennotiz": "InternalNote",
        "Beides": "Both",
    }[export_selection]
    external_content_suffix = "Summary"

    pdf_col_internal, pdf_col_external = st.columns(2)
    with pdf_col_internal:
        st.download_button(
            label="PDF - Internal version (unredacted)",
            data=exports.internal_pdf_bytes,
            file_name=f"{filename_stem}_Internal_{internal_content_suffix}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    with pdf_col_external:
        external_pdf_clicked = st.download_button(
            label="PDF - External version (redacted)",
            data=exports.external_pdf_bytes,
            file_name=f"{filename_stem}_External_redacted_{external_content_suffix}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
        if external_pdf_clicked and export_selection != "Zusammenfassung":
            st.info("For the external version, only the summary is exported.")

    docx_col_internal, docx_col_external = st.columns(2)
    with docx_col_internal:
        st.download_button(
            label="DOCX - Internal version (unredacted)",
            data=exports.internal_docx_bytes,
            file_name=f"{filename_stem}_Internal_{internal_content_suffix}.docx",
            mime=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            use_container_width=True,
        )
    with docx_col_external:
        external_docx_clicked = st.download_button(
            label="DOCX - External version (redacted)",
            data=exports.external_docx_bytes,
            file_name=f"{filename_stem}_External_redacted_{external_content_suffix}.docx",
            mime=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            use_container_width=True,
        )
        if external_docx_clicked and export_selection != "Zusammenfassung":
            st.info("For the external version, only the summary is exported.")

    if exports.internal_placeholder_hits:
        hit_preview = ", ".join(exports.internal_placeholder_hits[:8])
        st.warning(
            f"Note: {len(exports.internal_placeholder_hits)} placeholders could not be restored: "
            f"{hit_preview}"
        )
