"""core/doc_cache.py – SHA256-based analysis cache.

Cache key:   SHA256(doc_hash + "|" + policy_hash + "|" + model + "|" + runtime_scope)
Storage:     SQLite (in-memory for tests, file-based for production)
TTL:         configurable via AppConfig.doc_cache_ttl_seconds
Data handling: only the cache key (a hash) and the already-anonymized
             result_bundle/audit_entry are ever stored – plaintext document
             content is never written to this cache.

Public API:
    build_cache_key(doc_hash, policy_hash, model, runtime_scope="") → str
    get_cached_result(cache_key, ttl_seconds) → CacheHit | None
    put_cached_result(cache_key, result_bundle, audit_entry) → None
    clear_expired(ttl_seconds) → int          (number of deleted rows)
    _set_db_path(path)                        (test use only)
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import TypedDict

from core.logging_config import get_logger

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# DB path (override via _set_db_path in tests)
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = Path("doc_cache.sqlite")
_db_path: Path = _DEFAULT_DB_PATH
_db_lock = threading.Lock()


def _set_db_path(path: str | Path) -> None:
    """Sets the DB path. Test use only."""
    global _db_path
    _db_path = Path(path)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS doc_cache (
    cache_key   TEXT    PRIMARY KEY,
    created_at  TEXT    NOT NULL,
    result_json TEXT    NOT NULL,
    audit_json  TEXT    NOT NULL
);
"""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(_DDL)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_cache_key(
    doc_hash: str,
    policy_hash: str,
    model: str,
    runtime_scope: str = "",
) -> str:
    """Computes the cache key as SHA256(doc_hash|policy_hash|model|runtime_scope).

    Contains no plaintext – only hashes and the model name.
    """
    raw = f"{doc_hash}|{policy_hash}|{model}|{runtime_scope}"
    return sha256(raw.encode("utf-8")).hexdigest()


class CacheHit(TypedDict):
    result_bundle: dict
    audit_entry: dict
    cache_key: str
    created_at: str


def get_cached_result(cache_key: str, ttl_seconds: int) -> CacheHit | None:
    """Returns a cached result, or None on a miss/expiry.

    Args:
        cache_key:   As produced by build_cache_key().
        ttl_seconds: Maximum age in seconds; older entries are treated
                     as a miss (but are not automatically deleted).
    """
    with _db_lock:
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT cache_key, created_at, result_json, audit_json "
                "FROM doc_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            conn.close()
        except sqlite3.Error as exc:
            _log.warning("doc_cache_read_error", cache_key=cache_key[:16], error=repr(exc))
            return None

    if row is None:
        return None

    created_at_str: str = row[1]
    try:
        created_at = datetime.fromisoformat(created_at_str)
    except ValueError:
        return None

    age_seconds = (datetime.now(timezone.utc) - created_at).total_seconds()
    if age_seconds > ttl_seconds:
        _log.info("doc_cache_expired", cache_key=cache_key[:16], age_s=int(age_seconds))
        return None

    try:
        result_bundle: dict = json.loads(row[2])
        audit_entry: dict = json.loads(row[3])
    except (json.JSONDecodeError, TypeError) as exc:
        _log.warning("doc_cache_decode_error", cache_key=cache_key[:16], error=repr(exc))
        return None

    _log.info("doc_cache_hit", cache_key=cache_key[:16], age_s=int(age_seconds))
    return CacheHit(
        result_bundle=result_bundle,
        audit_entry=audit_entry,
        cache_key=row[0],
        created_at=created_at_str,
    )


def put_cached_result(
    cache_key: str,
    result_bundle: dict,
    audit_entry: dict,
) -> None:
    """Stores an analysis result in the cache.

    Data-handling note: only the already-anonymized result_bundle is
    stored. Plaintext document content must never be part of
    result_bundle (this is the caller's responsibility).
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        result_json = json.dumps(result_bundle, ensure_ascii=False)
        audit_json = json.dumps(audit_entry, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        _log.warning("doc_cache_serialize_error", cache_key=cache_key[:16], error=repr(exc))
        return

    with _db_lock:
        try:
            conn = _get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO doc_cache "
                "(cache_key, created_at, result_json, audit_json) VALUES (?, ?, ?, ?)",
                (cache_key, now, result_json, audit_json),
            )
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            _log.warning("doc_cache_write_error", cache_key=cache_key[:16], error=repr(exc))
            return

    _log.info("doc_cache_stored", cache_key=cache_key[:16])


def clear_expired(ttl_seconds: int) -> int:
    """Deletes expired cache entries. Returns the number of deleted rows."""
    with _db_lock:
        try:
            conn = _get_conn()
            cur = conn.execute(
                "DELETE FROM doc_cache "
                "WHERE (julianday('now') - julianday(created_at)) * 86400.0 >= ?",
                (float(ttl_seconds),),
            )
            deleted = cur.rowcount
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            _log.warning("doc_cache_clear_error", error=repr(exc))
            return 0
    _log.info("doc_cache_cleared_expired", deleted=deleted)
    return deleted
