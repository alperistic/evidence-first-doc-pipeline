"""
tests/test_new_fix4_mietbeginn_label.py

FIX 4: _human_label_for_evidence vergibt "Mietbeginn" nur für Miet-/Räumungsdokumente,
"Beschäftigungsbeginn" für Arbeitsrechtsdokumente, "Beginn" für alle anderen.
"""

import unittest

from core.labels import _human_label_for_evidence


class TestDatumBeginnLabel(unittest.TestCase):

    def test_mietvertrag_gives_mietbeginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.04.2025", "", "mietvertrag"),
            "Mietbeginn",
        )

    def test_gewerbemietvertrag_gives_mietbeginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.04.2025", "", "Gewerbemietvertrag"),
            "Mietbeginn",
        )

    def test_raeumungsklage_gives_mietbeginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.04.2025", "", "Räumungsklage"),
            "Mietbeginn",
        )

    def test_kuendigungsschutzmandat_gives_beschaeftigungsbeginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.04.2019", "", "Kündigungsschutzmandat"),
            "Beschäftigungsbeginn",
        )

    def test_arbeitsvertrag_gives_beschaeftigungsbeginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.04.2019", "", "arbeitsvertrag"),
            "Beschäftigungsbeginn",
        )

    def test_scheidungsfolgenvereinbarung_gives_beginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.01.2025", "", "Scheidungsfolgenvereinbarung"),
            "Beginn",
        )

    def test_generic_gives_beginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.01.2025", "", "generic"),
            "Beginn",
        )

    def test_none_doc_type_gives_beginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.01.2025", "", None),
            "Beginn",
        )

    def test_empty_doc_type_gives_beginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.01.2025", "", ""),
            "Beginn",
        )

    def test_quote_unterhalt_gives_unterhaltsbeginn_for_generic(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.01.2025", "Unterhaltszahlungen ab", "generic"),
            "Unterhaltsbeginn",
        )

    def test_doc_type_takes_precedence_over_quote(self):
        """Doc-type Miet gewinnt auch wenn Quote Arbeits-Keywords hat."""
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.04.2025", "Arbeitsbeginn", "Wohnraummietvertrag"),
            "Mietbeginn",
        )


if __name__ == "__main__":
    unittest.main()
