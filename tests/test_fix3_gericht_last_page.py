"""
tests/test_fix3_gericht_last_page.py

FIX 3: Gericht-Treffer die NUR auf der letzten Seite eines
Mehrseiten-Segments stehen → gefiltert (Cross-Segment-Kontamination).
Gericht-Treffer auf der ersten Seite → bleiben erhalten.
"""
import pytest
from core.evidence_map import build_evidence_map


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
