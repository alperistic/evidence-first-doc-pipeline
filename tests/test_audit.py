"""
test_audit.py – Unit Tests für governance/audit.py

Testet log_request() und get_recent_logs() isoliert via In-Memory-DB
(monkeypatching von governance.audit.DB_PATH auf ":memory:").
"""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

import governance.audit as audit_mod
from governance.audit import log_request, get_recent_logs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_audit_db(tmp_path, monkeypatch):
    """Jeder Test bekommt eine eigene temp-DB – kein gemeinsamer Zustand."""
    db_file = tmp_path / "test_audit.db"
    monkeypatch.setattr(audit_mod, "DB_PATH", db_file)
    yield db_file


# ---------------------------------------------------------------------------
# Hilfsfunktion: direkt in DB schauen
# ---------------------------------------------------------------------------

def _fetch_all(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# log_request – Happy Path
# ---------------------------------------------------------------------------

class TestLogRequestHappyPath:
    def test_creates_row(self, tmp_audit_db):
        log_request(
            doc_hash="abc123",
            doc_class="Vertrag",
            token_count=500,
            model="gpt-4o",
        )
        rows = _fetch_all(tmp_audit_db)
        assert len(rows) == 1

    def test_pflichtfelder_korrekt(self, tmp_audit_db):
        log_request(
            doc_hash="deadbeef",
            doc_class="Schriftsatz",
            token_count=123,
            model="gpt-4o",
        )
        row = _fetch_all(tmp_audit_db)[0]
        assert row["doc_hash"] == "deadbeef"
        assert row["doc_class"] == "Schriftsatz"
        assert row["token_count"] == 123
        assert row["model"] == "gpt-4o"

    def test_timestamp_ist_gesetzt(self, tmp_audit_db):
        log_request(doc_hash="h", doc_class="Vertrag", token_count=1, model="m")
        row = _fetch_all(tmp_audit_db)[0]
        assert row["timestamp"]  # nicht leer

    def test_optionale_token_felder(self, tmp_audit_db):
        log_request(
            doc_hash="h",
            doc_class="Vertrag",
            token_count=300,
            model="m",
            total_tokens=300,
            prompt_tokens=200,
            completion_tokens=100,
        )
        row = _fetch_all(tmp_audit_db)[0]
        assert row["total_tokens"] == 300
        assert row["prompt_tokens"] == 200
        assert row["completion_tokens"] == 100

    def test_policy_felder(self, tmp_audit_db):
        log_request(
            doc_hash="h",
            doc_class="Vertrag",
            token_count=10,
            model="m",
            policy_profile="standard",
            policy_hash="ph42",
        )
        row = _fetch_all(tmp_audit_db)[0]
        assert row["policy_profile"] == "standard"
        assert row["policy_hash"] == "ph42"

    def test_redaction_flags_als_int(self, tmp_audit_db):
        log_request(
            doc_hash="h",
            doc_class="Vertrag",
            token_count=10,
            model="m",
            redaction_applied=True,
            pseudonymization_applied=True,
        )
        row = _fetch_all(tmp_audit_db)[0]
        assert row["redaction_applied"] == 1
        assert row["pseudonymization_applied"] == 1

    def test_redaction_flags_false(self, tmp_audit_db):
        log_request(doc_hash="h", doc_class="Vertrag", token_count=10, model="m")
        row = _fetch_all(tmp_audit_db)[0]
        assert row["redaction_applied"] == 0
        assert row["pseudonymization_applied"] == 0

    def test_output_type_und_citation_status(self, tmp_audit_db):
        log_request(
            doc_hash="h",
            doc_class="Sonstiges",
            token_count=5,
            model="m",
            output_type="parse_error",
            citation_status="ok",
        )
        row = _fetch_all(tmp_audit_db)[0]
        assert row["output_type"] == "parse_error"
        assert row["citation_status"] == "ok"

    def test_output_json_wird_serialisiert(self, tmp_audit_db):
        payload = {"score": 42, "tags": ["A", "B"]}
        log_request(
            doc_hash="h",
            doc_class="Vertrag",
            token_count=10,
            model="m",
            output_json=payload,
        )
        row = _fetch_all(tmp_audit_db)[0]
        assert json.loads(row["output_json"]) == payload

    def test_output_json_none_ergibt_leeres_objekt(self, tmp_audit_db):
        log_request(doc_hash="h", doc_class="Vertrag", token_count=10, model="m")
        row = _fetch_all(tmp_audit_db)[0]
        assert json.loads(row["output_json"]) == {}

    def test_mehrere_eintraege(self, tmp_audit_db):
        for i in range(5):
            log_request(doc_hash=f"h{i}", doc_class="Vertrag", token_count=i, model="m")
        assert len(_fetch_all(tmp_audit_db)) == 5


# ---------------------------------------------------------------------------
# log_request – Token-Count Fallbacks
# ---------------------------------------------------------------------------

class TestTokenCountFallbacks:
    def test_token_count_none_wird_0(self, tmp_audit_db):
        log_request(doc_hash="h", doc_class="Vertrag", token_count=None, model="m")
        row = _fetch_all(tmp_audit_db)[0]
        assert row["token_count"] == 0

    def test_token_count_invalid_string_wird_0(self, tmp_audit_db):
        log_request(doc_hash="h", doc_class="Vertrag", token_count="INVALID", model="m")  # type: ignore
        row = _fetch_all(tmp_audit_db)[0]
        assert row["token_count"] == 0

    def test_total_tokens_none_erbt_token_count(self, tmp_audit_db):
        """Wenn total_tokens=None und token_count > 0, soll total_tokens = token_count."""
        log_request(
            doc_hash="h",
            doc_class="Vertrag",
            token_count=400,
            model="m",
            total_tokens=None,
        )
        row = _fetch_all(tmp_audit_db)[0]
        assert row["total_tokens"] == 400

    def test_total_tokens_explizit_ueberschreibt(self, tmp_audit_db):
        log_request(
            doc_hash="h",
            doc_class="Vertrag",
            token_count=400,
            model="m",
            total_tokens=600,
        )
        row = _fetch_all(tmp_audit_db)[0]
        assert row["total_tokens"] == 600

    def test_total_tokens_invalid_wird_none(self, tmp_audit_db):
        log_request(
            doc_hash="h",
            doc_class="Vertrag",
            token_count=0,
            model="m",
            total_tokens="bad",  # type: ignore
        )
        row = _fetch_all(tmp_audit_db)[0]
        assert row["total_tokens"] is None


# ---------------------------------------------------------------------------
# log_request – Fail-Safe (darf nie Exception werfen)
# ---------------------------------------------------------------------------

class TestLogRequestFailSafe:
    def test_keine_exception_bei_db_fehler(self, tmp_audit_db, monkeypatch):
        """log_request darf bei DB-Fehler nicht werfen."""
        def broken_connect():
            raise sqlite3.OperationalError("forced failure")

        monkeypatch.setattr(audit_mod, "_connect", broken_connect)
        # Darf keine Exception werfen:
        log_request(doc_hash="h", doc_class="Vertrag", token_count=1, model="m")

    def test_keine_exception_bei_serialisierungsfehler(self, tmp_audit_db):
        """log_request darf bei nicht-serialisierbarem output_json nicht werfen."""

        class NotSerializable:
            pass

        # json.dumps wird intern fehlschlagen – trotzdem kein raise
        log_request(
            doc_hash="h",
            doc_class="Vertrag",
            token_count=1,
            model="m",
            output_json={"obj": NotSerializable()},  # type: ignore
        )


# ---------------------------------------------------------------------------
# get_recent_logs
# ---------------------------------------------------------------------------

class TestGetRecentLogs:
    def test_leere_db_gibt_leere_liste(self, tmp_audit_db):
        result = get_recent_logs()
        assert result == []

    def test_gibt_eintraege_zurueck(self, tmp_audit_db):
        log_request(doc_hash="h1", doc_class="Vertrag", token_count=10, model="m")
        log_request(doc_hash="h2", doc_class="Schriftsatz", token_count=20, model="m")
        result = get_recent_logs()
        assert len(result) == 2

    def test_neueste_eintraege_zuerst(self, tmp_audit_db):
        for i in range(5):
            log_request(doc_hash=f"h{i}", doc_class="Vertrag", token_count=i, model="m")
        result = get_recent_logs()
        ids = [r["id"] for r in result]
        assert ids == sorted(ids, reverse=True)

    def test_limit_wird_eingehalten(self, tmp_audit_db):
        for i in range(10):
            log_request(doc_hash=f"h{i}", doc_class="Vertrag", token_count=i, model="m")
        result = get_recent_logs(limit=3)
        assert len(result) == 3

    def test_felder_in_rueckgabe(self, tmp_audit_db):
        log_request(
            doc_hash="hashval",
            doc_class="Sonstiges",
            token_count=7,
            model="gpt-4o",
            policy_profile="p",
            policy_hash="ph",
            redaction_applied=True,
        )
        row = get_recent_logs(limit=1)[0]
        assert row["doc_hash"] == "hashval"
        assert row["doc_class"] == "Sonstiges"
        assert row["token_count"] == 7
        assert row["model"] == "gpt-4o"
        assert row["policy_profile"] == "p"
        assert row["policy_hash"] == "ph"
        assert row["redaction_applied"] == 1

    def test_gibt_dicts_zurueck(self, tmp_audit_db):
        log_request(doc_hash="h", doc_class="Vertrag", token_count=1, model="m")
        result = get_recent_logs()
        assert isinstance(result[0], dict)

    def test_gibt_leere_liste_bei_db_fehler(self, tmp_audit_db, monkeypatch):
        """get_recent_logs gibt [] zurück wenn DB nicht erreichbar ist."""
        def broken_connect():
            raise sqlite3.OperationalError("forced failure")

        monkeypatch.setattr(audit_mod, "_connect", broken_connect)
        result = get_recent_logs()
        assert result == []

    def test_kein_klartext_in_output(self, tmp_audit_db):
        """output_json-Spalte darf keine sensiblen Felder durchlecken – nur Hashes."""
        log_request(
            doc_hash="sha256_only",
            doc_class="Vertrag",
            token_count=5,
            model="m",
        )
        rows = _fetch_all(tmp_audit_db)
        row_str = str(rows[0])
        # Kein Klartext-Dokumentinhalt in den Feldern
        assert "sha256_only" in row_str  # Hash ist ok
        # get_recent_logs gibt kein output_json zurück (nicht in der SELECT-Liste)
        result_row = get_recent_logs(limit=1)[0]
        assert "output_json" not in result_row


# ---------------------------------------------------------------------------
# §203-Konformität: Kein Klartext gespeichert
# ---------------------------------------------------------------------------

class TestParagraph203Konformitaet:
    def test_output_json_nicht_in_get_recent_logs(self, tmp_audit_db):
        """get_recent_logs darf output_json nicht zurückgeben (kann Analysetext enthalten)."""
        log_request(
            doc_hash="h",
            doc_class="Vertrag",
            token_count=1,
            model="m",
            output_json={"summary": "Vertrag zwischen Müller und Meier"},
        )
        row = get_recent_logs(limit=1)[0]
        # output_json ist absichtlich aus dem SELECT ausgelassen
        assert "output_json" not in row
        assert "summary" not in str(row)

    def test_doc_hash_sha256_format(self, tmp_audit_db):
        """Der gespeicherte Hash sollte kein Klartextdokument sein (min. 16 Zeichen)."""
        sha256 = "a" * 64
        log_request(doc_hash=sha256, doc_class="Vertrag", token_count=1, model="m")
        row = _fetch_all(tmp_audit_db)[0]
        assert len(row["doc_hash"]) >= 16
