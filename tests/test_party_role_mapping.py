"""
Party/role extraction in the evidence map: recognizing contracting parties
from role-labeled lines (Lieferant, Auftragnehmer, Auftraggeber) in supply
contracts, and filtering court ("Gericht") mentions that only appear on the
trailing page of a multi-page segment (cross-segment contamination) while
keeping genuine court references on the first page or in short segments.
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


def _make_pages(*texts_with_page):
    """Helper: [(page_no, text), ...] → pages list."""
    return [{"page": p, "text": t} for p, t in texts_with_page]


def test_gericht_only_on_last_page_is_filtered():
    """Gericht nur auf letzter Seite eines 5-Seiten-Segments → kein Gericht in Evidence."""
    pages = _make_pages(
        (10, "Testament des Erblassers Müller. Keinerlei Gerichtsangabe."),
        (11, "Erbschaftssachen werden hier geregelt."),
        (12, "Weitere Ausführungen ohne Gerichtsnennung."),
        (13, "Noch eine Seite ohne Gericht."),
        (14, "Amtsgericht Mannheim – dies ist schon der nächste Abschnitt."),
    )
    evidence_map = build_evidence_map(pages)
    gericht_items = [e for e in evidence_map if e.get("field") == "gericht" and e.get("value") != "missing"]
    assert gericht_items == [], (
        f"Gericht on last page only (5-page segment) should be filtered, got: {gericht_items}"
    )


def test_gericht_only_on_last_page_short_segment_kept():
    """Gericht nur auf Seite 2 (letzte) eines 2-Seiten-Segments → bleibt erhalten (Gerichtsstandklausel)."""
    pages = _make_pages(
        (10, "Vertrag über Werkleistungen. Keinerlei Gerichtsangabe hier."),
        (11, "Gerichtsstand ist das Amtsgericht Mannheim."),
    )
    evidence_map = build_evidence_map(pages)
    gericht_items = [e for e in evidence_map if e.get("field") == "gericht" and e.get("value") != "missing"]
    assert len(gericht_items) >= 1, (
        f"Gericht on last page of 2-page segment should be kept, got: {gericht_items}"
    )


def test_gericht_only_on_last_page_3page_segment_kept():
    """Gericht nur auf Seite 3 (letzte) eines 3-Seiten-Segments → bleibt erhalten."""
    pages = _make_pages(
        (10, "Vertrag. Keinerlei Gerichtsangabe."),
        (11, "Weitere Bedingungen ohne Gericht."),
        (12, "Gerichtsstand: Amtsgericht Mannheim."),
    )
    evidence_map = build_evidence_map(pages)
    gericht_items = [e for e in evidence_map if e.get("field") == "gericht" and e.get("value") != "missing"]
    assert len(gericht_items) >= 1, (
        f"Gericht on last page of 3-page segment should be kept, got: {gericht_items}"
    )


def test_gericht_on_first_page_is_kept():
    """Gericht auf Seite 1 eines 3-Seiten-Segments → bleibt erhalten."""
    pages = _make_pages(
        (10, "Klage beim Amtsgericht Frankfurt eingereicht."),
        (11, "Weitere Ausführungen zum Sachverhalt."),
        (12, "Letzte Seite ohne Gerichtsnennung."),
    )
    evidence_map = build_evidence_map(pages)
    gericht_items = [e for e in evidence_map if e.get("field") == "gericht" and e.get("value") != "missing"]
    assert len(gericht_items) >= 1, (
        f"Gericht on first page should be kept, evidence_map={evidence_map}"
    )


def test_gericht_single_page_segment_kept():
    """Einzelseiten-Segment: Gericht wird nicht gefiltert."""
    pages = _make_pages(
        (5, "Verhandlung vor dem Landgericht Berlin am 01.04.2026."),
    )
    evidence_map = build_evidence_map(pages)
    gericht_items = [e for e in evidence_map if e.get("field") == "gericht" and e.get("value") != "missing"]
    assert len(gericht_items) >= 1, (
        f"Single-page segment: Gericht should not be filtered, evidence_map={evidence_map}"
    )
