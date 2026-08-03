"""core/config.py - Central configuration as a typed AppConfig dataclass.

Singleton pattern: get_config() always returns the same instance.
All environment variables are loaded once, on first call.

Secrets (API keys) have no defaults and must be set via .env.
reset_config() is intended for use in tests only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Parsing helper functions (factory-internal only, not a public API)
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


def _is_demo_mode_enabled() -> bool:
    """Mirrors core/llm_client.py::_demo_mode_enabled(). Duplicated (rather than
    imported) to keep core/config.py free of a dependency on core/llm_client.py,
    which itself imports core.config - importing it here would create a cycle."""
    raw = os.getenv("DOCPIPE_DEMO_MODE")
    if raw is None:
        return False
    return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}


def _load_dotenv() -> None:
    """Loads .env if python-dotenv is installed (not a hard requirement)."""
    try:
        from dotenv import load_dotenv  # type: ignore[import-untyped]
        load_dotenv(override=False)
    except ModuleNotFoundError:
        pass


# ---------------------------------------------------------------------------
# AppConfig dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppConfig:
    # -----------------------------------------------------------------------
    # Azure OpenAI (required fields - no default, come from .env)
    # -----------------------------------------------------------------------
    azure_openai_endpoint: str            # AZURE_OPENAI_ENDPOINT
    azure_openai_api_key: str             # AZURE_OPENAI_API_KEY
    azure_openai_deployment: str          # AZURE_OPENAI_DEPLOYMENT
    azure_openai_api_version: str         # AZURE_OPENAI_API_VERSION
    azure_openai_classifier_deployment: str  # AZURE_OPENAI_CLASSIFIER_DEPLOYMENT (empty -> falls back to deployment)

    # -----------------------------------------------------------------------
    # Azure Document Intelligence / OCR (optional)
    # -----------------------------------------------------------------------
    azure_doc_intel_endpoint: str         # AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT
    azure_doc_intel_key: str              # AZURE_DOCUMENT_INTELLIGENCE_KEY
    azure_doc_intel_api_version: str      # AZURE_DOCUMENT_INTELLIGENCE_API_VERSION

    # -----------------------------------------------------------------------
    # LLM input budget
    # -----------------------------------------------------------------------
    input_max_chars: int                  # DOCPIPE_INPUT_MAX_CHARS (0 = no limit)

    # -----------------------------------------------------------------------
    # Chunking
    # -----------------------------------------------------------------------
    chunk_max_chars: int                  # DOCPIPE_CHUNK_MAX_CHARS
    chunk_max_output_tokens: int          # DOCPIPE_CHUNK_MAX_OUTPUT_TOKENS

    # -----------------------------------------------------------------------
    # Output token budgets per task type
    # -----------------------------------------------------------------------
    max_output_tokens: int                # DOCPIPE_MAX_OUTPUT_TOKENS
    struct_max_output_tokens: int         # DOCPIPE_STRUCT_MAX_OUTPUT_TOKENS
    doc_analysis_max_output_tokens: int   # DOCPIPE_DOC_ANALYSIS_MAX_OUTPUT_TOKENS
    verify_max_output_tokens: int         # DOCPIPE_VERIFY_MAX_OUTPUT_TOKENS
    aktenvermerk_max_tokens: int          # DOCPIPE_AKTENVERMERK_MAX_TOKENS

    # -----------------------------------------------------------------------
    # Classification
    # -----------------------------------------------------------------------
    classify_preview_chars: int           # DOCPIPE_CLASSIFY_PREVIEW_CHARS

    # -----------------------------------------------------------------------
    # PDF extraction / OCR polling
    # -----------------------------------------------------------------------
    ocr_min_pdf_text_chars: int           # DOCPIPE_MIN_PDF_TEXT_CHARS
    ocr_max_polls: int                    # DOCPIPE_OCR_MAX_POLLS
    ocr_poll_sleep_sec: float             # DOCPIPE_OCR_POLL_SLEEP_SEC

    # -----------------------------------------------------------------------
    # Segmenter
    # -----------------------------------------------------------------------
    segment_max_segments: int             # DOCPIPE_SEGMENT_MAX_SEGMENTS
    segment_max_workers: int              # DOCPIPE_SEGMENT_MAX_WORKERS
    segment_boundary_threshold: float     # DOCPIPE_SEGMENT_BOUNDARY_THRESHOLD
    segment_adaptive_threshold: bool      # DOCPIPE_SEGMENT_ADAPTIVE_THRESHOLD
    segment_boundary_cooldown_pages: int  # DOCPIPE_SEGMENT_BOUNDARY_COOLDOWN_PAGES
    segment_debug: bool                   # DOCPIPE_SEGMENT_DEBUG

    # -----------------------------------------------------------------------
    # Evidence / repeated-edge-line detection
    # -----------------------------------------------------------------------
    repeat_edge_lines: int                # DOCPIPE_REPEAT_EDGE_LINES
    repeat_edge_min_pages: int            # DOCPIPE_REPEAT_EDGE_MIN_PAGES
    repeat_edge_min_ratio: float          # DOCPIPE_REPEAT_EDGE_MIN_RATIO

    # -----------------------------------------------------------------------
    # Async segment analysis (concurrency limit)
    # Recommendation depends on the Azure OpenAI tier:
    #   S0 (free/dev): 1-2    S1 (production): 3-5
    # -----------------------------------------------------------------------
    max_concurrent_segments: int          # DOCPIPE_MAX_CONCURRENT_SEGMENTS

    # -----------------------------------------------------------------------
    # Document cache. Keyed by SHA-256 of the document, but the cached VALUE is
    # the full analysis result, including extracted values and verbatim quotes,
    # written to doc_cache.sqlite on disk. Off by default: enabling it trades
    # confidentiality for the cost of re-running the model on a repeat upload.
    # -----------------------------------------------------------------------
    doc_cache_enabled: bool               # DOCPIPE_DOC_CACHE_ENABLED (default: False)
    doc_cache_ttl_seconds: int            # DOCPIPE_DOC_CACHE_TTL_SECONDS (default: 3600)

    # -----------------------------------------------------------------------
    # Feature flags
    # -----------------------------------------------------------------------
    allow_plaintext_preview: bool         # ALLOW_PLAINTEXT_PREVIEW
    enable_fuzzy: bool                    # ENABLE_FUZZY
    citation_verify: bool                 # DOCPIPE_CITATION_VERIFY
    debug: bool                           # DEBUG
    debug_deadlines: bool                 # DOCPIPE_DEBUG_DEADLINES
    log_level: str                        # LOG_LEVEL (DEBUG|INFO|WARNING|ERROR)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_CONFIG: AppConfig | None = None


def get_config() -> AppConfig:
    """Returns the singleton AppConfig (loaded on first call)."""
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
            # LLM input budget
            input_max_chars=max(0, _getenv_int("DOCPIPE_INPUT_MAX_CHARS", 0)),
            # Chunking
            chunk_max_chars=max(1, _getenv_int("DOCPIPE_CHUNK_MAX_CHARS", 12000)),
            chunk_max_output_tokens=max(1, _getenv_int("DOCPIPE_CHUNK_MAX_OUTPUT_TOKENS", 1200)),
            # Output tokens
            max_output_tokens=max(1, _getenv_int("DOCPIPE_MAX_OUTPUT_TOKENS", 2200)),
            struct_max_output_tokens=max(1, _getenv_int("DOCPIPE_STRUCT_MAX_OUTPUT_TOKENS", 2200)),
            doc_analysis_max_output_tokens=max(1, _getenv_int("DOCPIPE_DOC_ANALYSIS_MAX_OUTPUT_TOKENS", 2400)),
            verify_max_output_tokens=max(1, _getenv_int("DOCPIPE_VERIFY_MAX_OUTPUT_TOKENS", 1800)),
            aktenvermerk_max_tokens=max(1, _getenv_int("DOCPIPE_AKTENVERMERK_MAX_TOKENS", 2200)),
            # Classification
            classify_preview_chars=max(1, _getenv_int("DOCPIPE_CLASSIFY_PREVIEW_CHARS", 1200)),
            # OCR
            ocr_min_pdf_text_chars=max(0, _getenv_int("DOCPIPE_MIN_PDF_TEXT_CHARS", 120)),
            ocr_max_polls=max(1, _getenv_int("DOCPIPE_OCR_MAX_POLLS", 40)),
            ocr_poll_sleep_sec=max(0.0, _getenv_float("DOCPIPE_OCR_POLL_SLEEP_SEC", 1.0)),
            # Segmenter
            segment_max_segments=max(2, _getenv_int("DOCPIPE_SEGMENT_MAX_SEGMENTS", 25)),
            segment_max_workers=max(1, _getenv_int("DOCPIPE_SEGMENT_MAX_WORKERS", 2)),
            segment_boundary_threshold=max(0.1, _getenv_float("DOCPIPE_SEGMENT_BOUNDARY_THRESHOLD", 2.4)),
            segment_adaptive_threshold=_getenv_bool("DOCPIPE_SEGMENT_ADAPTIVE_THRESHOLD", True),
            segment_boundary_cooldown_pages=max(0, _getenv_int("DOCPIPE_SEGMENT_BOUNDARY_COOLDOWN_PAGES", 1)),
            segment_debug=_getenv_bool("DOCPIPE_SEGMENT_DEBUG", False),
            # Evidence
            repeat_edge_lines=max(1, _getenv_int("DOCPIPE_REPEAT_EDGE_LINES", 4)),
            repeat_edge_min_pages=max(2, _getenv_int("DOCPIPE_REPEAT_EDGE_MIN_PAGES", 3)),
            repeat_edge_min_ratio=min(1.0, max(0.0, _getenv_float("DOCPIPE_REPEAT_EDGE_MIN_RATIO", 0.5))),
            # Async concurrency
            max_concurrent_segments=max(1, _getenv_int("DOCPIPE_MAX_CONCURRENT_SEGMENTS", 3)),
            # Document cache
            doc_cache_enabled=_getenv_bool("DOCPIPE_DOC_CACHE_ENABLED", False),
            doc_cache_ttl_seconds=max(60, _getenv_int("DOCPIPE_DOC_CACHE_TTL_SECONDS", 3600)),
            # Feature flags
            allow_plaintext_preview=_getenv_bool("ALLOW_PLAINTEXT_PREVIEW", False),
            enable_fuzzy=_getenv_bool("ENABLE_FUZZY", False),
            # Citation verification is the headline feature of this pipeline, so in
            # demo mode (DOCPIPE_DEMO_MODE=1) it defaults to ON - otherwise the demo
            # screenshot would show "n/a" for the single most important metric.
            # This only changes the DEFAULT: if the operator has explicitly set
            # DOCPIPE_CITATION_VERIFY (checked via `in os.environ`, which is True
            # even for "0"/"false"), that explicit choice always wins, in demo
            # mode or not. The production default (no demo mode, no explicit
            # setting) is unchanged: False.
            citation_verify=_getenv_bool(
                "DOCPIPE_CITATION_VERIFY",
                "DOCPIPE_CITATION_VERIFY" not in os.environ and _is_demo_mode_enabled(),
            ),
            debug=_getenv_bool("DEBUG", False),
            debug_deadlines=_getenv_bool("DOCPIPE_DEBUG_DEADLINES", False),
            log_level=_getenv_str("LOG_LEVEL", "INFO").strip().upper(),
        )
    return _CONFIG


def reset_config() -> None:
    """Resets the singleton. For use in tests only."""
    global _CONFIG
    _CONFIG = None
