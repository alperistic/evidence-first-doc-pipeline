"""
health.py – Leichtgewichtiger Health-Check-Server (Python stdlib).

Läuft als Daemon-Thread parallel zu Streamlit auf einem separaten Port.

Endpunkte:
  GET /health  → 200 {"status": "ok"}                         (Liveness)
  GET /ready   → 200 {"status": "ok", "db": "ok"}             (Readiness)
               → 503 {"status": "error", "db": "unreachable"} (DB nicht erreichbar)

Konfiguration:
  HEALTH_PORT  – Port des Health-Servers (Default: 8502)

Idempotenz:
  start_health_server() ist safe bei mehrfachem Aufruf (Streamlit re-executes
  app.py bei jedem Reload). Ein threading.Event verhindert race conditions.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

_HEALTH_PORT: int = int(os.getenv("HEALTH_PORT", "8502"))

# audit.db liegt im Arbeitsverzeichnis – identisch mit governance/audit.py
_AUDIT_DB_PATH: Path = Path("audit.db")

# ---------------------------------------------------------------------------
# Idempotenz-Guard (threading.Event ist thread-safe, Bool wäre es nicht)
# ---------------------------------------------------------------------------

_started: threading.Event = threading.Event()


# ---------------------------------------------------------------------------
# DB-Check
# ---------------------------------------------------------------------------


def _check_db() -> tuple[bool, str]:
    """Prüft ob die Datenbank erreichbar ist.

    Returns:
        (ok, label) – ok=True wenn erreichbar, label für JSON-Response.

    # TODO: PostgreSQL-Ping ersetzen wenn Hetzner live
    """
    try:
        conn = sqlite3.connect(str(_AUDIT_DB_PATH), timeout=2.0)
        conn.execute("SELECT 1")
        conn.close()
        return True, "ok"
    except Exception:
        return False, "unreachable"


# ---------------------------------------------------------------------------
# Request-Handler
# ---------------------------------------------------------------------------


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimaler HTTP-Handler für /health und /ready."""

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
        # stdlib-HTTP-Logging unterdrücken – nur bei DEBUG einschalten
        if os.getenv("DEBUG", "").lower() in {"1", "true", "yes", "on"}:
            super().log_message(fmt, *args)


# ---------------------------------------------------------------------------
# Server-Thread
# ---------------------------------------------------------------------------


def _serve(port: int) -> None:
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    server.serve_forever()


def start_health_server(port: int = _HEALTH_PORT) -> None:
    """Startet den Health-Server als Daemon-Thread (idempotent).

    Mehrfache Aufrufe (z.B. durch Streamlit-Reloads) sind sicher:
    threading.Event.set() + is_set() ist atomisch.
    """
    if _started.is_set():
        return
    _started.set()
    thread = threading.Thread(target=_serve, args=(port,), daemon=True, name="health-server")
    thread.start()
