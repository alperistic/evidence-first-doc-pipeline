"""
tests/test_fix6b_bussgeld_label.py

FIX 6: Bußgeld / Verwarnungsgeld / Geldbuße → Label "Bußgeld"
Vorrang vor "Vertragsstrafe", wenn Bußgeld-Keyword im Kontext steht.
"""
import pytest
from core.labels import _amount_label_from_context


@pytest.mark.parametrize("context,expected", [
    ("Bußgeld: EUR 180,00 gemäß OWiG", "Bußgeld"),
    ("Verwarnungsgeld EUR 180,00 wegen Geschwindigkeitsüberschreitung", "Bußgeld"),
    ("Geldbuße von EUR 250,00 festgesetzt", "Bußgeld"),
    ("bussgeld EUR 100,00", "Bußgeld"),
    ("OWi-Verfahren, EUR 180,00 fällig", "Bußgeld"),
    # Bußgeld hat Vorrang vor Vertragsstrafe wenn beide im Kontext
    ("Bußgeld wegen Verstoß EUR 180,00", "Bußgeld"),
    # Normale Vertragsstrafe (kein Bußgeld-Keyword)
    ("Vertragsstrafe EUR 500,00", "Vertragsstrafe"),
])
def test_bussgeld_label(context, expected):
    result = _amount_label_from_context(context)
    assert result == expected, f"context={context!r}: expected {expected!r}, got {result!r}"
