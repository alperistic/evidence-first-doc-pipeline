"""
tests/test_batch3_fix3_lieferant_party.py

Batch-3 FIX 3: Parteien aus Rahmenlieferverträgen werden extrahiert.
"Lieferant", "Auftragnehmer", "Auftraggeber" als Zeilen-Trigger ergänzt,
so dass Firmen auf Zeilen wie "Lieferant: Precision Motors Europe AG"
als Parteien erkannt werden.
"""
import pytest
from core.evidence_map import build_evidence_map


def _pages(text: str):
    return [{"page": 1, "text": text}]


def test_lieferant_triggers_party_extraction():
    """Zeile mit 'Lieferant:' löst Parteien-Extraktion aus."""
    text = "Lieferant: Precision Motors Europe AG, Musterstraße 1, 12345 Berlin"
    em = build_evidence_map(_pages(text))
    party_values = [e["value"] for e in em if e.get("field") == "parteien"]
    assert any("Precision Motors Europe AG" in v for v in party_values), (
        f"'Precision Motors Europe AG' not found in parties: {party_values}"
    )


def test_auftragnehmer_triggers_party_extraction():
    """Zeile mit 'Auftragnehmer:' löst Parteien-Extraktion aus."""
    text = "Auftragnehmer: SüdWest Industriekomponenten GmbH"
    em = build_evidence_map(_pages(text))
    party_values = [e["value"] for e in em if e.get("field") == "parteien"]
    assert any("SüdWest Industriekomponenten GmbH" in v or "SudWest" in v or "GmbH" in v for v in party_values), (
        f"Company not found in parties: {party_values}"
    )


def test_auftraggeber_triggers_party_extraction():
    """Zeile mit 'Auftraggeber:' löst Parteien-Extraktion aus."""
    text = "Auftraggeber: Muster GmbH und Auftragnehmer: Bau AG"
    em = build_evidence_map(_pages(text))
    party_values = [e["value"] for e in em if e.get("field") == "parteien"]
    assert len(party_values) >= 1, f"Expected parties, got: {party_values}"
