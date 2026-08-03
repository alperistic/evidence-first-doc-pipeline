"""
app.py – Confidential document analysis pipeline: document summarization demo.
Legal documents are used as the primary example use case, but the pipeline
is domain-agnostic. Designed so that no document content is persisted and
no plaintext is logged - only a SHA-256 hash is stored.
"""

from dotenv import load_dotenv
load_dotenv()
import os
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
# Page configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Document Analysis Pipeline",
    page_icon="📄",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Confidentiality notice (core requirement)
# ---------------------------------------------------------------------------
st.title("Document Analysis Pipeline")
st.caption(
    "AI extracts, code computes, a human decides - every extracted value "
    "carries a citation back to its source page."
)

st.warning(
    "**Confidentiality notice:** documents are held in memory only. Just a "
    "SHA-256 hash is written to the audit log - never the text or the "
    "filename. Every output is a suggestion for human review, not a decision.",
    icon="⚠️",
)


def _demo_mode_enabled() -> bool:
    raw = os.getenv("DOCPIPE_DEMO_MODE")
    if raw is None:
        return False
    return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}


DEMO_MODE = _demo_mode_enabled()

if DEMO_MODE:
    st.info(
        "**Demo mode:** analysis results are generated locally by a "
        "deterministic stub, not by a language model. Set Azure credentials "
        "in `.env` and unset `DOCPIPE_DEMO_MODE` for real analysis.",
        icon="🧪",
    )

loaded_policy = load_policy()
policy = loaded_policy.config
workflow = DocSummaryWorkflow()

# ---------------------------------------------------------------------------
# Sidebar: settings
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Settings")
    if DEMO_MODE:
        st.info(
            "🧪 Demo mode active: analysis is a local deterministic stub, "
            "not a language model.",
        )
    profile_labels = {
        "balanced": "Balanced (default)",
        "strict": "Strict (minimal data sent)",
    }
    can_override_profile = policy.dev_mode and policy.allow_ui_override
    if can_override_profile:
        override_options = ["balanced", "strict"]
        default_profile = workflow.resolve_effective_profile(loaded_policy)
        if default_profile not in override_options:
            default_profile = "balanced"
        selected_profile = st.selectbox(
            "Mode (DEV override)",
            override_options,
            index=override_options.index(default_profile),
            format_func=lambda p: profile_labels.get(p, p),
            help="Only overridable in DEV mode. Fast is not available in the product UI.",
        )
        st.caption("DEV override active: mode is taken from the sidebar.")
    else:
        selected_profile = None
    effective_profile = workflow.resolve_effective_profile(
        loaded_policy,
        profile_override=selected_profile,
    )
    if not can_override_profile:
        if effective_profile == "strict":
            st.info("Mode: strict - minimal data sent to the model.")
        else:
            st.info("Mode: balanced (default)")
        st.caption(f"Read-only from {loaded_policy.source_path.name}.")

    enable_redaction = st.toggle(
        "Input anonymization (redaction)",
        value=False,
        help=(
            "Masks people, addresses, and entities before the LLM call. "
            "Default OFF: confidentiality is handled via the Azure contract "
            "terms, not via input redaction."
        ),
    )

    advanced_expander = st.expander("Advanced (DEV)", expanded=False)
    with advanced_expander:
        debug_mode = st.toggle("Show debug info", value=False)
        auto_classify = True  # always active - LLM doc-class guess is a fixed part of the pipeline
        dictionary_entities_input = st.text_area(
            "Additional entities to mask safely (optional)",
            value="",
            help="One entity per line, e.g. names, organizations, or addresses.",
            height=120,
        )
        env_enable_fuzzy = get_config().enable_fuzzy
        dictionary_fuzzy = st.toggle(
            "Fuzzy matching (approx.)",
            value=env_enable_fuzzy,
            help="When fuzzy matching is active, similar spellings are also masked.",
        )
        dictionary_threshold = st.slider(
            "Fuzzy threshold",
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
            st.success("Pseudonymization active - names are masked before the model call.")
        elif not must_use_redacted:
            st.warning("LLM input: fast profile active (DEV/policy only).")
    else:
        st.caption("Input anonymization is off - the model receives the original text.")

    with advanced_expander:
        if debug_mode:
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
            st.caption(f"citation_verify_mode: {citation_verify_mode} (DOCPIPE_CITATION_VERIFY)")
            if not allow_plaintext_preview:
                st.caption("Plaintext preview stays disabled (ALLOW_PLAINTEXT_PREVIEW=0).")

    st.divider()
    st.caption("Document Analysis Pipeline v0.1 - MVP demo")

# ---------------------------------------------------------------------------
# Initialize session state
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
# Main area: upload
# ---------------------------------------------------------------------------
uploaded_file = st.file_uploader(
    "Upload document (PDF or DOCX)",
    type=["pdf", "docx"],
    help="The document is not stored. Only the SHA-256 hash is recorded in the audit log.",
)

if uploaded_file is not None:
    file_bytes = uploaded_file.read()
    doc_hash = compute_sha256(file_bytes)

    # New document or changed policy/profile setting -> reset state
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

    with st.expander("Document metadata", expanded=False):
        st.code(f"SHA-256: {doc_hash}", language=None)
        st.caption(
            f"Filename (local only): {uploaded_file.name} | "
            f"Size: {len(file_bytes):,} bytes"
        )

    # Text extraction
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
            "No readable text found in the document. "
            "For scanned PDFs, please configure the OCR fallback "
            "(AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT / AZURE_DOCUMENT_INTELLIGENCE_KEY)."
        )
        st.stop()

    if extraction.ocr_used:
        st.info(
            "Document was processed via OCR (scanned PDF detected). "
            "Please review the result especially carefully."
        )
    else:
        st.caption("Text extraction: direct document text (no OCR fallback).")

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
                    "Hard gate active: redaction failed, analysis is aborted for safety reasons. "
                    f"Error: {exc}"
                )
                st.stop()
            st.error(
                "Redaction failed. No LLM call will be made, for privacy reasons. "
                f"Error: {exc}"
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
                st.error("Hard gate active: mapping is missing or invalid. LLM call is blocked.")
                st.stop()
            if not text_for_llm.strip():
                st.error(
                    "Hard gate active: redacted_text is empty. "
                    "No LLM call will be made (fail_closed=true)."
                )
                st.stop()
        elif not text_for_llm.strip():
            st.warning(
                "redacted_text is empty; a minimized placeholder payload is used "
                "(no fallback to the original text)."
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
            f"Segmentation: segments: {len(segments)} (document-based), "
            f"average: {avg_pages_per_segment:.2f} pages/segment "
            f"(p. {first_page}-{last_page})."
        )
        with st.expander("Detected segments", expanded=False):
            rows: list[dict[str, str]] = []
            for seg in segments:
                row: dict[str, str] = {
                    "Segment": str(seg.get("segment_id")),
                    "Pages": (
                        f"{seg.get('page_start', seg.get('start_page', '?'))}-"
                        f"{seg.get('page_end', seg.get('end_page', '?'))}"
                    ),
                    "Type": str(seg.get("doc_type_guess", "Unclear")),
                    "Confidence": f"{float(seg.get('confidence', 0.0)):.2f}",
                }
                if debug_mode:
                    boundary_score = seg.get("boundary_score")
                    row["Boundary score"] = "n/a" if boundary_score is None else f"{float(boundary_score):.2f}"
                    row["Boundary reasons"] = " | ".join(
                        [str(reason) for reason in list(seg.get("boundary_reasons", []) or [])[:3]]
                    )
                rows.append(row)
            st.table(
                rows
            )

    if not runtime_config.auto_classify:
        classify_used_chars = 0

    # -----------------------------------------------------------------------
    # Redaction preview + confirmation
    # -----------------------------------------------------------------------
    if use_redaction and not st.session_state.confirmed:
        st.subheader("Anonymization preview")
        if st.button("Start analysis", type="primary", key="start_after_redaction"):
            st.session_state.confirmed = True
            st.rerun()

        dict_masks = list((dictionary_report or {}).get("matched_masks", []))
        dict_counts = dict((dictionary_report or {}).get("mask_counts", {}) or {})
        dict_entity_hits = int((dictionary_report or {}).get("matched_entities_count", 0))
        if dict_entity_hits > 0:
            st.success(f"{dict_entity_hits} additional entities masked.", icon="✅")
            st.table(
                [{"Mask": mask, "Count": dict_counts.get(mask, 0)} for mask in dict_masks]
            )
        elif dictionary_entities:
            st.info("No additional entity matches found in the text.", icon="ℹ️")

        red_summary = _build_redaction_summary(redaction_stats.get("mask_counts"))
        total_masks = sum(red_summary.values())
        if red_summary:
            st.success(
                f"Regex/heuristic masks applied: {total_masks}",
                icon="✅",
            )
            st.table(
                [{"Mask": label, "Count": count} for label, count in red_summary.items()]
            )
            placeholders = list(redaction_stats.get("placeholder_keys") or [])
            if placeholders:
                st.caption("Placeholders used")
                st.code(", ".join(placeholders), language=None)
            mask_types = dict(redaction_stats.get("mask_types") or {})
            if mask_types:
                st.caption("Mask types (counts)")
                st.table([{"Type": k, "Count": v} for k, v in mask_types.items()])
        else:
            st.info(
                "No maskable data points (IBAN, case reference, "
                "phone, email) detected in the document.",
                icon="ℹ️",
            )
        if debug_mode and allow_plaintext_preview:
            st.warning("Plaintext debug active (local only, not exported/logged).", icon="⚠️")
            st.text_area("Plaintext preview (debug)", value=raw_text[:2000], height=180, disabled=True)
        st.stop()

    # -----------------------------------------------------------------------
    # Start analysis
    # -----------------------------------------------------------------------
    if st.session_state.result is None:
        if st.button("Start analysis", type="primary"):
            source_pages_by_no = {
                _page_no(page): page
                for page in pages
                if _page_no(page)
            }

            with st.spinner("Analysis running..."):
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
                st.info("Result loaded from cache (same document + runtime configuration + model).")
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
            # The fail-closed export guard (validate_mandantenexport) only
            # makes sense when a redaction pass actually ran this request.
            # runtime_config.redact_required alone is a static policy default
            # (True unless policy.yaml overrides it) and is independent of
            # the "Input anonymization (redaction)" sidebar toggle - using it
            # by itself makes the guard fire even when the user left
            # redaction OFF and the model received (and returned) the
            # original, un-redacted text, which is not a leak.
            redact_required=enable_redaction and runtime_config.redact_required,
        )


# ---------------------------------------------------------------------------
# Audit log view
# ---------------------------------------------------------------------------
with st.expander("Audit log (last 50 entries)", expanded=False):
    logs = get_recent_logs()
    if logs:
        st.dataframe(
            logs,
            use_container_width=True,
            column_config={
                "doc_hash": st.column_config.TextColumn("Document hash (SHA-256)", width="large"),
                "timestamp": st.column_config.TextColumn("Timestamp (UTC)"),
                "doc_class": st.column_config.TextColumn("Class"),
                "policy_profile": st.column_config.TextColumn("Policy profile"),
                "redaction_applied": st.column_config.NumberColumn("Redaction applied"),
                "pseudonymization_applied": st.column_config.NumberColumn("Pseudonymization applied"),
                "classify_used_chars": st.column_config.NumberColumn("Classify chars"),
                "token_count": st.column_config.NumberColumn("Tokens"),
                "model": st.column_config.TextColumn("Model"),
                "citation_status": st.column_config.TextColumn("Citation status"),
            },
        )
    else:
        st.caption("No entries yet.")
