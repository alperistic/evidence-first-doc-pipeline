"""
tests/test_fix2_az_truncation.py

FIX 2: _clean_az_value muss Fließtext nach dem eigentlichen AZ abschneiden.
"""
import unittest

import pytest

from core.evidence_map import _clean_az_value


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
