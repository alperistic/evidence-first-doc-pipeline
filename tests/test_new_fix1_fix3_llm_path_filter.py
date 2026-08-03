"""
tests/test_new_fix1_fix3_llm_path_filter.py

FIX 1: Geburtsdaten werden im LLM-Pfad (classify_date_category + _filter_llm_eckdaten) gefiltert.
FIX 3: Beschäftigungsbeginn-Daten werden im LLM-Pfad gefiltert.
"""

import unittest

from core.legacy_deadlines import classify_date_category
from core.export_render import _filter_llm_eckdaten
from core.evidence_map import _is_past_date_no_strong_frist_context


class TestClassifyDateCategoryNonFrist(unittest.TestCase):
    """FIX 1/3: classify_date_category klassifiziert Geburts-/Beschäftigungsdaten als Referenzdatum."""

    def test_geboren_classified_as_referenzdatum(self):
        self.assertEqual(
            classify_date_category("geboren am 22.09.1968", ""),
            "Referenzdatum",
        )

    def test_geburtsdatum_label_classified_as_referenzdatum(self):
        self.assertEqual(
            classify_date_category("22.09.1968", "Geburtsdatum"),
            "Referenzdatum",
        )

    def test_beschaeftigungsbeginn_classified_as_referenzdatum(self):
        self.assertEqual(
            classify_date_category("Arbeitsverhältnis seit 01.04.2019", ""),
            "Referenzdatum",
        )

    def test_eingestellt_am_classified_as_referenzdatum(self):
        self.assertEqual(
            classify_date_category("eingestellt am 01.04.2019 als Vertriebsleiter", ""),
            "Referenzdatum",
        )

    def test_seit_dem_classified_as_referenzdatum(self):
        self.assertEqual(
            classify_date_category("seit dem 01. April 2019 beschäftigt", ""),
            "Referenzdatum",
        )

    def test_gegruendet_classified_as_referenzdatum(self):
        self.assertEqual(
            classify_date_category("gegründet am 15.03.2018", ""),
            "Referenzdatum",
        )

    def test_zahlungsfrist_stays_frist(self):
        self.assertEqual(
            classify_date_category("Zahlung bis 17.06.2026 fällig", ""),
            "Frist",
        )

    def test_kuendigungsfrist_stays_frist(self):
        self.assertEqual(
            classify_date_category("Kündigungsfrist endet am 01.04.2026", ""),
            "Frist",
        )


class TestFilterLlmEckdaten(unittest.TestCase):
    """FIX 1/3: _filter_llm_eckdaten entfernt Geburts-/Beschäftigungsdaten."""

    def test_geburtsdatum_label_filtered(self):
        items = [{"label": "Geburtsdatum", "wert": "22.09.1968"}]
        self.assertEqual(_filter_llm_eckdaten(items), [])

    def test_geburtstag_label_filtered(self):
        items = [{"label": "Geburtstag", "wert": "22.09.1968"}]
        self.assertEqual(_filter_llm_eckdaten(items), [])

    def test_eintrittsdatum_label_filtered(self):
        items = [{"label": "Eintrittsdatum", "wert": "01.04.2019"}]
        self.assertEqual(_filter_llm_eckdaten(items), [])

    def test_beschaeftigungsbeginn_label_filtered(self):
        items = [{"label": "Beschäftigungsbeginn", "wert": "01.04.2019"}]
        self.assertEqual(_filter_llm_eckdaten(items), [])

    def test_miete_label_stays(self):
        item = {"label": "Miete", "wert": "EUR 820,00"}
        self.assertEqual(_filter_llm_eckdaten([item]), [item])

    def test_frist_label_stays(self):
        item = {"label": "Frist/Termin", "wert": "17.06.2026"}
        self.assertEqual(_filter_llm_eckdaten([item]), [item])

    def test_mixed_filters_only_non_frist(self):
        miete = {"label": "Miete", "wert": "EUR 820,00"}
        geburt = {"label": "Geburtsdatum", "wert": "22.09.1968"}
        frist = {"label": "Frist/Termin", "wert": "17.06.2026"}
        result = _filter_llm_eckdaten([miete, geburt, frist])
        self.assertEqual(result, [miete, frist])

    def test_empty_list_stays_empty(self):
        self.assertEqual(_filter_llm_eckdaten([]), [])


class TestSeitDemKeywordInEvidenceMap(unittest.TestCase):
    """FIX 3: 'seit dem' und 'angestellt seit' als Negativ-Keywords."""

    def test_seit_dem_filters(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("01.04.2019", "seit dem 01. April 2019 beschäftigt")
        )

    def test_angestellt_seit_filters(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("01.04.2019", "angestellt seit 01.04.2019")
        )

    def test_arbeitsbeginn_filters(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("01.04.2019", "Arbeitsbeginn: 01.04.2019")
        )

    def test_in_kraft_seit_filters(self):
        self.assertTrue(
            _is_past_date_no_strong_frist_context("01.01.2020", "in Kraft seit 01.01.2020")
        )


if __name__ == "__main__":
    unittest.main()
