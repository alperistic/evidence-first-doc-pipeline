"""
audit.py – SQLite audit trail.
Only the document hash, class, token count, and model are stored.
No plaintext, no document content, is ever persisted.
"""

import sqlite3
from datetime import datetime, timezone
import json
from pathlib import Path

from core.logging_config import get_logger

_log = get_logger(__name__)

DB_PATH = Path("audit.db")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    doc_hash    TEXT    NOT NULL,
    doc_class   TEXT    NOT NULL,
    token_count INTEGER NOT NULL,
    total_tokens INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    model       TEXT    NOT NULL,
    policy_profile TEXT NOT NULL DEFAULT '',
    policy_hash TEXT NOT NULL DEFAULT '',
    redaction_applied INTEGER NOT NULL DEFAULT 0,
    pseudonymization_applied INTEGER NOT NULL DEFAULT 0,
    classify_used_chars INTEGER NOT NULL DEFAULT 0,
    output_type TEXT    NOT NULL DEFAULT 'json_ok',
    citation_status TEXT NOT NULL DEFAULT 'n/a',
    output_json TEXT    NOT NULL DEFAULT '{}'
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(_CREATE_TABLE)
    # Migration: add output_type column for existing DBs
    existing = {row[1] for row in conn.execute("PRAGMA table_info(audit_log)")}
    if "output_type" not in existing:
        conn.execute(
            "ALTER TABLE audit_log ADD COLUMN output_type TEXT NOT NULL DEFAULT 'json_ok'"
        )
    if "policy_profile" not in existing:
        conn.execute(
            "ALTER TABLE audit_log ADD COLUMN policy_profile TEXT NOT NULL DEFAULT ''"
        )
    if "policy_hash" not in existing:
        conn.execute(
            "ALTER TABLE audit_log ADD COLUMN policy_hash TEXT NOT NULL DEFAULT ''"
        )
    if "redaction_applied" not in existing:
        conn.execute(
            "ALTER TABLE audit_log ADD COLUMN redaction_applied INTEGER NOT NULL DEFAULT 0"
        )
    if "pseudonymization_applied" not in existing:
        conn.execute(
            "ALTER TABLE audit_log ADD COLUMN pseudonymization_applied INTEGER NOT NULL DEFAULT 0"
        )
    if "classify_used_chars" not in existing:
        conn.execute(
            "ALTER TABLE audit_log ADD COLUMN classify_used_chars INTEGER NOT NULL DEFAULT 0"
        )
    if "citation_status" not in existing:
        conn.execute(
            "ALTER TABLE audit_log ADD COLUMN citation_status TEXT NOT NULL DEFAULT 'n/a'"
        )
    if "output_json" not in existing:
        conn.execute(
            "ALTER TABLE audit_log ADD COLUMN output_json TEXT NOT NULL DEFAULT '{}'"
        )
    if "total_tokens" not in existing:
        conn.execute("ALTER TABLE audit_log ADD COLUMN total_tokens INTEGER")
    if "prompt_tokens" not in existing:
        conn.execute("ALTER TABLE audit_log ADD COLUMN prompt_tokens INTEGER")
    if "completion_tokens" not in existing:
        conn.execute("ALTER TABLE audit_log ADD COLUMN completion_tokens INTEGER")
    conn.commit()
    return conn


def log_request(
    doc_hash: str,
    doc_class: str,
    token_count: int | None,
    model: str,
    total_tokens: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    policy_profile: str = "",
    policy_hash: str = "",
    redaction_applied: bool = False,
    pseudonymization_applied: bool = False,
    classify_used_chars: int = 0,
    output_type: str = "json_ok",
    citation_status: str = "n/a",
    output_json: dict | None = None,
) -> None:
    """Writes an audit entry. Never raises exceptions outward (fail-safe)."""
    try:
        ts = datetime.now(timezone.utc).isoformat()
        output_json_str = json.dumps(output_json or {}, ensure_ascii=False)
        try:
            token_count_db = int(token_count) if token_count is not None else 0
        except (TypeError, ValueError):
            token_count_db = 0
        try:
            total_tokens_db = int(total_tokens) if total_tokens is not None else None
        except (TypeError, ValueError):
            total_tokens_db = None
        if total_tokens_db is None and token_count_db > 0:
            total_tokens_db = token_count_db
        try:
            prompt_tokens_db = int(prompt_tokens) if prompt_tokens is not None else None
        except (TypeError, ValueError):
            prompt_tokens_db = None
        try:
            completion_tokens_db = int(completion_tokens) if completion_tokens is not None else None
        except (TypeError, ValueError):
            completion_tokens_db = None
        with _connect() as conn:
            conn.execute(
                "INSERT INTO audit_log "
                "("
                "timestamp, doc_hash, doc_class, token_count, total_tokens, prompt_tokens, completion_tokens, model, "
                "policy_profile, policy_hash, redaction_applied, pseudonymization_applied, classify_used_chars, "
                "output_type, citation_status, output_json"
                ") "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    doc_hash,
                    doc_class,
                    token_count_db,
                    total_tokens_db,
                    prompt_tokens_db,
                    completion_tokens_db,
                    model,
                    policy_profile,
                    policy_hash,
                    int(bool(redaction_applied)),
                    int(bool(pseudonymization_applied)),
                    int(classify_used_chars),
                    output_type,
                    citation_status,
                    output_json_str,
                ),
            )
    except Exception as exc:
        # Audit failures must never interrupt the main flow
        _ = exc  # nosec - intentionally not logged, to avoid leaking plaintext
        _log.warning("audit_write_failed", stage="audit", error_type=type(exc).__name__)


def get_recent_logs(limit: int = 50) -> list[dict]:
    """Returns the most recent entries as a list of dicts."""
    try:
        with _connect() as conn:
            cur = conn.execute(
                "SELECT id, timestamp, doc_hash, doc_class, token_count, total_tokens, prompt_tokens, completion_tokens, model, "
                "policy_profile, policy_hash, redaction_applied, pseudonymization_applied, classify_used_chars, "
                "output_type, citation_status "
                "FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
