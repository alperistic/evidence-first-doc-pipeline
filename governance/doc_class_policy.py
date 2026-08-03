import re
from typing import Any, Mapping

_RATENZAHLUNG_OVERRIDE_KEYWORDS = (
    "ratenzahlungsvereinbarung",
    "ratenplan",
    "zahlungsvereinbarung",
    "vergleich",
)


def normalize_doc_class(value: Any, fallback: str = "Unklar") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text


def _guess_doc_class_from_text(text: str) -> str:
    norm = str(text or "").strip().lower()
    if not norm:
        return "Unklar"
    if any(keyword in norm for keyword in _RATENZAHLUNG_OVERRIDE_KEYWORDS):
        return "Ratenzahlungsvereinbarung"
    if "arbeitsvertrag" in norm or ("arbeitgeber" in norm and "arbeitnehmer" in norm):
        return "Arbeitsvertrag"
    if "mietvertrag" in norm or ("vermieter" in norm and "mieter" in norm):
        return "Mietvertrag"
    if "klageschrift" in norm or re.search(r"\bklage\b", norm):
        return "Klageschrift"
    if "mahnschreiben" in norm or "mahnung" in norm:
        return "Mahnung"
    if "kaufvertrag" in norm:
        return "Kaufvertrag"
    return "Unklar"


def _doc_class_override_from_segment_text(segment: Mapping[str, Any]) -> str:
    title_guess = str(segment.get("title_guess", "") or "")
    pages = segment.get("pages", [])
    page_head = ""
    if isinstance(pages, list) and pages:
        page_head = "\n".join(str((page or {}).get("text", "") or "") for page in pages[:2])[:3000]
    combined = f"{title_guess}\n{page_head}".lower()
    if any(keyword in combined for keyword in _RATENZAHLUNG_OVERRIDE_KEYWORDS):
        return "Ratenzahlungsvereinbarung"
    return ""


def segment_doc_class_from_segment(segment: Mapping[str, Any]) -> str:
    guessed = normalize_doc_class(segment.get("doc_type_guess"))
    override = _doc_class_override_from_segment_text(segment)
    if override and guessed in {"Unklar", "Mahnung"}:
        return override
    if guessed != "Unklar":
        return guessed
    title_guess = normalize_doc_class(segment.get("title_guess"), fallback="")
    if title_guess:
        title_class = _guess_doc_class_from_text(title_guess)
        if title_class != "Unklar":
            return title_class
    pages = segment.get("pages", [])
    if isinstance(pages, list) and pages:
        first_text = "\n".join(str((page or {}).get("text", "") or "") for page in pages[:2])
        from_text = _guess_doc_class_from_text(first_text[:3000])
        if from_text != "Unklar":
            return from_text
    return "Unklar"


def llm_doc_class_guess_from_payload(payload: Mapping[str, Any] | None) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    guess = normalize_doc_class(payload.get("document_type"), fallback="")
    return guess or None


def extracted_doc_class_from_payload(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    extracted = normalize_doc_class(payload.get("document_type_extracted"), fallback="")
    if extracted and extracted != "Unklar":
        return extracted
    fallback = normalize_doc_class(payload.get("document_type"), fallback="")
    return fallback if fallback != "Unklar" else ""


def resolve_doc_class_fields(
    *,
    segment_doc_class: str,
    allow_llm_doc_class_guess: bool,
    analysis_payload: Mapping[str, Any] | None,
) -> dict[str, str]:
    normalized_segment = normalize_doc_class(segment_doc_class)
    fields: dict[str, str] = {
        "audit_doc_class": normalized_segment,
        "doc_class_source": "llm" if allow_llm_doc_class_guess else "segmenter",
        "segment_doc_class": normalized_segment,
    }
    if allow_llm_doc_class_guess:
        llm_guess = llm_doc_class_guess_from_payload(analysis_payload)
        if llm_guess:
            fields["llm_doc_class_guess"] = llm_guess
    return fields


def resolve_display_document_type(
    *,
    segment_doc_class: str,
    allow_llm_doc_class_guess: bool,
    analysis_payload: Mapping[str, Any] | None,
    segment_confidence: float | None = None,
    min_confidence: float = 0.6,
) -> tuple[str, str]:
    segment_class = normalize_doc_class(segment_doc_class)
    llm_guess = normalize_doc_class(
        llm_doc_class_guess_from_payload(analysis_payload),
        fallback="Unklar",
    )
    try:
        confidence = float(segment_confidence) if segment_confidence is not None else 0.0
    except (TypeError, ValueError):
        confidence = 0.0
    segment_confident = segment_class != "Unklar" and confidence >= float(min_confidence)
    if (not allow_llm_doc_class_guess) or segment_confident or llm_guess == "Unklar":
        return segment_class, "segmenter"
    return llm_guess, "llm"


def resolve_segment_header_document_type(
    *,
    segment: Mapping[str, Any] | None,
    analysis_payload: Mapping[str, Any] | None,
) -> tuple[str, str]:
    extracted = extracted_doc_class_from_payload(analysis_payload)
    if extracted:
        return extracted, "analysis"

    segment_mapping = segment_doc_class_from_segment(segment or {})
    if segment_mapping != "Unklar":
        return segment_mapping, "segmenter"
    return "Unklar", "fallback"
