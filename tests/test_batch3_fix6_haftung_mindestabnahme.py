"""
tests/test_batch3_fix6_haftung_mindestabnahme.py

Batch-3 FIX 6: Haftungsbegrenzung und Mindestabnahme-Beträge erscheinen
in den Eckdaten (wurden vorher durch _amount_label_from_context → None
unterdrückt, weil die Keywords fehlten).
"""
import pytest
from core.labels import _amount_label_from_context


@pytest.mark.parametrize("context,expected", [
    # Haftungsbegrenzung
    ("Haftungsbegrenzung EUR 5.000.000,00", "Haftungsbegrenzung"),
    ("Die Haftungsobergrenze beträgt EUR 5.000.000", "Haftungsbegrenzung"),
    ("Haftungslimit EUR 2.000.000 pro Schadensfall", "Haftungsbegrenzung"),
    ("Haftungsgrenze EUR 1.000.000,00", "Haftungsbegrenzung"),
    # Haftung generisch (kein spezifisches Begrenzungs-Keyword)
    ("Haftung EUR 500.000,00", "Haftung"),
    # Mindestabnahme
    ("Mindestabnahmeverpflichtung EUR 50.000,00 jährlich", "Mindestabnahme"),
    ("Mindestabnahme EUR 10.000,00 pro Quartal", "Mindestabnahme"),
    ("Mindestbestellwert EUR 5.000,00", "Mindestabnahme"),
    # Vertragsstrafe (bereits vorhanden — Regression sichern)
    ("Vertragsstrafe 0,5% je Woche der Verzögerung", "Vertragsstrafe"),
    ("Bei Verstoß gilt eine Vertragsstrafe EUR 10.000,00", "Vertragsstrafe"),
])
def test_haftung_mindestabnahme_labels(context, expected):
    result = _amount_label_from_context(context)
    assert result == expected, f"context={context!r}: expected {expected!r}, got {result!r}"
