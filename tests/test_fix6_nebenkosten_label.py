"""
tests/test_fix6_nebenkosten_label.py

FIX 6: Wenn Nebenkosten-Kontext "insgesamt" enthält, ist der Betrag
die Gesamtmiete, nicht Nebenkosten — aber NUR wenn "insgesamt" VOR
"nebenkosten" steht (d.h. der Betrag selbst ist das Gesamt).
Regression: EUR 195 mit Kontext "Nebenkosten EUR 195 ... insgesamt EUR 1.015"
darf nicht als "Gesamtmiete" gelabelt werden.
"""
import pytest

from core.labels import _amount_label_from_context


@pytest.mark.parametrize("context,expected", [
    # Gesamtmiete-Fälle: "insgesamt" steht VOR Nebenkosten (Betrag = Gesamt)
    ("insgesamt also EUR 1.015,00 (820 Kaltmiete + 195 Nebenkosten)", "Gesamtmiete"),
    ("insgesamt EUR 1.015,00 inklusive Nebenkosten", "Gesamtmiete"),
    ("Betrag insgesamt: EUR 1.015,00 inkl. Betriebskosten", "Gesamtmiete"),
    # Regression FIX 2: "nebenkosten" steht VOR "insgesamt" → Betrag ist NK-Position
    ("Nebenkostenvorauszahlung EUR 195,00 monatlich, insgesamt also EUR 1.015,00", "Nebenkosten"),
    ("Betriebskosten EUR 195,00; insgesamt EUR 1.015,00 fällig", "Nebenkosten"),
    # Normale Nebenkosten-Fälle ohne "insgesamt"
    ("Nebenkosten EUR 195,00 monatlich", "Nebenkosten"),
    ("Betriebskosten 195 EUR", "Nebenkosten"),
    # Kein Treffer → None
    ("Betrag EUR 500,00", None),
])
def test_amount_label_nebenkosten_gesamtmiete(context, expected):
    result = _amount_label_from_context(context)
    assert result == expected, f"context={context!r}: expected {expected!r}, got {result!r}"
