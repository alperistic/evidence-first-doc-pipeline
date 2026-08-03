"""core/config.py – Zentrale Konfiguration als typisierte AppConfig Dataclass.

Singleton-Pattern: get_config() gibt immer dieselbe Instanz zurück.
Alle Umgebungsvariablen werden einmalig beim ersten Aufruf geladen.

Secrets (API-Keys) haben keine Defaults und müssen über .env gesetzt werden.
reset_config() steht ausschließlich für Tests zur Verfügung.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Parse-Hilfsfunktionen (nur für die Factory, kein öffentliches API)
# ---------------------------------------------------------------------------


def _getenv_str(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _getenv_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _getenv_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _getenv_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _load_dotenv() -> None:
    """Lädt .env falls python-dotenv installiert ist (kein Pflichtfehler)."""
    try:
        from dotenv import load_dotenv  # type: ignore[import-untyped]
        load_dotenv(override=False)
    except ModuleNotFoundError:
        pass


# ---------------------------------------------------------------------------
# AppConfig Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppConfig:
    # -----------------------------------------------------------------------
    # Azure OpenAI (Pflichtfelder – kein Default, kommen aus .env)
    # -----------------------------------------------------------------------
    azure_openai_endpoint: str            # AZURE_OPENAI_ENDPOINT
    azure_openai_api_key: str             # AZURE_OPENAI_API_KEY
    azure_openai_deployment: str          # AZURE_OPENAI_DEPLOYMENT
    azure_openai_api_version: str         # AZURE_OPENAI_API_VERSION
    azure_openai_classifier_deployment: str  # AZURE_OPENAI_CLASSIFIER_DEPLOYMENT (leer → deployment)

    # -----------------------------------------------------------------------
    # Azure Document Intelligence / OCR (optional)
    # -----------------------------------------------------------------------
    azure_doc_intel_endpoint: str         # AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT
    azure_doc_intel_key: str              # AZURE_DOCUMENT_INTELLIGENCE_KEY
    azure_doc_intel_api_version: str      # AZURE_DOCUMENT_INTELLIGENCE_API_VERSION

    # -----------------------------------------------------------------------
    # LLM Input-Budget
    # -----------------------------------------------------------------------
    input_max_chars: int                  # LATE_INPUT_MAX_CHARS (0 = kein Limit)

    # -----------------------------------------------------------------------
    # Chunking
    # -----------------------------------------------------------------------
    chunk_max_chars: int                  # LATE_CHUNK_MAX_CHARS
    chunk_max_output_tokens: int          # LATE_CHUNK_MAX_OUTPUT_TOKENS

    # -----------------------------------------------------------------------
    # Output-Token-Budgets je Task-Typ
    # -----------------------------------------------------------------------
    max_output_tokens: int                # LATE_MAX_OUTPUT_TOKENS
    struct_max_output_tokens: int         # LATE_STRUCT_MAX_OUTPUT_TOKENS
    doc_analysis_max_output_tokens: int   # LATE_DOC_ANALYSIS_MAX_OUTPUT_TOKENS
    verify_max_output_tokens: int         # LATE_VERIFY_MAX_OUTPUT_TOKENS
    aktenvermerk_max_tokens: int          # LATE_AKTENVERMERK_MAX_TOKENS

    # -----------------------------------------------------------------------
    # Klassifikation
    # -----------------------------------------------------------------------
    classify_preview_chars: int           # LATE_CLASSIFY_PREVIEW_CHARS

    # -----------------------------------------------------------------------
    # PDF-Extraktion / OCR-Polling
    # -----------------------------------------------------------------------
    ocr_min_pdf_text_chars: int           # LATE_MIN_PDF_TEXT_CHARS
    ocr_max_polls: int                    # LATE_OCR_MAX_POLLS
    ocr_poll_sleep_sec: float             # LATE_OCR_POLL_SLEEP_SEC

    # -----------------------------------------------------------------------
    # Segmentierer
    # -----------------------------------------------------------------------
    segment_max_segments: int             # LATE_SEGMENT_MAX_SEGMENTS
    segment_max_workers: int              # LATE_SEGMENT_MAX_WORKERS
    segment_boundary_threshold: float     # LATE_SEGMENT_BOUNDARY_THRESHOLD
    segment_adaptive_threshold: bool      # LATE_SEGMENT_ADAPTIVE_THRESHOLD
    segment_boundary_cooldown_pages: int  # LATE_SEGMENT_BOUNDARY_COOLDOWN_PAGES
    segment_debug: bool                   # LATE_SEGMENT_DEBUG

    # -----------------------------------------------------------------------
    # Evidence / Wiederholungs-Edge-Erkennung
    # -----------------------------------------------------------------------
    repeat_edge_lines: int                # LATE_REPEAT_EDGE_LINES
    repeat_edge_min_pages: int            # LATE_REPEAT_EDGE_MIN_PAGES
    repeat_edge_min_ratio: float          # LATE_REPEAT_EDGE_MIN_RATIO

    # -----------------------------------------------------------------------
    # Async Segment-Analyse (Concurrency-Limit)
    # Empfehlung abhängig vom Azure OpenAI Tier:
    #   S0 (Free/Dev): 1–2    S1 (Produktion): 3–5
    # -----------------------------------------------------------------------
    max_concurrent_segments: int          # LATE_MAX_CONCURRENT_SEGMENTS

    # -----------------------------------------------------------------------
    # Document Cache (SHA256-basiert, §203-konform)
    # -----------------------------------------------------------------------
    doc_cache_enabled: bool               # LATE_DOC_CACHE_ENABLED (default: False)
    doc_cache_ttl_seconds: int            # LATE_DOC_CACHE_TTL_SECONDS (default: 3600)

    # -----------------------------------------------------------------------
    # Feature-Flags
    # -----------------------------------------------------------------------
    allow_plaintext_preview: bool         # ALLOW_PLAINTEXT_PREVIEW
    enable_fuzzy: bool                    # ENABLE_FUZZY
    citation_verify: bool                 # LATE_CITATION_VERIFY
    debug: bool                           # DEBUG
    debug_deadlines: bool                 # LATE_DEBUG_DEADLINES
    log_level: str                        # LOG_LEVEL (DEBUG|INFO|WARNING|ERROR)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_CONFIG: AppConfig | None = None


def get_config() -> AppConfig:
    """Gibt die Singleton-AppConfig zurück (wird beim ersten Aufruf geladen)."""
    global _CONFIG
    if _CONFIG is None:
        _load_dotenv()
        _CONFIG = AppConfig(
            # Azure OpenAI
            azure_openai_endpoint=_getenv_str("AZURE_OPENAI_ENDPOINT"),
            azure_openai_api_key=_getenv_str("AZURE_OPENAI_API_KEY"),
            azure_openai_deployment=_getenv_str("AZURE_OPENAI_DEPLOYMENT"),
            azure_openai_api_version=_getenv_str("AZURE_OPENAI_API_VERSION", "2024-02-01"),
            azure_openai_classifier_deployment=_getenv_str("AZURE_OPENAI_CLASSIFIER_DEPLOYMENT"),
            # Azure Document Intelligence
            azure_doc_intel_endpoint=_getenv_str("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"),
            azure_doc_intel_key=_getenv_str("AZURE_DOCUMENT_INTELLIGENCE_KEY"),
            azure_doc_intel_api_version=_getenv_str("AZURE_DOCUMENT_INTELLIGENCE_API_VERSION", "2023-07-31"),
            # LLM Input-Budget
            input_max_chars=max(0, _getenv_int("LATE_INPUT_MAX_CHARS", 0)),
            # Chunking
            chunk_max_chars=max(1, _getenv_int("LATE_CHUNK_MAX_CHARS", 12000)),
            chunk_max_output_tokens=max(1, _getenv_int("LATE_CHUNK_MAX_OUTPUT_TOKENS", 1200)),
            # Output tokens
            max_output_tokens=max(1, _getenv_int("LATE_MAX_OUTPUT_TOKENS", 2200)),
            struct_max_output_tokens=max(1, _getenv_int("LATE_STRUCT_MAX_OUTPUT_TOKENS", 2200)),
            doc_analysis_max_output_tokens=max(1, _getenv_int("LATE_DOC_ANALYSIS_MAX_OUTPUT_TOKENS", 2400)),
            verify_max_output_tokens=max(1, _getenv_int("LATE_VERIFY_MAX_OUTPUT_TOKENS", 1800)),
            aktenvermerk_max_tokens=max(1, _getenv_int("LATE_AKTENVERMERK_MAX_TOKENS", 2200)),
            # Klassifikation
            classify_preview_chars=max(1, _getenv_int("LATE_CLASSIFY_PREVIEW_CHARS", 1200)),
            # OCR
            ocr_min_pdf_text_chars=max(0, _getenv_int("LATE_MIN_PDF_TEXT_CHARS", 120)),
            ocr_max_polls=max(1, _getenv_int("LATE_OCR_MAX_POLLS", 40)),
            ocr_poll_sleep_sec=max(0.0, _getenv_float("LATE_OCR_POLL_SLEEP_SEC", 1.0)),
            # Segmenter
            segment_max_segments=max(2, _getenv_int("LATE_SEGMENT_MAX_SEGMENTS", 25)),
            segment_max_workers=max(1, _getenv_int("LATE_SEGMENT_MAX_WORKERS", 2)),
            segment_boundary_threshold=max(0.1, _getenv_float("LATE_SEGMENT_BOUNDARY_THRESHOLD", 2.4)),
            segment_adaptive_threshold=_getenv_bool("LATE_SEGMENT_ADAPTIVE_THRESHOLD", True),
            segment_boundary_cooldown_pages=max(0, _getenv_int("LATE_SEGMENT_BOUNDARY_COOLDOWN_PAGES", 1)),
            segment_debug=_getenv_bool("LATE_SEGMENT_DEBUG", False),
            # Evidence
            repeat_edge_lines=max(1, _getenv_int("LATE_REPEAT_EDGE_LINES", 4)),
            repeat_edge_min_pages=max(2, _getenv_int("LATE_REPEAT_EDGE_MIN_PAGES", 3)),
            repeat_edge_min_ratio=min(1.0, max(0.0, _getenv_float("LATE_REPEAT_EDGE_MIN_RATIO", 0.5))),
            # Async Concurrency
            max_concurrent_segments=max(1, _getenv_int("LATE_MAX_CONCURRENT_SEGMENTS", 3)),
            # Document Cache
            doc_cache_enabled=_getenv_bool("LATE_DOC_CACHE_ENABLED", False),
            doc_cache_ttl_seconds=max(60, _getenv_int("LATE_DOC_CACHE_TTL_SECONDS", 3600)),
            # Feature-Flags
            allow_plaintext_preview=_getenv_bool("ALLOW_PLAINTEXT_PREVIEW", False),
            enable_fuzzy=_getenv_bool("ENABLE_FUZZY", False),
            citation_verify=_getenv_bool("LATE_CITATION_VERIFY", False),
            debug=_getenv_bool("DEBUG", False),
            debug_deadlines=_getenv_bool("LATE_DEBUG_DEADLINES", False),
            log_level=_getenv_str("LOG_LEVEL", "INFO").strip().upper(),
        )
    return _CONFIG


def reset_config() -> None:
    """Setzt den Singleton zurück. Nur für Tests verwenden."""
    global _CONFIG
    _CONFIG = None
