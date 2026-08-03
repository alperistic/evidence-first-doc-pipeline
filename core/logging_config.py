"""core/logging_config.py – Strukturiertes Logging via structlog.

§203 StGB-konform: Es werden NIEMALS Klartexte aus Dokumenten geloggt.
Erlaubte Log-Felder: stage, segment_id, doc_hash (SHA256), duration_ms,
                     token_count, model, event (Fehlerbeschreibung ohne Klartext).

Konfiguration:
  LOG_LEVEL=DEBUG|INFO|WARNING|ERROR  (Default: INFO)
  LOG_FORMAT=json|console             (Default: console; in Prod: json)
"""

from __future__ import annotations

import logging
import os
import sys

import structlog


def _log_level_from_env() -> int:
    raw = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    mapping = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return mapping.get(raw, logging.INFO)


def _use_json_format() -> bool:
    raw = os.getenv("LOG_FORMAT", "console").strip().lower()
    return raw == "json"


def configure_logging() -> None:
    """Richtet structlog ein. Einmalig beim App-Start aufrufen."""
    level = _log_level_from_env()

    # Stdlib-Logging auf gleichen Level setzen (structlog nutzt stdlib als Backend)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if _use_json_format():
        # Produktion: maschinenlesbares JSON
        renderer = structlog.processors.JSONRenderer()
    else:
        # Entwicklung: farbige Konsolenausgabe
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Vorhandene Handler ersetzen
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Gibt einen benannten structlog Logger zurück.

    Verwendung:
        log = get_logger(__name__)
        log.info("segment_processed", stage="pipeline", duration_ms=120, token_count=800)
    """
    return structlog.get_logger(name)
