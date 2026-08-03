"""
tests/test_fix2_fix6_birth_employment_not_frist.py

FIX 2: Geburtsdaten sind keine Fristen (Kontext-Check).
FIX 6: Beschäftigungsbeginn-Daten sind keine Fristen.

Sicherheitsnetz: Abgelaufene echte Fristen bleiben erhalten.
"""

import unittest

from core.evidence_map import _is_past_date_no_strong_frist_context


class TestBirthDateNotFrist(unittest.TestCase):
    """FIX 2: Geburtsdaten werden gefiltert."""

    def test_geboren_keyword_filters_date(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("22.09.1968", "Dr. Ingrid Pfeiffer geboren am 22.09.1968 ist Gesellschafterin")
        )

    def test_geb_abbreviation_filters_date(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("14.03.1982", "geb. 14.03.1982 wohnhaft in Hamburg")
        )

    def test_geburtsdatum_keyword_filters_date(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("22.09.1968", "Geburtsdatum: 22.09.1968")
        )

    def test_geburtstag_keyword_filters_date(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("01.04.1975", "Geburtstag 01.04.1975")
        )

    def test_asterisk_genealogy_filters_date(self):
        """Genealogischer Asterisk (* Datum) ist ein Geburtsdatum."""
        self.assertTrue(
            _is_past_date_no_strong_frist_context("22.09.1968", "* 22.09.1968 in München")
        )

    def test_birth_date_post_1960_filtered(self):
        """1968 > 1960 — alter Cutoff greift nicht, aber Kontext-Check greift."""
        self.assertTrue(
            _is_past_date_no_strong_frist_context("22.09.1968", "geboren am 22.09.1968")
        )

    def test_birth_date_pre_1960_filtered_anyway(self):
        """Vor-1960-Cutoff greift auch ohne geboren-Keyword."""
        self.assertTrue(
            _is_past_date_no_strong_frist_context("15.03.1955", "Vertragspartner 15.03.1955")
        )


class TestEmploymentStartNotFrist(unittest.TestCase):
    """FIX 6: Beschäftigungsbeginn-Daten werden gefiltert."""

    def test_beschaeftigt_seit_filters_date(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("01.04.2019", "seit dem 01. April 2019 beschäftigt")
        )

    def test_eingestellt_am_filters_date(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("01.04.2019", "eingestellt am 01.04.2019 als Vertriebsleiter")
        )

    def test_eintritt_filters_date(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("01.04.2019", "Eintritt 01.04.2019 gemäß Arbeitsvertrag")
        )

    def test_beschaeftigungsbeginn_filters_date(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("01.04.2019", "Beschäftigungsbeginn: 01.04.2019")
        )

    def test_taetig_seit_filters_date(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("01.04.2019", "tätig seit 01.04.2019 im Unternehmen")
        )

    def test_arbeitsverhaeltnis_seit_filters_date(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("01.04.2019", "Arbeitsverhältnis seit 01.04.2019 besteht")
        )


class TestFoundingDateNotFrist(unittest.TestCase):
    """FIX 2: Gründungsdaten werden gefiltert."""

    def test_gegruendet_filters_date(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("15.03.2018", "gegründet am 15.03.2018 in München")
        )

    def test_eintragung_filters_date(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("15.03.2018", "Eintragung 15.03.2018 Handelsregister")
        )

    def test_registriert_filters_date(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("15.03.2018", "registriert am 15.03.2018")
        )


class TestExpiredFristKept(unittest.TestCase):
    """Abgelaufene echte Fristen bleiben erhalten."""

    def test_expired_frist_with_keyword_kept(self):
        self.assertFalse(
            _is_past_date_no_strong_frist_context("15.03.1995", "Frist bis 15.03.1995 abgelaufen")
        )

    def test_frist_lief_ab_kept(self):
        self.assertFalse(
            _is_past_date_no_strong_frist_context("01.01.1999", "Frist lief ab am 01.01.1999")
        )

    def test_kuendigungsfrist_2019_kept(self):
        """Kündigungsfrist endet am 01.04.2019 — ist eine echte Frist."""
        self.assertFalse(
            _is_past_date_no_strong_frist_context("01.04.2019", "Kündigungsfrist endet am 01.04.2019 gemäß Vertrag")
        )

    def test_future_date_never_filtered(self):
        """Zukunftsdaten werden nie gefiltert."""
        self.assertFalse(
            _is_past_date_no_strong_frist_context("01.01.2050", "geboren am 01.01.2050")
        )


if __name__ == "__main__":
    unittest.main()
