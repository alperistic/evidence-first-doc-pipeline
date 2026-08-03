"""core/models.py – Pydantic schemas and the LLMResult dataclass.

Moved here from llm.py (Phase 1, Step 1).
"""

import re
from dataclasses import dataclass
from typing import Any, Literal, get_args, get_origin

try:
    from pydantic import BaseModel, Field, ValidationError, field_validator
except ModuleNotFoundError:  # pragma: no cover - lightweight fallback for tests
    class ValidationError(Exception):  # type: ignore[no-redef]
        def __init__(self, errors: list[dict[str, Any]]) -> None:
            self._errors = errors
            super().__init__(str(errors[0]["msg"]) if errors else "ValidationError")

        def errors(self) -> list[dict[str, Any]]:
            return list(self._errors)

    class _FieldInfo:
        def __init__(self, default: Any = ..., default_factory: Any = None) -> None:
            self.default = default
            self.default_factory = default_factory

    def Field(default: Any = ..., default_factory: Any = None, **kwargs):  # type: ignore[no-redef]
        del kwargs
        return _FieldInfo(default=default, default_factory=default_factory)

    def field_validator(*fields: str, mode: str = "after"):  # type: ignore[no-redef]
        def _decorator(func):
            target = func.__func__ if isinstance(func, classmethod) else func
            setattr(target, "__field_validator_fields__", tuple(fields))
            setattr(target, "__field_validator_mode__", str(mode))
            return func

        return _decorator

    class BaseModel:  # type: ignore[no-redef]
        _fallback_validators: dict[str, list[tuple[str, str]]] = {}

        def __init_subclass__(cls, **kwargs) -> None:
            super().__init_subclass__(**kwargs)
            validators: dict[str, list[tuple[str, str]]] = {}
            for name, attr in cls.__dict__.items():
                target = attr.__func__ if isinstance(attr, classmethod) else attr
                fields = getattr(target, "__field_validator_fields__", None)
                if not fields:
                    continue
                mode = str(getattr(target, "__field_validator_mode__", "after"))
                for field_name in fields:
                    validators.setdefault(str(field_name), []).append((mode, str(name)))
            cls._fallback_validators = validators

        @classmethod
        def _default_for(cls, field_name: str) -> Any:
            if not hasattr(cls, field_name):
                return ...
            default = getattr(cls, field_name)
            if isinstance(default, _FieldInfo):
                if default.default_factory is not None:
                    return default.default_factory()
                return default.default
            return default

        @classmethod
        def _coerce_value(cls, annotation: Any, value: Any) -> Any:
            origin = get_origin(annotation)
            args = get_args(annotation)

            if origin is Literal:
                return value
            if origin in (list, tuple):
                inner = args[0] if args else Any
                if not isinstance(value, list):
                    return []
                return [cls._coerce_value(inner, item) for item in value]
            if origin is dict:
                return value if isinstance(value, dict) else {}
            if origin is None and isinstance(annotation, type) and issubclass(annotation, BaseModel):
                if isinstance(value, annotation):
                    return value
                if isinstance(value, dict):
                    return annotation.model_validate(value)
                return annotation.model_validate({})
            if origin is not None and args:
                # Optional / Union
                if value is None:
                    return None
                for option in args:
                    if option is type(None):
                        continue
                    try:
                        return cls._coerce_value(option, value)
                    except Exception:
                        continue
                return value
            if annotation in (str, int, float, bool):
                if value is None:
                    return "" if annotation is str else value
                try:
                    return annotation(value)
                except Exception:
                    return value
            return value

        @classmethod
        def model_validate(cls, data: Any):
            if not isinstance(data, dict):
                raise ValidationError([{"msg": f"{cls.__name__}: expected object"}])

            raw = dict(data)
            for field_name, validators in cls._fallback_validators.items():
                for mode, method_name in validators:
                    if mode != "before":
                        continue
                    raw[field_name] = getattr(cls, method_name)(raw.get(field_name))

            values: dict[str, Any] = {}
            for field_name, annotation in getattr(cls, "__annotations__", {}).items():
                if field_name.startswith("_"):
                    continue
                if field_name in raw:
                    value = raw.get(field_name)
                else:
                    default = cls._default_for(field_name)
                    if default is ...:
                        raise ValidationError([{"msg": f"{cls.__name__}.{field_name}: field required"}])
                    value = default
                values[field_name] = cls._coerce_value(annotation, value)

            obj = cls.__new__(cls)
            for field_name, value in values.items():
                setattr(obj, field_name, value)

            for field_name, validators in cls._fallback_validators.items():
                if field_name not in values:
                    continue
                for mode, method_name in validators:
                    if mode == "before":
                        continue
                    setattr(obj, field_name, getattr(cls, method_name)(getattr(obj, field_name)))
            return obj

        def model_dump(self) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for field_name in getattr(self, "__annotations__", {}):
                value = getattr(self, field_name, None)
                if isinstance(value, BaseModel):
                    out[field_name] = value.model_dump()
                elif isinstance(value, list):
                    out[field_name] = [
                        item.model_dump() if isinstance(item, BaseModel) else item for item in value
                    ]
                else:
                    out[field_name] = value
            return out


# ---------------------------------------------------------------------------
# Snippet helper functions (used by model validators and llm.py)
# ---------------------------------------------------------------------------

def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


_SNIPPET_MAX_CHARS = 170
_LEADING_ARTIFACT_RE = re.compile(r"^(?:seite\s*)?\d{1,4}\s*[:.)-]?\s*", flags=re.IGNORECASE)
_DATE_ORDINAL_START_RE = re.compile(
    r"^\d{1,2}\.\s+(?:januar|februar|m[äa]rz|april|mai|juni|juli|august|september|oktober|november|dezember)\b",
    flags=re.IGNORECASE,
)


def clean_snippet(text: str, max_chars: int = _SNIPPET_MAX_CHARS) -> str:
    """
    Cleans up snippets for UI/exports:
    - removes parser artifacts (e.g. leading page numbers),
    - normalizes whitespace and hyphenation,
    - tries to start at a sentence boundary,
    - clips without leaving a truncated word fragment.
    """
    snippet = (text or "").replace("\u00ad", "")
    snippet = re.sub(r"(\w)-\s+(\w)", r"\1\2", snippet)
    snippet = _normalize_ws(snippet)
    # No stripping when the snippet starts with a long-format date: "14. Februar 2025"
    if not _DATE_ORDINAL_START_RE.match(snippet):
        snippet = _LEADING_ARTIFACT_RE.sub("", snippet)
    snippet = re.sub(r"^(?:s(?:eite)?\.?\s*\d+)\s*", "", snippet, flags=re.IGNORECASE)
    snippet = re.sub(r"^[a-zäöüß]{1,3}\s+(?=[\(\[A-ZÄÖÜ])", "", snippet)
    snippet = re.sub(r"^[\)\],.;:]+\s*", "", snippet)
    snippet = snippet.strip(" -:;,.")
    snippet = _normalize_ws(snippet)
    if not snippet:
        return ""

    tokens = re.findall(r"\S+", snippet)
    common_short_starts = {"im", "in", "am", "an", "zum", "zur", "von", "mit", "der", "die", "das", "den", "dem"}
    if len(tokens) >= 2:
        first = tokens[0].strip(".,;:!?")
        if first.islower() and len(first) <= 3 and first not in common_short_starts:
            snippet = _normalize_ws(" ".join(tokens[1:]))

    first_boundary = re.search(r"[.!?]\s+", snippet)
    if first_boundary and first_boundary.end() < min(len(snippet), 80):
        _prefix = snippet[:first_boundary.start()]
        _suffix_after = snippet[first_boundary.end():]
        # Don't jump ahead when the boundary is a date ordinal: "14. Februar", "31. Januar" etc.
        _is_date_ordinal = bool(re.search(r"\b\d{1,2}$", _prefix)) and bool(re.search(
            r"^(?:januar|februar|m[äa]rz|april|mai|juni|juli|august|september|oktober|november|dezember)\b",
            _suffix_after,
            re.IGNORECASE,
        ))
        if not _is_date_ordinal:
            remainder = _suffix_after.strip()
            if len(re.findall(r"\S+", remainder)) >= 4:
                snippet = remainder

    if len(snippet) <= max_chars:
        return snippet

    clipped = snippet[:max_chars]
    clipped = re.sub(r"\s+\S*$", "", clipped).strip()
    if not clipped:
        return snippet[:max_chars].strip()
    return clipped


def _normalize_citations_before(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    normalized: list[Any] = []
    for item in value:
        if isinstance(item, str):
            text = clean_snippet(item, max_chars=180)
            if text:
                normalized.append({"page_number": None, "quote_snippet": text, "section_hint": None})
        elif isinstance(item, dict):
            casted = dict(item)
            if "quote_snippet" not in casted and "quote" in casted:
                casted["quote_snippet"] = casted.get("quote")
            if "quote_snippet" not in casted and "text" in casted:
                casted["quote_snippet"] = casted.get("text")
            casted["quote_snippet"] = clean_snippet(str(casted.get("quote_snippet", "")), max_chars=180)
            normalized.append(casted)
    return normalized


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CitationModel(BaseModel):
    page_number: int | None = Field(default=None, ge=1)
    quote_snippet: str
    section_hint: str | None = None

    @field_validator("quote_snippet", mode="before")
    @classmethod
    def trim_max_words(cls, value: Any) -> str:
        text = clean_snippet(str(value or ""), max_chars=180)
        words = re.findall(r"\S+", text)
        if len(words) <= 20:
            return text
        return " ".join(words[:20])

    @field_validator("quote_snippet")
    @classmethod
    def validate_max_words(cls, value: str) -> str:
        if len(re.findall(r"\S+", value or "")) > 20:
            raise ValueError("quote_snippet darf maximal 20 Wörter enthalten")
        return value


class TextItemModel(BaseModel):
    text: str
    citations: list[CitationModel] = Field(default_factory=list)
    date_category: str | None = None

    @field_validator("citations", mode="before")
    @classmethod
    def normalize_citations(cls, value: Any) -> list[Any]:
        return _normalize_citations_before(value)


class TopFindingModel(BaseModel):
    title: str
    why_relevant: str
    question: str
    confidence: Literal["hoch", "mittel", "niedrig"]
    citations: list[CitationModel] = Field(default_factory=list)

    @field_validator("citations", mode="before")
    @classmethod
    def normalize_citations(cls, value: Any) -> list[Any]:
        return _normalize_citations_before(value)


class SummaryPayloadModel(BaseModel):
    kurzueberblick: str
    top3_findings: list[TopFindingModel]
    top3_questions: list[str]
    dokumenttyp_parteien: str
    kernaussagen: list[TextItemModel]
    fristen_termine: list[str]
    auffaelligkeiten: list[TextItemModel]
    prueffragen: list[str]

    @field_validator("top3_findings", mode="before")
    @classmethod
    def normalize_top_findings(cls, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                normalized.append(
                    {
                        "title": item,
                        "why_relevant": "Hinweis",
                        "question": "Zu prüfen",
                        "confidence": "niedrig",
                        "citations": [],
                    }
                )
            elif isinstance(item, dict):
                casted = dict(item)
                casted.setdefault("title", str(casted.get("text", "")).strip() or "Hinweis")
                casted.setdefault("why_relevant", "Hinweis")
                casted.setdefault("question", "Zu prüfen")
                casted.setdefault("confidence", "niedrig")
                casted.setdefault("citations", [])
                normalized.append(casted)
        return normalized

    @field_validator("kernaussagen", "auffaelligkeiten", mode="before")
    @classmethod
    def normalize_text_items(cls, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                normalized.append({"text": item, "citations": []})
            elif isinstance(item, dict):
                casted = dict(item)
                casted["text"] = str(casted.get("text") or casted.get("title") or "").strip()
                casted.setdefault("citations", [])
                normalized.append(casted)
            else:
                normalized.append({"text": str(item), "citations": []})
        return normalized

    @field_validator("fristen_termine", "top3_questions", "prueffragen", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                out.append(str(item.get("text") or item.get("title") or "").strip())
            else:
                out.append(str(item).strip())
        return [x for x in out if x]


class FristItemModel(BaseModel):
    text: str
    berechnet: bool = False
    herleitung: str | None = None
    citations: list[CitationModel] = Field(default_factory=list)


class AktenvermerkPayloadModel(BaseModel):
    dokumenttyp_parteien: str
    kernaussagen: list[TextItemModel]
    fristen_termine: list[FristItemModel]
    widersprueche: list[TextItemModel]
    fehlende_angaben: list[str]
    todos: list[str]
    eckdaten: list[Any] = Field(default_factory=list)

    @field_validator("kernaussagen", "widersprueche", mode="before")
    @classmethod
    def normalize_akten_text_items(cls, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                normalized.append({"text": item, "citations": []})
            elif isinstance(item, dict):
                casted = dict(item)
                casted["text"] = str(casted.get("text") or casted.get("title") or "").strip()
                casted.setdefault("citations", [])
                normalized.append(casted)
            else:
                normalized.append({"text": str(item), "citations": []})
        return normalized


class ChunkCitationCandidateModel(BaseModel):
    page_number: int | None = Field(default=None, ge=1)
    quote_snippet: str
    section_hint: str | None = None


class ChunkPayloadModel(BaseModel):
    key_points: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    todos: list[str] = Field(default_factory=list)
    citations_candidates: list[ChunkCitationCandidateModel] = Field(default_factory=list)


class SegmentTopFindingModel(BaseModel):
    title: str
    severity: Literal["hoch", "mittel", "niedrig"] = "mittel"
    why_relevant: str = "Hinweis aus dem Dokument."
    question: str = "Rückfrage: zu prüfen."
    citations: list[CitationModel] = Field(default_factory=list)

    @field_validator("citations", mode="before")
    @classmethod
    def normalize_citations(cls, value: Any) -> list[Any]:
        return _normalize_citations_before(value)


class SegmentSummaryPayloadModel(BaseModel):
    dokumenttyp_parteien: str = "missing"
    kurzueberblick: str = "missing"
    top3_findings: list[SegmentTopFindingModel] = Field(default_factory=list)
    kernaussagen: list[TextItemModel] = Field(default_factory=list)
    auffaelligkeiten: list[TextItemModel] = Field(default_factory=list)
    prueffragen: list[str] = Field(default_factory=list)
    todos: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)

    @field_validator("top3_findings", mode="before")
    @classmethod
    def normalize_top_findings(cls, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                normalized.append(
                    {
                        "title": item,
                        "severity": "niedrig",
                        "why_relevant": "Hinweis aus dem Dokument.",
                        "question": "Rückfrage: zu prüfen.",
                        "citations": [],
                    }
                )
            elif isinstance(item, dict):
                casted = dict(item)
                casted.setdefault("title", str(casted.get("text", "")).strip() or "Hinweis")
                casted.setdefault("severity", str(casted.get("confidence", "niedrig")).strip().lower())
                casted.setdefault("why_relevant", "Hinweis aus dem Dokument.")
                casted.setdefault("question", "Rückfrage: zu prüfen.")
                casted.setdefault("citations", [])
                normalized.append(casted)
        return normalized

    @field_validator("kernaussagen", "auffaelligkeiten", mode="before")
    @classmethod
    def normalize_text_items(cls, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                normalized.append({"text": item, "citations": []})
            elif isinstance(item, dict):
                casted = dict(item)
                casted["text"] = str(casted.get("text") or casted.get("title") or "").strip()
                casted.setdefault("citations", [])
                normalized.append(casted)
            else:
                normalized.append({"text": str(item), "citations": []})
        return normalized

    @field_validator("prueffragen", "todos", "missing_info", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                out.append(str(item.get("text") or item.get("title") or "").strip())
            else:
                out.append(str(item).strip())
        return [x for x in out if x]


class DocumentEvidenceModel(BaseModel):
    page_number: int | None = Field(default=None, ge=1)
    quote_snippet: str = ""
    section_hint: str | None = None

    @field_validator("quote_snippet", mode="before")
    @classmethod
    def normalize_quote(cls, value: Any) -> str:
        return clean_snippet(str(value or ""), max_chars=180)


class DocumentAnalysisPayloadModel(BaseModel):
    metadata: dict[str, Any] = Field(default_factory=dict)
    document_type: str = "Unklar"
    facts: list[TextItemModel] = Field(default_factory=list)
    dates: list[TextItemModel] = Field(default_factory=list)
    issues: list[TextItemModel] = Field(default_factory=list)
    parties: list[TextItemModel] = Field(default_factory=list)
    amounts: list[TextItemModel] = Field(default_factory=list)
    key_facts: list[str] = Field(default_factory=list)
    deadlines: list[str] = Field(default_factory=list)
    summary_neutral: str = "missing"
    uncertainties: list[str] = Field(default_factory=list)
    todos: list[str] = Field(default_factory=list)
    evidence: list[DocumentEvidenceModel] = Field(default_factory=list)

    @field_validator("key_facts", "deadlines", "uncertainties", "todos", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                out.append(str(item.get("text") or item.get("value") or "").strip())
            else:
                out.append(str(item).strip())
        return [x for x in out if x]

    @field_validator("facts", "dates", "issues", "parties", "amounts", mode="before")
    @classmethod
    def normalize_claim_items(cls, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                normalized.append({"text": item, "citations": []})
            elif isinstance(item, dict):
                casted = dict(item)
                casted["text"] = str(casted.get("text") or casted.get("value") or casted.get("title") or "").strip()
                casted.setdefault("citations", [])
                normalized.append(casted)
            else:
                normalized.append({"text": str(item), "citations": []})
        return normalized


class VerifyFieldCitationsModel(BaseModel):
    citations: list[CitationModel] = Field(default_factory=list)


class VerifyMappingPayloadModel(BaseModel):
    kernaussagen: list[VerifyFieldCitationsModel] = Field(default_factory=list)
    auffaelligkeiten: list[VerifyFieldCitationsModel] = Field(default_factory=list)
    top3_findings: list[VerifyFieldCitationsModel] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LLMResult dataclass
# ---------------------------------------------------------------------------

@dataclass
class LLMResult:
    parsed: dict
    token_count: int
    model: str
    output_type: str    # "json_ok" | "json_fallback" | "api_error"
    input_chars: int
    input_truncated: bool
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    has_token_usage: bool = False
