"""
ui/components.py – Reusable Streamlit render components for the pipeline.
Contains UI helper functions that were factored out of app.py.
"""

from html import escape

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


_CITATION_STYLE = (
    "border-left:3px solid #1f4e79;background:#f4f6f8;padding:6px 10px;"
    "margin:2px 0 10px 18px;font-size:0.85rem;color:#31353b;"
)


def _render_citations(citations: list[dict]) -> None:
    """Render citations as source blocks.

    Quotes originate from uploaded documents, so they are HTML-escaped before
    being embedded in markup.
    """
    for citation in citations:
        page = escape(str(citation.get("page_number", "?")))
        section_hint = escape((citation.get("section_hint") or "").strip())
        quote = _clean_quote(citation.get("quote_snippet", ""))

        location = f"p.&nbsp;{page}"
        if section_hint:
            location += f" &middot; {section_hint}"

        if quote:
            body = f"&bdquo;{escape(quote)}&ldquo;"
        else:
            body = "<em>no displayable excerpt available</em>"

        st.markdown(
            f"<div style='{_CITATION_STYLE}'><strong>{location}</strong> &middot; {body}</div>",
            unsafe_allow_html=True,
        )


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
        citations = list(item.get("citations", []) or [])
        if citations:
            # Citations stay expanded: the point of the pipeline is that every
            # statement shows its source without the reviewer having to dig.
            _render_citations(citations)
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
        st.markdown("– missing (follow-up required)")

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
        st.markdown("– missing (follow-up required)")

    st.markdown("**Open questions (document-related)**")
    if analysis.get("auffaelligkeiten"):
        _render_claim_items(list(analysis.get("auffaelligkeiten", []))[:10], max_items=10)
    else:
        st.markdown("– none")

    st.markdown("**Follow-up questions for the client (missing info)**")
    if analysis.get("missing_info"):
        for item in list(analysis.get("missing_info", []))[:10]:
            st.markdown(f"– {item}")
    else:
        st.markdown("– none")

    st.markdown("**To-dos (work items)**")
    todos_filtered = [item for item in list(analysis.get("todos", []))[:10] if not _is_generic_todo(str(item))]
    if todos_filtered:
        for item in todos_filtered:
            st.markdown(f"– {item}")
    else:
        st.markdown("– none")

    if analysis.get("prueffragen"):
        st.markdown("**Questions to verify**")
        for item in list(analysis.get("prueffragen", []))[:8]:
            st.markdown(f"– {item}")
