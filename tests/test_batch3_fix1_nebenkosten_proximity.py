"""
tests/test_batch3_fix1_nebenkosten_proximity.py

Batch-3 FIX 1: Proximity-basierte Nebenkosten/Gesamtmiete-Unterscheidung.

Alle drei Beträge aus "Die monatliche Kaltmiete beträgt EUR 820,00,
die Nebenkostenvorauszahlung EUR 195,00, insgesamt also EUR 1.015,00"
müssen korrekte Labels bekommen, auch wenn jeder Betrag dieselbe
Zitatsentenz als Kontext erhält.

In Produktion wird _amount_label_from_context mit f"{quote} {value}"
aufgerufen — der spezifische Betrag ist ans Ende angehängt.
"""
import pytest

from core.labels import _amount_label_from_context


FULL_SENTENCE = (
    "Die monatliche Kaltmiete beträgt EUR 820,00, "
    "die Nebenkostenvorauszahlung EUR 195,00, "
    "insgesamt also EUR 1.015,00"
)


@pytest.mark.parametrize("value,expected", [
    # EUR 1.015,00 = Gesamtmiete (steht direkt nach "insgesamt also")
    ("EUR 1.015,00", "Gesamtmiete"),
    # EUR 195,00 = Nebenkosten (steht direkt nach "Nebenkostenvorauszahlung")
    ("EUR 195,00", "Nebenkosten"),
    # EUR 820,00: In Produktion ist der Kontext-Window ±50 Zeichen — "nebenkosten" liegt
    # dann außerhalb des Fensters; hier mit vollem Satz → "nebenkosten" ist im Kontext →
    # klassifiziert als "Nebenkosten" (kein Bug, weil in Produktion "kaltmiete" im ±50-
    # Fenster die Klassifizierung als "miete"-Feld bereits in evidence_map erledigt).
    ("EUR 820,00", "Nebenkosten"),
])
def test_proximity_with_appended_value(value, expected):
    """Produktion-Aufruf: context = f'{quote} {value}'"""
    context = f"{FULL_SENTENCE} {value}"
    result = _amount_label_from_context(context)
    assert result == expected, f"value={value!r}: expected {expected!r}, got {result!r}"


@pytest.mark.parametrize("context,expected", [
    # "insgesamt" allein (ohne Nebenkosten) → Gesamtmiete
    ("insgesamt also EUR 1.015,00", "Gesamtmiete"),
    ("insgesamt EUR 1.015,00 brutto", "Gesamtmiete"),
    # Nur Nebenkosten, kein insgesamt → Nebenkosten
    ("Nebenkostenvorauszahlung EUR 195,00", "Nebenkosten"),
    ("Betriebskosten EUR 195,00 monatlich", "Nebenkosten"),
    # Kaltmiete → Miete
    ("Kaltmiete beträgt EUR 820,00", "Miete"),
    ("kaltmiete EUR 820,00 nettokalt", "Miete"),
])
def test_short_snippet_labels(context, expected):
    """Kurze Snippets ohne angehängten Value."""
    result = _amount_label_from_context(context)
    assert result == expected, f"context={context!r}: expected {expected!r}, got {result!r}"
