"""
health.py – Lightweight health-check server (Python stdlib).

Runs as a daemon thread alongside Streamlit on a separate port.

Endpoints:
  GET /health  → 200 {"status": "ok"}                         (liveness)
  GET /ready   → 200 {"status": "ok", "db": "ok"}             (readiness)
               → 503 {"status": "error", "db": "unreachable"} (DB unreachable)

Configuration:
  HEALTH_PORT  – Port of the health server (default: 8502)

Idempotency:
  start_health_server() is safe to call multiple times (Streamlit re-executes
  app.py on every reload). A threading.Event prevents race conditions.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_HEALTH_PORT: int = int(os.getenv("HEALTH_PORT", "8502"))

# audit.db lives in the working directory – same location as governance/audit.py
_AUDIT_DB_PATH: Path = Path("audit.db")

# ---------------------------------------------------------------------------
# Idempotency guard (threading.Event is thread-safe, a bool would not be)
# ---------------------------------------------------------------------------

_started: threading.Event = threading.Event()


# ---------------------------------------------------------------------------
# DB check
# ---------------------------------------------------------------------------


def _check_db() -> tuple[bool, str]:
    """Checks whether the database is reachable.

    Returns:
        (ok, label) – ok=True if reachable, label for the JSON response.

    # TODO: swap for a PostgreSQL ping if the backing store moves to Postgres
    """
    try:
        conn = sqlite3.connect(str(_AUDIT_DB_PATH), timeout=2.0)
        conn.execute("SELECT 1")
        conn.close()
        return True, "ok"
    except Exception:
        return False, "unreachable"


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for /health and /ready."""

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._liveness()
        elif self.path == "/ready":
            self._readiness()
        else:
            self._send_json(404, {"status": "not_found"})

    def _liveness(self) -> None:
        self._send_json(200, {"status": "ok"})

    def _readiness(self) -> None:
        db_ok, db_label = _check_db()
        if db_ok:
            self._send_json(200, {"status": "ok", "db": db_label})
        else:
            self._send_json(503, {"status": "error", "db": db_label})

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: ANN002
        # Suppress stdlib HTTP logging – only enable it when DEBUG is set
        if os.getenv("DEBUG", "").lower() in {"1", "true", "yes", "on"}:
            super().log_message(fmt, *args)


# ---------------------------------------------------------------------------
# Server thread
# ---------------------------------------------------------------------------


def _serve(port: int) -> None:
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    server.serve_forever()


def start_health_server(port: int = _HEALTH_PORT) -> None:
    """Starts the health server as a daemon thread (idempotent).

    Multiple calls (e.g. from Streamlit reloads) are safe:
    threading.Event.set() + is_set() is atomic.
    """
    if _started.is_set():
        return
    _started.set()
    thread = threading.Thread(target=_serve, args=(port,), daemon=True, name="health-server")
    thread.start()
