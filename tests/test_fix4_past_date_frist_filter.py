"""
tests/test_fix4_past_date_frist_filter.py

FIX 4: Vergangene Daten ohne echten Frist-Kontext dürfen nicht als
fristen_termine klassifiziert werden.
"""
import unittest

import pytest

from core.evidence_map import _is_past_date_no_strong_frist_context


@pytest.mark.parametrize("date_value,context,expected_filtered", [
    # Kein Frist-Kontext → soll gefiltert werden
    ("04.03.2011", "Klavier bis zum 04.03.2011 Mein letzter Wille", True),
    ("15.07.2015", "wurde am 15.07.2015 unterzeichnet und gehört bis heute", True),
    # Starker Frist-Kontext → nicht filtern (historische Frist bleibt relevant)
    ("01.01.2024", "Frist lief ab am 01.01.2024 und wurde versäumt", False),
    ("15.03.2023", "spätestens bis zum 15.03.2023 zu zahlen", False),
    ("01.07.2022", "Zahlungsfrist: 01.07.2022", False),
    ("31.12.2020", "Termin für Einreichung war der 31.12.2020", False),
    # Zahlungs-Kontext → nicht filtern (auch wenn Datum in der Vergangenheit)
    ("01.06.2025", "bis 01.06.2025 zu zahlen", False),
    ("15.01.2026", "Fällig bis 15.01.2026", False),
    # Kein echter Frist-Kontext, Datum liegt in der Vergangenheit → filtern
    ("15.01.2026", "nur 'bis' ohne Kontext bis 15.01.2026", True),
])
def test_is_past_date_no_strong_frist_context(date_value, context, expected_filtered):
    result = _is_past_date_no_strong_frist_context(date_value, context)
    assert result == expected_filtered, (
        f"date={date_value!r}, context={context!r}: "
        f"expected filtered={expected_filtered}, got {result}"
    )


class BuildEvidenceMapFristFilterTests(unittest.TestCase):
    def test_past_date_without_frist_context_not_in_fristen(self):
        """04.03.2011 aus Testament-Kontext erscheint nicht als fristen_termine."""
        from core.evidence_map import build_evidence_map

        pages = [
            {
                "page_number": 10,
                "text": (
                    "Mein letzter Wille. Ich vermache meinen Kindern bis "
                    "zur Vollendung ihres 25. Lebensjahres das Klavier. "
                    "Unterzeichnet am 04.03.2011 in München."
                ),
            }
        ]
        ev_map = build_evidence_map(pages)
        fristen = [e for e in ev_map if e.get("field") == "fristen_termine" and e.get("value") != "missing"]
        fristen_values = [e["value"] for e in fristen]
        self.assertNotIn("04.03.2011", fristen_values, f"Frist-Values: {fristen_values}")

    def test_past_expired_frist_with_context_is_kept(self):
        """'Frist lief am 01.01.2024 ab' bleibt als fristen_termine."""
        from core.evidence_map import build_evidence_map

        pages = [
            {
                "page_number": 5,
                "text": "Die Zahlungsfrist lief ab am 01.01.2024 und wurde nicht eingehalten.",
            }
        ]
        ev_map = build_evidence_map(pages)
        fristen = [e for e in ev_map if e.get("field") == "fristen_termine" and e.get("value") != "missing"]
        fristen_values = [e["value"] for e in fristen]
        self.assertIn("01.01.2024", fristen_values, f"Frist-Values: {fristen_values}")
