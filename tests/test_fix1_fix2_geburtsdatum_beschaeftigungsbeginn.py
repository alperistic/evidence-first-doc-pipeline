"""
tests/test_fix1_fix2_geburtsdatum_beschaeftigungsbeginn.py

FIX 1: Geburtsdatum darf NICHT als Frist im Output erscheinen.
FIX 2: Beschäftigungsbeginn / Vertragsbeginn-Daten dürfen NICHT als Frist erscheinen.
"""

import unittest

from core.evidence_map import _is_past_date_no_strong_frist_context, build_evidence_map


class TestGeburtsdatumFilter(unittest.TestCase):
    """FIX 1: Geburtsdaten werden aus Fristen herausgefiltert."""

    def test_geboren_dd_mm_yyyy(self):
        """geboren am 22.09.1968 → filter."""
        self.assertTrue(
            _is_past_date_no_strong_frist_context("22.09.1968", "Ingrid Pfeiffer, geboren am 22.09.1968")
        )

    def test_geboren_long_format(self):
        """geboren am 22. September 1968 → filter (nach Normalisierung)."""
        self.assertTrue(
            _is_past_date_no_strong_frist_context("22.09.1968", "geboren am 22. September 1968")
        )

    def test_geb_abbreviation(self):
        """geb. 14.03.1982 → filter."""
        self.assertTrue(
            _is_past_date_no_strong_frist_context("14.03.1982", "geb. 14.03.1982 in Berlin")
        )

    def test_future_frist_not_filtered(self):
        """Frist bis 22.09.2026 → bleibt als Frist."""
        self.assertFalse(
            _is_past_date_no_strong_frist_context("22.09.2026", "Frist bis 22.09.2026")
        )

    def test_past_frist_with_strong_trigger_kept(self):
        """Zahlungsfrist 15.01.2023 bleibt (starker Frist-Kontext)."""
        self.assertFalse(
            _is_past_date_no_strong_frist_context("15.01.2023", "Zahlungsfrist 15.01.2023, fällig bis")
        )

    def test_no_geboren_in_fristen_output(self):
        """Segment mit 'geboren am 22.09.1968' darf kein fristen_termine-Item erzeugen."""
        pages = [{"page_number": 1, "text": "Termin: Ingrid Pfeiffer, geboren am 22.09.1968"}]
        ev = build_evidence_map(pages)
        fristen = [e for e in ev if e.get("field") == "fristen_termine" and "1968" in str(e.get("value", ""))]
        self.assertEqual(fristen, [], f"Geburtsdatum darf keine Frist sein: {fristen}")


class TestBeschaeftigungsbeginnFilter(unittest.TestCase):
    """FIX 2: Beschäftigungsbeginn/Vertragsbeginn-Daten werden herausgefiltert."""

    def test_arbeitsvertrag_vom(self):
        """Arbeitsvertrag vom 01. April 2019 → filter."""
        self.assertTrue(
            _is_past_date_no_strong_frist_context("01.04.2019", "Arbeitsvertrag vom 01. April 2019")
        )

    def test_beschaeftigt_seit_dem(self):
        """beschäftigt seit dem 01.04.2019 → filter."""
        self.assertTrue(
            _is_past_date_no_strong_frist_context("01.04.2019", "beschäftigt seit dem 01.04.2019")
        )

    def test_vertrag_vom_old(self):
        """Vertrag vom 15.01.2018 (vor 2024) → filter."""
        self.assertTrue(
            _is_past_date_no_strong_frist_context("15.01.2018", "Vertrag vom 15.01.2018")
        )

    def test_vertrag_vom_recent_kept(self):
        """Vertrag vom 01.03.2025 (2024+) → bleibt als potenzielle Frist."""
        self.assertFalse(
            _is_past_date_no_strong_frist_context("01.03.2025", "Vertrag vom 01.03.2025")
        )

    def test_frist_aus_vertrag_bleibt(self):
        """'Frist aus dem Vertrag vom 01.04.2019' → bleibt (expliziter Frist-Kontext)."""
        self.assertFalse(
            _is_past_date_no_strong_frist_context(
                "01.04.2019", "Frist aus dem Vertrag vom 01.04.2019 beachten"
            )
        )

    def test_eingetreten_am(self):
        """eingetreten am 01.03.2015 → filter."""
        self.assertTrue(
            _is_past_date_no_strong_frist_context("01.03.2015", "eingetreten am 01.03.2015")
        )

    def test_no_beschaeftigung_in_fristen_output(self):
        """Segment mit 'Arbeitsvertrag vom 01.04.2019' darf kein fristen_termine-Item erzeugen."""
        pages = [{"page_number": 1, "text": "Termin: Arbeitsvertrag vom 01.04.2019 unterzeichnet"}]
        ev = build_evidence_map(pages)
        fristen = [e for e in ev if e.get("field") == "fristen_termine" and "2019" in str(e.get("value", ""))]
        self.assertEqual(fristen, [], f"Beschäftigungsbeginn darf keine Frist sein: {fristen}")


if __name__ == "__main__":
    unittest.main()
