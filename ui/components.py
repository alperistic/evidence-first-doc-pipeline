"""
ui/components.py – Reusable Streamlit render components for the pipeline.
Contains UI helper functions that were factored out of app.py.
"""

import streamlit as st

from core.citation import _normalize_item  # noqa: F401 – re-exported for app.py compat
from core.labels import (  # noqa: F401 – re-exported for app.py compat
    _SUMMARY_LABELS,
    _collect_all_kaution_amounts,
    _dedupe_summary_items,
    _human_label_for_evidence,
    _postprocess_eckdaten_items,
    _should_suppress_party_list,
    _summary_doc_type_key,
)
from core.utils import _clean_quote
from core.export_render import _is_generic_todo


def _render_citations(citations: list[dict]) -> None:
    for idx, citation in enumerate(citations, start=1):
        page = citation.get("page_number", "?")
        section_hint = (citation.get("section_hint") or "").strip()
        quote = _clean_quote(citation.get("quote_snippet", ""))

        headline = f"Evidence {idx} · p. {page}"
        if section_hint:
            headline += f" · {section_hint}"
        st.caption(headline)

        if quote:
            st.markdown(f"> {quote}")
        else:
            st.caption("No displayable excerpt available.")


def _render_evidence_map(evidence_map: list[dict]) -> None:
    rows: list[dict] = []
    for item in evidence_map or []:
        rows.append(
            {
                "Field": item.get("field", ""),
                "Value": item.get("value", ""),
                "Page": item.get("page") if item.get("page") is not None else "missing",
                "Quote": _clean_quote(item.get("quote", "")),
            }
        )
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_claim_items(items: list[dict], *, max_items: int | None = None) -> None:
    rendered = items[:max_items] if max_items is not None else items
    for item in rendered:
        text = str(item.get("text", "") or item.get("title", "")).strip()
        if not text:
            continue
        st.markdown(f"– {text}")
        if item.get("citations"):
            with st.expander("Source / evidence", expanded=False):
                _render_citations(item.get("citations", []))
        else:
            st.caption("unverified – evidence missing (follow-up required)")


def _verified_claims(items: list[dict], max_items: int) -> list[dict]:
    out: list[dict] = []
    for raw in items:
        item = _normalize_item(raw)
        citations = item.get("citations", []) if isinstance(item.get("citations"), list) else []
        if citations:
            out.append(item)
        if len(out) >= max_items:
            break
    return out


def _evidence_by_fields(evidence_map: list[dict], fields: tuple[str, ...], max_items: int) -> list[dict]:
    out: list[dict] = []
    for item in evidence_map or []:
        if str(item.get("field", "") or "") not in fields:
            continue
        if str(item.get("value", "") or "") == "missing":
            continue
        out.append(item)
        if len(out) >= max_items:
            break
    return out


def _format_summary_evidence_line(item: dict, doc_type: str) -> str:
    full_quote = str(item.get("quote", "") or "")
    label = _human_label_for_evidence(
        str(item.get("field", "") or ""),
        str(item.get("value", "") or ""),
        full_quote,
        doc_type,
    )
    if label is None:
        return ""
    value = str(item.get("value", "") or "").strip()
    page = item.get("page", "?")
    return f"– {label}: {value} (p. {page})"


def _render_aktenvermerk_block(analysis: dict, evidence_map: list[dict], *, show_title: bool = True) -> None:
    if show_title:
        st.markdown("**Case memo (internal) (working draft, neutral tone)**")

    st.markdown("**Evidenced facts (max. 10)**")
    verified_facts = _verified_claims(list(analysis.get("kernaussagen", [])), max_items=10)
    if verified_facts:
        _render_claim_items(verified_facts, max_items=10)
    else:
        st.markdown("– missing (Rückfrage erforderlich)")

    st.markdown("**Deadlines / dates**")
    fristen = _dedupe_summary_items(_evidence_by_fields(
        evidence_map,
        ("fristen_termine", "datum_beginn", "kuendigung_fristen"),
        max_items=10,
    ))
    if fristen:
        for item in fristen:
            st.markdown(f"– {item.get('value')} (p. {item.get('page', '?')})")
    else:
        st.markdown("– missing (Rückfrage erforderlich)")

    st.markdown("**Open questions (document-related)**")
    if analysis.get("auffaelligkeiten"):
        _render_claim_items(list(analysis.get("auffaelligkeiten", []))[:10], max_items=10)
    else:
        st.markdown("– keine")

    st.markdown("**Follow-up questions for the client (missing info)**")
    if analysis.get("missing_info"):
        for item in list(analysis.get("missing_info", []))[:10]:
            st.markdown(f"– {item}")
    else:
        st.markdown("– keine")

    st.markdown("**To-dos (work items)**")
    todos_filtered = [item for item in list(analysis.get("todos", []))[:10] if not _is_generic_todo(str(item))]
    if todos_filtered:
        for item in todos_filtered:
            st.markdown(f"– {item}")
    else:
        st.markdown("– keine")

    if analysis.get("prueffragen"):
        st.markdown("**Questions to verify**")
        for item in list(analysis.get("prueffragen", []))[:8]:
            st.markdown(f"– {item}")
