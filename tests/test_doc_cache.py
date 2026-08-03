"""tests/test_doc_cache.py – Unit-Tests für core/doc_cache.py."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

import core.doc_cache as dc


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path: Path):
    """Jeder Test bekommt eine eigene In-Memory-SQLite-DB."""
    db_file = tmp_path / "test_cache.sqlite"
    dc._set_db_path(db_file)
    yield
    dc._set_db_path(dc._DEFAULT_DB_PATH)


# ---------------------------------------------------------------------------
# build_cache_key
# ---------------------------------------------------------------------------

class TestBuildCacheKey:
    def test_deterministic(self):
        k1 = dc.build_cache_key("abc", "def", "gpt-4o")
        k2 = dc.build_cache_key("abc", "def", "gpt-4o")
        assert k1 == k2

    def test_different_inputs_differ(self):
        k1 = dc.build_cache_key("abc", "def", "gpt-4o")
        k2 = dc.build_cache_key("abc", "def", "gpt-4o-mini")
        k3 = dc.build_cache_key("xyz", "def", "gpt-4o")
        assert k1 != k2
        assert k1 != k3

    def test_runtime_scope_changes_key(self):
        k1 = dc.build_cache_key("abc", "def", "gpt-4o", "scope-a")
        k2 = dc.build_cache_key("abc", "def", "gpt-4o", "scope-b")
        assert k1 != k2

    def test_is_hex_64_chars(self):
        k = dc.build_cache_key("a", "b", "m")
        assert len(k) == 64
        assert all(c in "0123456789abcdef" for c in k)

    def test_no_plaintext_in_key(self):
        doc_hash = "a" * 64
        k = dc.build_cache_key(doc_hash, "policy123", "gpt-4o")
        # Key ist SHA256 – darf keinen Klartext-Substring enthalten
        assert "policy123" not in k
        assert doc_hash not in k


# ---------------------------------------------------------------------------
# put / get round-trip
# ---------------------------------------------------------------------------

class TestPutGet:
    def test_miss_returns_none(self):
        assert dc.get_cached_result("nonexistent_key", ttl_seconds=3600) is None

    def test_round_trip(self):
        key = dc.build_cache_key("h1", "p1", "m1")
        rb = {"segment_results": [{"analysis": {"kernaussagen": ["Punkt 1"]}}]}
        ae = {"doc_hash": "h1", "model": "m1"}
        dc.put_cached_result(key, rb, ae)
        hit = dc.get_cached_result(key, ttl_seconds=3600)
        assert hit is not None
        assert hit["result_bundle"] == rb
        assert hit["audit_entry"] == ae
        assert hit["cache_key"] == key

    def test_hit_contains_created_at(self):
        key = dc.build_cache_key("h2", "p2", "m2")
        dc.put_cached_result(key, {}, {})
        hit = dc.get_cached_result(key, ttl_seconds=3600)
        assert hit is not None
        assert "created_at" in hit
        assert hit["created_at"]  # not empty

    def test_insert_or_replace(self):
        key = dc.build_cache_key("h3", "p3", "m3")
        dc.put_cached_result(key, {"v": 1}, {})
        dc.put_cached_result(key, {"v": 2}, {})
        hit = dc.get_cached_result(key, ttl_seconds=3600)
        assert hit is not None
        assert hit["result_bundle"]["v"] == 2


# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------

class TestTTL:
    def test_expired_returns_none(self):
        key = dc.build_cache_key("h4", "p4", "m4")
        dc.put_cached_result(key, {"data": "ok"}, {})
        # TTL von 0 Sekunden → sofort abgelaufen
        result = dc.get_cached_result(key, ttl_seconds=0)
        assert result is None

    def test_fresh_entry_not_expired(self):
        key = dc.build_cache_key("h5", "p5", "m5")
        dc.put_cached_result(key, {"data": "ok"}, {})
        hit = dc.get_cached_result(key, ttl_seconds=3600)
        assert hit is not None


# ---------------------------------------------------------------------------
# clear_expired
# ---------------------------------------------------------------------------

class TestClearExpired:
    def test_clears_old_entries(self):
        k1 = dc.build_cache_key("h6", "p6", "m6")
        k2 = dc.build_cache_key("h7", "p7", "m7")
        dc.put_cached_result(k1, {}, {})
        dc.put_cached_result(k2, {}, {})
        # ttl=0 → alle Einträge gelten als abgelaufen
        deleted = dc.clear_expired(ttl_seconds=0)
        assert deleted == 2
        assert dc.get_cached_result(k1, ttl_seconds=3600) is None
        assert dc.get_cached_result(k2, ttl_seconds=3600) is None

    def test_keeps_fresh_entries(self):
        k = dc.build_cache_key("h8", "p8", "m8")
        dc.put_cached_result(k, {"fresh": True}, {})
        deleted = dc.clear_expired(ttl_seconds=3600)
        assert deleted == 0
        hit = dc.get_cached_result(k, ttl_seconds=3600)
        assert hit is not None

    def test_returns_zero_on_empty_db(self):
        assert dc.clear_expired(ttl_seconds=60) == 0


# ---------------------------------------------------------------------------
# §203-Konformität: kein Klartext
# ---------------------------------------------------------------------------

class TestParagraph203:
    def test_no_plaintext_in_stored_key(self):
        """Cache-Key ist SHA256 – enthält keinen Klartext."""
        doc_hash = "a" * 64
        policy_hash = "b" * 64
        key = dc.build_cache_key(doc_hash, policy_hash, "gpt-4o")
        # Der Key darf den Original-doc_hash nicht plain enthalten
        assert doc_hash not in key
        assert policy_hash not in key

    def test_result_bundle_stored_as_given(self):
        """Der Cache speichert result_bundle so wie übergeben (Aufrufer ist verantwortlich)."""
        key = dc.build_cache_key("hA", "pA", "mA")
        safe_bundle = {"segment_results": [], "segment_count": 0}
        dc.put_cached_result(key, safe_bundle, {"doc_hash": "hA"})
        hit = dc.get_cached_result(key, ttl_seconds=3600)
        assert hit is not None
        # Kein zusätzlicher Klartext wurde hinzugefügt
        assert hit["result_bundle"] == safe_bundle
