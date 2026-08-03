"""core/demo_stub.py – Deterministic offline stand-in for the Azure OpenAI call.

Purpose
-------
Lets the pipeline run end-to-end (extraction -> segmentation -> redaction ->
"LLM" analysis -> evidence map -> citation verification -> export) without
any Azure OpenAI credentials, so a portfolio-repo clone can be tried
immediately and the full result view can be screenshotted for the README.

Activated only via the explicit `DOCPIPE_DEMO_MODE=1` environment flag
(read in core/llm_client.py); this module has no side effects on its own
and is never invoked unless that flag is set.

Design
------
`_invoke_json_task` in core/llm_client.py is the single chokepoint every
analysis call goes through (summarize, summarize_segment_structured,
analyze_document, verify_claim_mappings, generate_aktenvermerk, chunked
summarization). Rather than sprinkling `if demo_mode` branches across
core/analyzer.py, the demo stub is spliced in at that one seam: same
input contract (`text`, `system_prompt`, `model_schema`,
`max_output_tokens`), same `LLMResult` output contract. Every downstream
consumer (evidence mapping, citation verification, party extraction,
export rendering) exercises its real code path against a real,
schema-validated payload - only the network call itself is replaced.

Non-negotiable constraint: citation verification does fuzzy substring
matching of `quote_snippet` against the source pages. So every
quote_snippet emitted here is extracted verbatim from the input `text`
(never invented), tied to the `[Seite N]` marker it was pulled from
(see core/pipeline.py::_segment_text_for_analysis / core/utils.py -
the analysis-time text uses "[Seite N]" tags, not "[[PAGE:N]]" which is
a different, chunk-only marker format).

Determinism: no randomness, no timestamps, no wall-clock reads. The same
`text` always yields the same payload.
"""

from __future__ import annotations

import re
from typing import Any

from core.models import BaseModel

# ---------------------------------------------------------------------------
# Page / sentence extraction
# ---------------------------------------------------------------------------

_PAGE_MARKER_RE = re.compile(r"\[Seite\s+(\d+)\]")

# Sentence-ish splitter: split on '.', '!', '?' followed by whitespace, but
# keep the punctuation. Deliberately simple/deterministic - this only needs
# to produce genuine substrings of the source text, not a linguistically
# perfect sentence segmenter.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9\[])")

_WORD_RE = re.compile(r"\S+")

_MAX_QUOTE_WORDS = 20
_MAX_QUOTE_CHARS = 170

# Generic bracket-only "meta" tokens (e.g. the sample generator's synthetic-data
# banner/watermark, see scripts/generate_samples.py::SYNTHETIC_BANNER /
# WATERMARK_TEXT - every generated sample stamps one on each page header,
# footer, and as a diagonal watermark). Not hardcoded to that exact wording:
# this matches ANY sentence-level content that is nothing but one or more
# "[...]" bracket groups, so it also covers other bracket-wrapped meta markers
# a future sample might add. Real document prose practically never consists
# entirely of bracketed text, so this is safe to treat as noise generically -
# it is a structural check (only brackets + whitespace, nothing else), not a
# hardcoded string match against this repo's specific sample fixtures.
_BRACKET_GROUP_RE = re.compile(r"\[[^\[\]]*\]")
_ONLY_BRACKETS_RE = re.compile(r"^(?:\s*\[[^\[\]]*\]\s*)+$")


def _split_pages(text: str) -> list[tuple[int, str]]:
    """Splits analysis text into (page_number, page_text) using the
    "[Seite N]" markers produced by core/pipeline.py::_segment_text_for_analysis.
    Falls back to a single synthetic page 1 if no markers are present
    (e.g. a raw chunk in summarize_pages_chunked)."""
    matches = list(_PAGE_MARKER_RE.finditer(text))
    if not matches:
        stripped = text.strip()
        return [(1, stripped)] if stripped else []

    pages: list[tuple[int, str]] = []
    for idx, match in enumerate(matches):
        try:
            page_no = int(match.group(1))
        except (TypeError, ValueError):
            continue
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        page_text = text[start:end].strip()
        pages.append((page_no, page_text))
    return pages


def _clip_quote(sentence: str) -> str:
    """Clips a sentence to at most _MAX_QUOTE_WORDS words / _MAX_QUOTE_CHARS
    chars WITHOUT inventing any text - a strict prefix of the input, so the
    result stays a genuine substring of the source page."""
    words = _WORD_RE.findall(sentence)
    clipped = " ".join(words[:_MAX_QUOTE_WORDS])
    if len(clipped) > _MAX_QUOTE_CHARS:
        clipped = clipped[:_MAX_QUOTE_CHARS].rsplit(" ", 1)[0]
    return clipped.strip()


def _strip_leading_trailing_bracket_meta(sentence: str) -> str:
    """Strips leading/trailing bracket-only runs (banner/watermark noise, see
    _ONLY_BRACKETS_RE above) from a sentence WITHOUT touching anything in the
    middle. Only trims from the two ends, and only whole "[...]" groups plus
    surrounding whitespace - never invents or reorders text, so the result
    stays a strict substring of the input (same invariant _clip_quote relies
    on). A sentence that is bracket-only can collapse down to "" here, which
    the caller then treats like any other empty/too-short sentence."""
    text = sentence
    changed = True
    while changed:
        changed = False
        stripped = text.strip()
        lead = _BRACKET_GROUP_RE.match(stripped)
        if lead and lead.start() == 0:
            stripped = stripped[lead.end():]
            changed = True
        trail = None
        for candidate in _BRACKET_GROUP_RE.finditer(stripped):
            if candidate.end() == len(stripped):
                trail = candidate
        if trail:
            stripped = stripped[: trail.start()]
            changed = True
        text = stripped
    return text.strip()


def _sentences_per_page(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Yields (page_number, quote_snippet) pairs where quote_snippet is a
    verbatim clipped sentence taken from that page's text.

    Sentences that are pure synthetic-banner/watermark noise (see
    _ONLY_BRACKETS_RE) are skipped entirely; sentences where the banner is
    merged onto real content (the sample generator's header/footer text has
    no reliable sentence break from the surrounding body text) have the
    bracketed run trimmed off the ends first, so the emitted quote is real
    document prose, not banner text - while the page text itself (returned
    by _split_pages, used for hashing/redaction/audit) is left completely
    untouched. This only affects which sentence gets SELECTED as a quote."""
    out: list[tuple[int, str]] = []
    for page_no, page_text in pages:
        if not page_text:
            continue
        for raw_sentence in _SENTENCE_SPLIT_RE.split(page_text):
            sentence = raw_sentence.strip()
            if not sentence:
                continue
            if _ONLY_BRACKETS_RE.match(sentence):
                continue  # pure banner/watermark noise, no real content at all
            sentence = _strip_leading_trailing_bracket_meta(sentence)
            # Skip pure markers/noise (e.g. leftover bracket tokens) and
            # anything too short to be a meaningful citation anchor.
            if len(_WORD_RE.findall(sentence)) < 3:
                continue
            quote = _clip_quote(sentence)
            if quote:
                out.append((page_no, quote))
    return out


# ---------------------------------------------------------------------------
# Doc-type detection (heuristic, generic - not hardcoded to sample wording)
# ---------------------------------------------------------------------------

_DOC_TYPE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("Mietvertrag", ("mietvertrag", "mieter", "vermieter", "kaltmiete", "monatsmiete", "mietsache")),
    ("Klageschrift", ("klageschrift", "klägerin", "klaeger", "beklagter", "amtsgericht", "landgericht", "klageantrag")),
    ("Mahnung", ("mahnung", "mahnschreiben", "zahlungserinnerung", "rückstand", "mahngebühr", "verzugszinsen")),
    ("Abmahnung", ("abmahnung", "unterlassungserklärung", "unterlassungsbegehren")),
    ("Vertrag", ("vertrag", "vertragspartei", "vertragsgegenstand")),
]


def _detect_doc_type(text: str) -> str:
    lowered = text.lower()
    for label, keywords in _DOC_TYPE_KEYWORDS:
        if any(kw in lowered for kw in keywords):
            return label
    return "Sonstiges"


# ---------------------------------------------------------------------------
# Party (role -> name) extraction
# ---------------------------------------------------------------------------
#
# Reuses the real pipeline's role vocabulary/candidate-cleaning logic
# (core/party_extractor.py, also used by ensure_party_evidence_consistency())
# instead of re-deriving a second copy of "which German role labels count as
# a party" - so the demo stub recognizes the same roles (Vermieter/Mieter,
# Kläger/Beklagter, Käufer/Verkäufer, ...) the real LLM path is expected to.
#
# NOT anchored to line start (no `^`/`re.MULTILINE`): core/extractor.py's
# normalize_extracted_text() collapses each *paragraph* (text between blank
# lines in the raw PDF) onto a single line via `part.replace("\n", " ")`
# before this stub ever sees the text. A real contract's "Vermieter: ...\n
# Mieter: ..." block has no blank line between the two role lines, so after
# normalization both labels end up mid-line inside one flattened paragraph -
# never at the start of a line. An anchored/`.match()`-per-`splitlines()`
# approach therefore finds nothing on real extracted text (see the Bug A
# investigation in the session that added this comment), silently falling
# through to the old whole-sentence heuristic and rendering paragraph
# fragments as party "values". `.finditer()` over the whole page text (see
# _extract_party_lines below) finds the label wherever it occurs.
_PARTY_ROLE_RE = re.compile(
    r"(kl[äa]ger(?:in)?|beklagt(?:e|er|en)?|vermieter(?:in)?|mieter(?:in)?|"
    r"arbeitgeber(?:in)?|arbeitnehmer(?:in)?|k[äa]ufer(?:in)?|verk[äa]ufer(?:in)?)"
    r"\s*:\s*",
    flags=re.IGNORECASE,
)


def _extract_party_lines(pages: list[tuple[int, str]]) -> list[tuple[int, str, str, str]]:
    """Scans page text for "Rolle: Wert"-style role labels and returns
    (page_number, role_label, party_name, quote) tuples, one per role found.

    Uses `_PARTY_ROLE_RE.finditer()` over the whole page text rather than
    matching line-by-line: real extracted-and-normalized text has already had
    each paragraph flattened onto one line (see the module-level comment on
    _PARTY_ROLE_RE), so "Vermieter:" and "Mieter:" typically sit mid-line, not
    at a line start. Each match's value span runs from right after the label
    up to the start of the NEXT role-label match (or a paragraph break /
    end of page text), so one role's value never swallows the next role's
    label+value into the same candidate string. party_name is cleaned via
    core.party_extractor's existing candidate logic (strips address/contact-
    detail tails, keeps the actual person/org name); quote is the clipped
    ORIGINAL substring, unmodified - guaranteed a verbatim substring of the
    page text, same invariant _clip_quote relies on elsewhere in this
    module."""
    from core.party_extractor import _extract_party_candidates, _looks_like_person, _looks_like_org

    out: list[tuple[int, str, str, str]] = []
    seen_roles: set[str] = set()
    for page_no, page_text in pages:
        if not page_text:
            continue
        matches = list(_PARTY_ROLE_RE.finditer(page_text))
        for i, match in enumerate(matches):
            role_raw = match.group(1)
            role_key = role_raw.lower()
            if role_key in seen_roles:
                continue  # keep the first occurrence per role (e.g. signature-block repeats)
            value_start = match.end()
            # Stop at the next role-label match (same paragraph) or the next
            # paragraph break, whichever comes first - either boundary means
            # we've left this role's value and would otherwise pull in the
            # next role's "Label: Name" text as if it were part of this one.
            value_end = matches[i + 1].start() if i + 1 < len(matches) else len(page_text)
            para_break = page_text.find("\n\n", value_start, value_end)
            if para_break != -1:
                value_end = para_break
            value_span = page_text[value_start:value_end].strip()
            if not value_span:
                continue
            # _extract_party_candidates() expects a "Rolle: Wert" string and
            # splits on the FIRST colon to drop the role label (see its
            # docstring / core/party_extractor.py callers, which all pass
            # "role: value"). value_span itself has no leading role label
            # anymore (already consumed by this match), so re-add one here -
            # passing value_span directly would make it split on the first
            # colon INSIDE the value instead (e.g. an "E-Mail: ..." contact
            # line embedded in the address), truncating away the actual name.
            candidates = [
                c for c in _extract_party_candidates(f"{role_raw}: {value_span}")
                if _looks_like_person(c) or _looks_like_org(c)
            ]
            if not candidates:
                continue
            seen_roles.add(role_key)
            role_label = role_raw[:1].upper() + role_raw[1:]
            quote_source = page_text[match.start():value_end].strip()
            out.append((page_no, role_label, candidates[0], _clip_quote(quote_source)))
    return out


# ---------------------------------------------------------------------------
# Amount / date extraction (generic regexes, not tied to specific samples)
# ---------------------------------------------------------------------------

_AMOUNT_RE = re.compile(r"(?:EUR|€)\s?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?")
_DATE_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b")


def _find_first_amount_sentence(sentences: list[tuple[int, str]]) -> tuple[int, str] | None:
    for page_no, quote in sentences:
        if _AMOUNT_RE.search(quote):
            return page_no, quote
    return None


def _find_amount_sentences(sentences: list[tuple[int, str]], limit: int) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for page_no, quote in sentences:
        if _AMOUNT_RE.search(quote):
            out.append((page_no, quote))
        if len(out) >= limit:
            break
    return out


def _find_date_sentences(sentences: list[tuple[int, str]], limit: int) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for page_no, quote in sentences:
        if _DATE_RE.search(quote):
            out.append((page_no, quote))
        if len(out) >= limit:
            break
    return out


def _citation(page_no: int, quote: str, section_hint: str | None = None) -> dict[str, Any]:
    return {"page_number": page_no, "quote_snippet": quote, "section_hint": section_hint}


def _text_item(text: str, page_no: int | None, quote: str | None) -> dict[str, Any]:
    citations = [_citation(page_no, quote)] if page_no and quote else []
    return {"text": text, "citations": citations}


# ---------------------------------------------------------------------------
# Payload builders, one per Pydantic schema reachable via _invoke_json_task
# ---------------------------------------------------------------------------


def _build_document_analysis_payload(
    sentences: list[tuple[int, str]], doc_type: str, pages: list[tuple[int, str]]
) -> dict[str, Any]:
    amount_hits = _find_amount_sentences(sentences, limit=6)
    date_hits = _find_date_sentences(sentences, limit=6)
    fact_hits = sentences[:8]

    facts = [_text_item(quote, page_no, quote) for page_no, quote in fact_hits] or [
        {"text": "Rückfrage (Beleg im Segment nicht vorhanden): keine extrahierbaren Kernaussagen gefunden.", "citations": []}
    ]
    dates = [_text_item(quote, page_no, quote) for page_no, quote in date_hits]
    amounts = [_text_item(quote, page_no, quote) for page_no, quote in amount_hits]

    # Prefer explicit "Rolle: Name" lines (e.g. "Vermieter: Max Beispiel, ...") -
    # this is what real contracts/pleadings look like and yields a clean
    # role -> name mapping instead of a whole paragraph. Falls back to the old
    # whole-sentence heuristic for documents without that line format, so
    # something still renders under "Parties" rather than nothing.
    role_hits = _extract_party_lines(pages)
    if role_hits:
        parties = [
            _text_item(f"{role}: {name}", page_no, quote)
            for page_no, role, name, quote in role_hits
        ]
    else:
        party_hits = [
            s for s in sentences
            if re.search(r"\b(?:Partei|Klägerin|Beklagter|Mieter|Vermieter)\b", s[1], re.IGNORECASE)
        ][:4]
        parties = [_text_item(quote, page_no, quote) for page_no, quote in party_hits]

    key_facts = [quote for _, quote in sentences[:5]]
    deadlines = [quote for _, quote in date_hits]

    first_page = sentences[0][0] if sentences else None
    summary_neutral = (
        f"Im Dokument steht: {doc_type}-Analyse (Demo-Modus, lokal generiert)."
        if not sentences
        else f"Im Dokument steht: {sentences[0][1]}"
    )

    evidence = [
        {"page_number": page_no, "quote_snippet": quote, "section_hint": None}
        for page_no, quote in sentences[:10]
    ]

    return {
        "metadata": {"demo_mode": True, "detected_doc_type": doc_type},
        "document_type": doc_type,
        "facts": facts,
        "dates": dates,
        "issues": [],
        "parties": parties,
        "amounts": amounts,
        "key_facts": key_facts,
        "deadlines": deadlines,
        "summary_neutral": summary_neutral,
        "uncertainties": [],
        "todos": ["Hinweis: Demo-Modus aktiv - Ergebnis stammt aus einem lokalen Stub, keine echte LLM-Analyse."],
        "evidence": evidence,
        "first_page": first_page,
    }


def _build_aktenvermerk_payload(sentences: list[tuple[int, str]], doc_type: str) -> dict[str, Any]:
    amount_hits = _find_amount_sentences(sentences, limit=6)
    date_hits = _find_date_sentences(sentences, limit=6)
    kernaussagen_hits = sentences[:8]

    kernaussagen = [
        {"text": quote, "citations": [_citation(page_no, quote)]} for page_no, quote in kernaussagen_hits
    ] or [{"text": "Rückfrage (Beleg im Segment nicht vorhanden): keine Kernaussagen extrahierbar.", "citations": []}]

    fristen_termine = [
        {
            "text": f"[Sonstige] | {quote} | Quelle: Demo-Stub",
            "berechnet": False,
            "herleitung": None,
            "citations": [_citation(page_no, quote)],
        }
        for page_no, quote in date_hits
    ]

    dokumenttyp_parteien = (
        f"Dokumenttyp: {doc_type}\nKlägerin: fehlende Angabe\nBeklagter: fehlende Angabe\n"
        "Vertreter Klägerin: fehlende Angabe\nVertreter Beklagter: fehlende Angabe"
    )

    eckdaten: list[dict[str, Any]] = []
    for page_no, quote in amount_hits:
        match = _AMOUNT_RE.search(quote)
        if not match:
            continue
        eckdaten.append(
            {
                "label": "Betrag",
                "wert": match.group(0),
                "quelle": f"S. {page_no}: {quote}",
            }
        )
    for page_no, quote in date_hits[:3]:
        match = _DATE_RE.search(quote)
        if not match:
            continue
        eckdaten.append(
            {
                "label": "Frist/Termin",
                "wert": match.group(0),
                "quelle": f"S. {page_no}: {quote}",
            }
        )

    return {
        "dokumenttyp_parteien": dokumenttyp_parteien,
        "kernaussagen": kernaussagen,
        "fristen_termine": fristen_termine,
        "widersprueche": [],
        "fehlende_angaben": [],
        "todos": ["Hinweis: Demo-Modus aktiv - Ergebnis stammt aus einem lokalen Stub, keine echte LLM-Analyse."],
        "eckdaten": eckdaten,
    }


def _build_summary_payload(sentences: list[tuple[int, str]], doc_type: str) -> dict[str, Any]:
    kern_hits = sentences[:8]
    issue_hits = sentences[8:11]
    date_hits = _find_date_sentences(sentences, limit=6)

    kernaussagen = [
        {"text": quote, "citations": [_citation(page_no, quote)]} for page_no, quote in kern_hits
    ]
    auffaelligkeiten = [
        {"text": quote, "citations": [_citation(page_no, quote)]} for page_no, quote in issue_hits
    ]
    top3_findings = [
        {
            "title": quote[:140],
            "why_relevant": "Hinweis aus dem Dokument, zu prüfen.",
            "question": "Rückfrage: zu prüfen.",
            "confidence": "mittel",
            "citations": [_citation(page_no, quote)],
        }
        for page_no, quote in issue_hits[:3]
    ]

    kurzueberblick = (
        f"{doc_type}: {sentences[0][1]}" if sentences else f"{doc_type}: keine Inhalte extrahierbar."
    )

    return {
        "kurzueberblick": kurzueberblick[:280],
        "top3_findings": top3_findings,
        "top3_questions": ["Rückfrage: zu prüfen."] * min(3, max(1, len(issue_hits))),
        "dokumenttyp_parteien": f"Dokumenttyp: {doc_type}\nKlägerin: fehlende Angabe\nBeklagter: fehlende Angabe",
        "kernaussagen": kernaussagen,
        "fristen_termine": [quote for _, quote in date_hits],
        "auffaelligkeiten": auffaelligkeiten,
        "prueffragen": ["Rückfrage: zu prüfen."],
    }


def _build_segment_summary_payload(sentences: list[tuple[int, str]], doc_type: str) -> dict[str, Any]:
    kern_hits = sentences[:8]
    kernaussagen = [{"text": quote, "citations": [_citation(page_no, quote)]} for page_no, quote in kern_hits]
    return {
        "dokumenttyp_parteien": f"Dokumenttyp: {doc_type}\nKlägerin: fehlende Angabe\nBeklagter: fehlende Angabe",
        "kurzueberblick": f"{doc_type}: {sentences[0][1]}" if sentences else "missing",
        "top3_findings": [],
        "kernaussagen": kernaussagen,
        "auffaelligkeiten": [],
        "prueffragen": [],
        "todos": ["Hinweis: Demo-Modus aktiv - lokaler Stub, keine echte LLM-Analyse."],
        "missing_info": [],
    }


def _build_chunk_payload(sentences: list[tuple[int, str]]) -> dict[str, Any]:
    key_points = [quote for _, quote in sentences[:10]]
    amount_hits = _find_amount_sentences(sentences, limit=5)
    date_hits = _find_date_sentences(sentences, limit=5)
    citations_candidates = [
        {"page_number": page_no, "quote_snippet": quote, "section_hint": None}
        for page_no, quote in sentences[:10]
    ]
    return {
        "key_points": key_points,
        "issues": [],
        "questions": [],
        "dates": [quote for _, quote in date_hits],
        "missing_info": [],
        "todos": [],
        "citations_candidates": citations_candidates,
        "_amount_hits": [q for _, q in amount_hits],  # harmless extra key, ignored by schema
    }


def _build_verify_mapping_payload(text: str) -> dict[str, Any]:
    """text here is the JSON-encoded {"claims": ..., "segment_pages": ..., "segment_text": ...}
    payload built by verify_claim_mappings(). The genuine, [Seite N]-tagged
    document text lives in the "segment_text" field - it must be parsed back
    out of the JSON and re-split there, rather than running _split_pages on
    the raw JSON envelope (which would only pick up escaped "\\n" sequences
    and JSON punctuation, not real document sentences)."""
    import json as _json

    try:
        payload = _json.loads(text)
    except (ValueError, TypeError):
        payload = {}

    segment_text = payload.get("segment_text", "") if isinstance(payload, dict) else ""
    pages = _split_pages(str(segment_text or ""))
    sentences = _sentences_per_page(pages)

    claims = payload.get("claims", {}) if isinstance(payload, dict) else {}
    out: dict[str, Any] = {}
    for field in ("kernaussagen", "auffaelligkeiten", "top3_findings"):
        claim_list = claims.get(field, []) if isinstance(claims, dict) else []
        entries = []
        for idx, _claim_text in enumerate(claim_list):
            if idx < len(sentences):
                page_no, quote = sentences[idx]
                entries.append({"citations": [_citation(page_no, quote)]})
            else:
                entries.append({"citations": []})
        out[field] = entries
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_demo_result(
    *,
    text: str,
    system_prompt: str,
    model_schema: type[BaseModel],
) -> dict[str, Any]:
    """Builds a schema-shaped payload for `model_schema`, using only
    sentences/dates/amounts extracted verbatim from `text`. Deterministic:
    depends only on `text` (and, for doc-type detection, the combination of
    `text` + `system_prompt`).

    Returns a plain dict; the caller (core/llm_client.py::_invoke_json_task)
    is responsible for running it through `model_schema.model_validate` and
    the same post-processing (_neutralize_legal_tone) the real path uses -
    so downstream code never has to know the difference.
    """
    del system_prompt  # doc type is detected from the text itself; kept for signature symmetry
    schema_name = model_schema.__name__

    if schema_name == "VerifyMappingPayloadModel":
        # `text` here is NOT the document text - it's a JSON envelope built by
        # verify_claim_mappings() as json.dumps({"claims": ..., "segment_pages":
        # ..., "segment_text": "[Seite N]\n...\n\n[Seite M]\n..."}). The real
        # page-tagged text lives inside the "segment_text" field, so it must be
        # extracted from there - splitting the raw JSON envelope itself would
        # pick up JSON punctuation/escaped "\n" sequences as if they were
        # genuine document content.
        return _build_verify_mapping_payload(text)

    pages = _split_pages(text)
    sentences = _sentences_per_page(pages)
    doc_type = _detect_doc_type(text)

    if schema_name == "DocumentAnalysisPayloadModel":
        return _build_document_analysis_payload(sentences, doc_type, pages)
    if schema_name == "AktenvermerkPayloadModel":
        return _build_aktenvermerk_payload(sentences, doc_type)
    if schema_name == "SummaryPayloadModel":
        return _build_summary_payload(sentences, doc_type)
    if schema_name == "SegmentSummaryPayloadModel":
        return _build_segment_summary_payload(sentences, doc_type)
    if schema_name == "ChunkPayloadModel":
        return _build_chunk_payload(sentences)

    # Unknown schema: fall back to an empty-but-valid payload so callers
    # relying on required-field defaults still get something schema-shaped.
    return {}
