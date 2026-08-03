"""
Small text-sanitization helpers applied to individual extracted field values:
truncating trailing running text that leaks past the actual Aktenzeichen
(reference/case number) in _clean_az_value, and filtering out generic
prompt-template placeholder todos (e.g. an unfilled "Frist aus dem Dokument
pruefen" schema example that the LLM echoed back verbatim) via
_is_generic_todo.
"""

import pytest

from core.evidence_map import _clean_az_value
from core.export_render import _is_generic_todo


@pytest.mark.parametrize("raw,expected", [
    # Die vier kaputten Strings aus dem Bug-Report
    (
        "WL/2025-MR/0892.1 Rechtsgebiet: Mietrecht - Kündigung / Räumung Gericht: Amtsger",
        "WL/2025-MR/0892.1",
    ),
    (
        "3 F 156/25 anhängig. Der Scheidungsantrag wurde am 10. Januar 2025 von der Ehefr",
        "3 F 156/25",
    ),
    (
        "7 VI 89/25 Mein letzter Wille Ich",
        "7 VI 89/25",
    ),
    # Valides AZ ohne Fließtext – muss unverändert bleiben
    (
        "4 O 321/24",
        "4 O 321/24",
    ),
    # Klassisches Format
    (
        "ABC-2025/0001",
        "ABC-2025/0001",
    ),
    # OLG-Kürzel
    (
        "OLG 2 U 45/23 Berufungsverfahren",
        "OLG 2 U 45/23",
    ),
])
def test_clean_az_value(raw, expected):
    assert _clean_az_value(raw) == expected


@pytest.mark.parametrize("item,expected", [
    # Generische Prompt-Platzhalter → True (unterdrücken)
    ("Hinweis: Frist aus dem Dokument prüfen und ggf. notieren", True),
    ("Hinweis: Frist aus dem Dokument notieren", True),
    ("frist aus dem Dokument prüfen und ggf. notieren", True),
    # Konkrete To-dos → False (anzeigen)
    ("Hinweis: Mahnfrist bis 15.06.2025 notieren", False),
    ("Zu prüfen: Unterschriften vollständig", False),
    ("Bitte klären: Zahlungsmodalitäten mit Mandant", False),
    ("", False),
])
def test_is_generic_todo(item, expected):
    assert _is_generic_todo(item) == expected, f"item={item!r}: expected {expected}"
