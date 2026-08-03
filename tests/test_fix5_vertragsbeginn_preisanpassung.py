"""
tests/test_fix5_vertragsbeginn_preisanpassung.py

FIX 5: Datum im Preisanpassungs-/Stichtag-Kontext soll NICHT als
datum_beginn extrahiert werden (würde sonst das falsche Jahr zeigen).
Echter Vertragsbeginn (direkt nach "beginnt am" etc.) bleibt erhalten.
"""
import pytest
from core.evidence_map import build_evidence_map


def _pages(page_no: int, text: str):
    return [{"page": page_no, "text": text}]


def test_date_in_preisanpassung_context_not_extracted_as_beginn():
    """Datum nach 'frühestens 12 Monate nach Vertragsbeginn' → kein datum_beginn."""
    text = (
        "Der Vertrag beginnt am 01.05.2025. "
        "Eine Preisanpassung ist frühestens 12 Monate nach Vertragsbeginn, "
        "also ab 01.05.2026, möglich."
    )
    em = build_evidence_map(_pages(1, text))
    beginn_items = [e for e in em if e.get("field") == "datum_beginn" and e.get("value") != "missing"]
    values = [e["value"] for e in beginn_items]
    assert "01.05.2026" not in values, (
        f"01.05.2026 from Preisanpassung context must not be datum_beginn, got: {values}"
    )


def test_actual_vertragsbeginn_is_extracted():
    """Echter Vertragsbeginn 'beginnt am 01.05.2025' → datum_beginn extrahiert."""
    text = (
        "Der Vertrag beginnt am 01.05.2025. "
        "Eine Preiserhöhung ist frühestens nach 12 Monaten möglich."
    )
    em = build_evidence_map(_pages(1, text))
    beginn_items = [e for e in em if e.get("field") == "datum_beginn" and e.get("value") != "missing"]
    values = [e["value"] for e in beginn_items]
    assert "01.05.2025" in values, (
        f"01.05.2025 should be extracted as datum_beginn, got: {values}"
    )
