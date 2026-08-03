"""
Deadline (Frist/Termin) extraction and filtering: distinguishing genuine
deadlines from dates that merely happen to look like ones — birth dates,
employment/contract start dates, founding dates, and price-adjustment
reference dates. Covers the context-based filter
(_is_past_date_no_strong_frist_context), its application inside
build_evidence_map, the LLM-path equivalent (classify_date_category /
_filter_llm_eckdaten), long-format German date extraction, and
deadline/date deduplication (2-digit vs 4-digit years, duplicate
missing-info/auffaelligkeiten items).
"""

import unittest

import pytest

from core.evidence_map import _is_past_date_no_strong_frist_context, build_evidence_map
from core.legacy_deadlines import (
    build_legacy_deadlines,
    _render_legacy_deadline_text,
    classify_date_category,
)
from core.labels import _dedupe_summary_items, _normalize_date_value_for_dedup
from core.export_render import (
    _filter_llm_eckdaten,
    _collect_unclear_items,
    _sanitize_fristen_value,
    _filter_fallback_fristen_if_real_exist,
)


class TestGeburtsdatumFilter(unittest.TestCase):
    """Geburtsdatum darf NICHT als Frist im Output erscheinen."""

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
    """Beschäftigungsbeginn/Vertragsbeginn-Daten werden herausgefiltert."""

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


def _item(text: str, label: str = "Frist/Termin") -> dict:
    return {"text": text, "label": label, "citations": []}


class TestWeiterbeschaeftigungFilter(unittest.TestCase):
    """Weiterbeschäftigung / Beschäftigungsbeginn darf NICHT als Frist/Termin
    im LLM-Pfad erscheinen (build_legacy_deadlines / _render_legacy_deadline_text).

    Root cause was a year-filter of < 2000, letting 2019 through.
    Fix: year < 2024 + _NON_FRIST_ITEM_KEYWORDS short-circuit.
    """

    def test_weiterbeschaeftigung_2019_filtered(self):
        """'Weiterbeschäftigung … 01.04.2019' → NOT in output."""
        items = [_item("Weiterbeschäftigung an einem anderen Arbeitsplatz ist uns 01.04.2019")]
        result = build_legacy_deadlines(items)
        self.assertEqual(result, [], f"Employment date must be filtered. Got: {result}")

    def test_beschaeftigt_seit_2019_filtered(self):
        """'seit dem 01. April 2019 beschäftigt' → NOT in output."""
        items = [_item("seit dem 01. April 2019 beschäftigt")]
        result = build_legacy_deadlines(items)
        self.assertEqual(result, [], f"Employment date must be filtered. Got: {result}")

    def test_year_2019_no_context_filtered(self):
        """Year 2019 without deadline context → filtered (< 2024 rule)."""
        items = [_item("Übergabe der Unterlagen am 15.06.2019")]
        result = build_legacy_deadlines(items)
        self.assertEqual(result, [], f"Past reference date must be filtered. Got: {result}")

    def test_zahlungsfrist_2019_kept(self):
        """'Zahlungsfrist 15.03.2019 fällig' → kept (has deadline context)."""
        items = [_item("Zahlungsfrist 15.03.2019 fällig", label="Zahlungsfrist")]
        result = build_legacy_deadlines(items)
        self.assertNotEqual(result, [], f"Zahlungsfrist with context must be kept. Got: {result}")

    def test_future_frist_kept(self):
        """'Frist bis 01.04.2025' → kept."""
        items = [_item("Frist bis 01.04.2025")]
        result = build_legacy_deadlines(items)
        self.assertNotEqual(result, [], f"Future Frist must be kept. Got: {result}")

    def test_render_weiterbeschaeftigung_returns_empty(self):
        """_render_legacy_deadline_text returns '' for Weiterbeschäftigung item."""
        item = _item("Weiterbeschäftigung an einem anderen Arbeitsplatz ist uns 01.04.2019")
        self.assertEqual(_render_legacy_deadline_text(item), "")

    def test_render_weiterbeschaeftigung_ascii_returns_empty(self):
        """ASCII variant 'Weiterbeschaeftigung' also filtered."""
        item = _item("Weiterbeschaeftigung an einem anderen Arbeitsplatz 01.04.2019")
        self.assertEqual(_render_legacy_deadline_text(item), "")


class TestBirthDateNotFrist(unittest.TestCase):
    """Geburtsdaten werden gefiltert (Kontext-Check, breiter Keyword-Satz)."""

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
    """Beschäftigungsbeginn-Daten werden gefiltert."""

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
    """Gründungsdaten werden gefiltert."""

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


class TestNormalizeDateValueForDedup(unittest.TestCase):
    """Kündigungsfrist-Dedupe — "15.12.24" und "15.12.2024" dürfen nicht als
    separate Einträge erscheinen."""

    def test_2digit_year_expanded(self):
        self.assertEqual(_normalize_date_value_for_dedup("15.12.24"), "15.12.2024")

    def test_4digit_year_unchanged(self):
        self.assertEqual(_normalize_date_value_for_dedup("15.12.2024"), "15.12.2024")

    def test_year_70_boundary(self):
        """Year 70 → 1970, year 69 → 2069."""
        self.assertEqual(_normalize_date_value_for_dedup("01.01.70"), "01.01.1970")
        self.assertEqual(_normalize_date_value_for_dedup("01.01.69"), "01.01.2069")

    def test_non_date_string_unchanged(self):
        self.assertEqual(_normalize_date_value_for_dedup("EUR 1.250,00"), "EUR 1.250,00")


class TestDedupeSummaryItemsDateNormalization(unittest.TestCase):
    """_dedupe_summary_items treats 2- and 4-digit year variants as duplicates."""

    def test_15_12_24_and_15_12_2024_deduped(self):
        items = [
            {"field": "fristen_termine", "value": "15.12.24", "page": "3"},
            {"field": "fristen_termine", "value": "15.12.2024", "page": "3"},
        ]
        result = _dedupe_summary_items(items)
        self.assertEqual(len(result), 1)
        # First item wins
        self.assertEqual(result[0]["value"], "15.12.24")

    def test_different_dates_not_deduped(self):
        items = [
            {"field": "fristen_termine", "value": "15.12.2024", "page": "3"},
            {"field": "fristen_termine", "value": "31.12.2024", "page": "3"},
        ]
        result = _dedupe_summary_items(items)
        self.assertEqual(len(result), 2)

    def test_same_date_different_page_not_deduped(self):
        items = [
            {"field": "fristen_termine", "value": "15.12.2024", "page": "3"},
            {"field": "fristen_termine", "value": "15.12.2024", "page": "5"},
        ]
        result = _dedupe_summary_items(items)
        self.assertEqual(len(result), 2)

    def test_amount_values_unaffected(self):
        items = [
            {"field": "betraege", "value": "EUR 1.250,00", "page": "2"},
            {"field": "betraege", "value": "EUR 1.250,00", "page": "2"},
        ]
        result = _dedupe_summary_items(items)
        self.assertEqual(len(result), 1)


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
    """Vergangene Daten ohne echten Frist-Kontext dürfen nicht als
    fristen_termine klassifiziert werden."""

    def test_past_date_without_frist_context_not_in_fristen(self):
        """04.03.2011 aus Testament-Kontext erscheint nicht als fristen_termine."""
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


def _make_page(text: str, page_no: int = 1) -> dict:
    return {"page_number": page_no, "text": text}


class TestVertragsbeginnPreisanpassungFilter(unittest.TestCase):
    """Datum aus Preisanpassungsklausel wird NICHT als Vertragsbeginn
    (datum_beginn) extrahiert."""

    def test_preisanpassung_stichtag_not_datum_beginn(self):
        """Preisanpassungs-Stichtag darf nicht als datum_beginn erscheinen."""
        pages = [_make_page(
            "Der Vertrag beginnt am 01.05.2025. "
            "Preisanpassungen sind frühestens 12 Monate nach Vertragsbeginn (Stichtag 01.05.2026) zulässig."
        )]
        ev = build_evidence_map(pages)
        beginn_values = [
            item["value"] for item in ev if item.get("field") == "datum_beginn" and item["value"] != "missing"
        ]
        # 01.05.2026 ist ein Preisanpassungs-Stichtag — darf NICHT als Beginn erscheinen
        self.assertNotIn("01.05.2026", beginn_values, f"datum_beginn: {beginn_values}")

    def test_echter_vertragsbeginn_bleibt(self):
        """Echter Vertragsbeginn wird weiterhin extrahiert."""
        pages = [_make_page(
            "Der Mietvertrag beginnt am 01.05.2025 und endet regulär nach 5 Jahren."
        )]
        ev = build_evidence_map(pages)
        beginn_values = [
            item["value"] for item in ev if item.get("field") == "datum_beginn" and item["value"] != "missing"
        ]
        self.assertIn("01.05.2025", beginn_values, f"datum_beginn: {beginn_values}")

    def test_monate_nach_context_not_datum_beginn(self):
        """'X Monate nach Vertragsbeginn' mit Datum → nicht als Vertragsbeginn."""
        pages = [_make_page(
            "Eine Anpassung ist 12 Monate nach dem Vertragsbeginn am 01.06.2026 möglich."
        )]
        ev = build_evidence_map(pages)
        beginn_values = [
            item["value"] for item in ev if item.get("field") == "datum_beginn" and item["value"] != "missing"
        ]
        self.assertNotIn("01.06.2026", beginn_values, f"datum_beginn: {beginn_values}")


def _pages(page_no: int, text: str):
    return [{"page": page_no, "text": text}]


def test_date_in_preisanpassung_context_not_extracted_as_beginn():
    """Datum nach 'frühestens 12 Monate nach Vertragsbeginn' → kein datum_beginn."""
    text = (
        "Der Vertrag beginnt am 01.05.2025. "
        "Eine Preisanpassung ist frühestens 12 Monate nach Vertragsbeginn, "
        "also ab 01.05.2026, möglich."
    )
    em = build_evidence_map(_pages(1, text))
    beginn_items = [e for e in em if e.get("field") == "datum_beginn" and e.get("value") != "missing"]
    values = [e["value"] for e in beginn_items]
    assert "01.05.2026" not in values, (
        f"01.05.2026 from Preisanpassung context must not be datum_beginn, got: {values}"
    )


def test_actual_vertragsbeginn_is_extracted():
    """Echter Vertragsbeginn 'beginnt am 01.05.2025' → datum_beginn extrahiert."""
    text = (
        "Der Vertrag beginnt am 01.05.2025. "
        "Eine Preiserhöhung ist frühestens nach 12 Monaten möglich."
    )
    em = build_evidence_map(_pages(1, text))
    beginn_items = [e for e in em if e.get("field") == "datum_beginn" and e.get("value") != "missing"]
    values = [e["value"] for e in beginn_items]
    assert "01.05.2025" in values, (
        f"01.05.2025 should be extracted as datum_beginn, got: {values}"
    )


class Fix1LongFormatDateExtractionTests(unittest.TestCase):
    """_GERMAN_MONTH_DATE_RE-Loop speichert Daten als DD.MM.YYYY."""

    def _page(self, text, page_no=9):
        return {"text": text, "page": page_no}

    def test_januar_frist_wird_extrahiert(self):
        """'Empfohlene Frist zur Mängelbeseitigung bis 31. Januar 2025' → 31.01.2025."""
        pages = [self._page("Empfohlene Frist zur Mängelbeseitigung bis 31. Januar 2025.")]
        ev_map = build_evidence_map(pages)
        fristen = [e for e in ev_map if e.get("field") == "fristen_termine"]
        values = [e["value"] for e in fristen]
        self.assertIn("31.01.2025", values, f"31.01.2025 fehlt in {values}")

    def test_februar_zahlung_wird_extrahiert(self):
        """'Zahlung der Mängelbeseitigungskosten bis 14. Februar 2025' → 14.02.2025."""
        pages = [self._page("Zahlung der Mängelbeseitigungskosten bis 14. Februar 2025.")]
        ev_map = build_evidence_map(pages)
        fristen = [e for e in ev_map if e.get("field") == "fristen_termine"]
        values = [e["value"] for e in fristen]
        self.assertIn("14.02.2025", values, f"14.02.2025 fehlt in {values}")

    def test_long_format_wert_ist_numerisches_datum(self):
        """Gespeicherter Wert hat DD.MM.YYYY-Format, kein 'Januar' mehr."""
        pages = [self._page("Frist: 15. März 2026.")]
        ev_map = build_evidence_map(pages)
        fristen = [e for e in ev_map if e.get("field") == "fristen_termine"]
        for e in fristen:
            self.assertNotIn("März", e["value"], f"Monatsname darf nicht im Wert sein: {e['value']!r}")
            self.assertRegex(e["value"], r"\d{2}\.\d{2}\.\d{4}", f"Wert ist kein DD.MM.YYYY: {e['value']!r}")


class Fix2CollectUnclearItemsDedupeTests(unittest.TestCase):
    """_collect_unclear_items dedupliziert über beide Quellen."""

    def test_duplikat_in_missing_info_wird_entfernt(self):
        """Gleicher Text zweimal in missing_info → nur einmal in Ausgabe."""
        parsed = {
            "missing_info": [
                "Unklar, ob die Zahlung bis zur Frist erfolgt.",
                "Unklar, ob die Zahlung bis zur Frist erfolgt.",
            ],
            "auffaelligkeiten": [],
        }
        result = _collect_unclear_items(parsed, max_items=10)
        self.assertEqual(result.count("Unklar, ob die Zahlung bis zur Frist erfolgt."), 1)

    def test_duplikat_across_sources_wird_entfernt(self):
        """Gleicher Text in missing_info UND auffaelligkeiten → nur einmal."""
        text = "Unklar, ob die Mietminderung zu prüfen ist."
        parsed = {
            "missing_info": [text],
            "auffaelligkeiten": [{"text": text, "citations": []}],
        }
        result = _collect_unclear_items(parsed, max_items=10)
        self.assertEqual(result.count(text), 1, f"Duplikat über Quellen: {result}")

    def test_verschiedene_texte_bleiben_erhalten(self):
        """Verschiedene Texte werden nicht fälschlich dedupliziert."""
        parsed = {
            "missing_info": ["Unklar A.", "Unklar B."],
            "auffaelligkeiten": [{"text": "Unklar C.", "citations": []}],
        }
        result = _collect_unclear_items(parsed, max_items=10)
        self.assertEqual(len(result), 3)

    def test_case_insensitive_dedupe(self):
        """Groß-/Kleinschreibung zählt nicht beim Deduplizieren."""
        parsed = {
            "missing_info": ["Unklar, ob zahlung erfolgt."],
            "auffaelligkeiten": [{"text": "unklar, ob Zahlung erfolgt.", "citations": []}],
        }
        result = _collect_unclear_items(parsed, max_items=10)
        self.assertEqual(len(result), 1, f"Case-insensitive Dedupe fehlt: {result}")


class Fix3SanitizeFristenValueTests(unittest.TestCase):
    """_sanitize_fristen_value akzeptiert Long-Format-Daten (Defense in Depth)."""

    def test_long_format_datum_wird_akzeptiert(self):
        """'31. Januar 2025' → nicht zu 'nicht genannt' konvertiert."""
        result = _sanitize_fristen_value("fristen_termine", "31. Januar 2025")
        self.assertEqual(result, "31. Januar 2025")

    def test_numerisches_datum_bleibt_unveraendert(self):
        """'15.03.2025' → unverändert."""
        result = _sanitize_fristen_value("fristen_termine", "15.03.2025")
        self.assertEqual(result, "15.03.2025")

    def test_rohtext_wird_fallback(self):
        """'2 Jahre' → 'nicht genannt (Rückfrage erforderlich)'."""
        result = _sanitize_fristen_value("fristen_termine", "2 Jahre")
        self.assertEqual(result, "nicht genannt (Rückfrage erforderlich)")

    def test_missing_wird_fallback(self):
        """'missing' → Fallback."""
        result = _sanitize_fristen_value("fristen_termine", "missing")
        self.assertEqual(result, "nicht genannt (Rückfrage erforderlich)")


class Fix3bFilterFallbackFristenTests(unittest.TestCase):
    """'nicht genannt' wird unterdrückt wenn echte Fristen existieren."""

    def test_fallback_bleibt_wenn_keine_echten_fristen(self):
        """Wenn keine validen Fristen → Fallback-Item bleibt."""
        items = [
            {"field": "fristen_termine", "value": "missing", "page": None},
            {"field": "betraege", "value": "1.200 €", "page": 1},
        ]
        result = _filter_fallback_fristen_if_real_exist(items)
        fristen = [e for e in result if e.get("field") == "fristen_termine"]
        self.assertEqual(len(fristen), 1)

    def test_fallback_wird_entfernt_wenn_echte_fristen_da(self):
        """Wenn valide Fristen vorhanden → Fallback-Item wird entfernt."""
        items = [
            {"field": "fristen_termine", "value": "31.01.2025", "page": 9},
            {"field": "fristen_termine", "value": "missing", "page": None},
            {"field": "betraege", "value": "1.200 €", "page": 1},
        ]
        result = _filter_fallback_fristen_if_real_exist(items)
        fristen_values = [e["value"] for e in result if e.get("field") == "fristen_termine"]
        self.assertIn("31.01.2025", fristen_values)
        self.assertNotIn("missing", fristen_values, "Fallback darf nicht neben echten Fristen erscheinen")

    def test_long_format_als_echte_frist_erkannt(self):
        """Long-Format-Datum ('31. Januar 2025') gilt als echte Frist für den Filter."""
        items = [
            {"field": "fristen_termine", "value": "31. Januar 2025", "page": 9},
            {"field": "fristen_termine", "value": "missing", "page": None},
        ]
        result = _filter_fallback_fristen_if_real_exist(items)
        fristen_values = [e["value"] for e in result if e.get("field") == "fristen_termine"]
        self.assertIn("31. Januar 2025", fristen_values)
        self.assertNotIn("missing", fristen_values)

    def test_nicht_fristen_felder_bleiben_unveraendert(self):
        """Beträge und andere Felder werden nicht angefasst."""
        items = [
            {"field": "betraege", "value": "500 €", "page": 1},
            {"field": "fristen_termine", "value": "31.01.2025", "page": 2},
        ]
        result = _filter_fallback_fristen_if_real_exist(items)
        self.assertEqual(len(result), 2)
        self.assertIn({"field": "betraege", "value": "500 €", "page": 1}, result)


class Fix4LongFormatWindowQuoteTests(unittest.TestCase):
    """Long-Format-Datum: direkt Kontextfenster als Quote, kein sentence-split Bug."""

    def _page(self, text, page_no=9):
        return {"text": text, "page": page_no}

    def test_februar_wird_nicht_gedroppt_neben_numerischem_datum(self):
        """'14. Februar 2025' bleibt als 14.02.2025 erhalten, auch wenn '31.01.2025' auf derselben Seite steht."""
        text = (
            "Zahlung der Mängelbeseitigungskosten bis 31.01.2025. "
            "Frist zur Schadensbehebung bis 14. Februar 2025 muss eingehalten werden."
        )
        pages = [self._page(text)]
        ev_map = build_evidence_map(pages)
        fristen = [e for e in ev_map if e.get("field") == "fristen_termine"]
        values = [e["value"] for e in fristen]
        self.assertIn("14.02.2025", values, f"14.02.2025 wurde gedroppt; gefunden: {values}")

    def test_long_format_quote_enthaelt_datumstext(self):
        """Quote für Long-Format-Datum enthält den ausgeschriebenen Monatsnamen."""
        pages = [self._page("Zahlungsfrist bis 14. Februar 2025 gemäß Vereinbarung.")]
        ev_map = build_evidence_map(pages)
        fristen = [e for e in ev_map if e.get("field") == "fristen_termine" and e.get("value") == "14.02.2025"]
        self.assertTrue(fristen, "Kein 14.02.2025-Entry gefunden")
        quote = fristen[0].get("quote", "")
        self.assertIn("Februar", quote, f"Monatsname fehlt in Quote: {quote!r}")

    def test_leading_artifact_strippet_nicht_monatsnamen(self):
        """_LEADING_ARTIFACT_RE darf '14. Februar' nicht als Artefakt strippen."""
        from core.models import clean_snippet
        result = clean_snippet("14. Februar 2025 ist die Zahlungsfrist.")
        self.assertIn("14", result, f"'14.' wurde fälschlich gestripped: {result!r}")
        self.assertIn("Februar", result, f"'Februar' fehlt nach strip: {result!r}")

    def test_leading_artifact_strippet_weiter_seitenzahlen(self):
        """_LEADING_ARTIFACT_RE strippt weiterhin echte Artefakte wie 'Seite 3:'."""
        from core.models import clean_snippet
        result = clean_snippet("Seite 3: Die Kündigung muss bis zum 01.03.2025 erfolgen.")
        self.assertNotIn("Seite 3:", result, f"'Seite 3:' sollte gestripped sein: {result!r}")
        result2 = clean_snippet("14: Nächster Punkt im Vertrag.")
        self.assertNotIn("14:", result2, f"'14:' (Listenpunkt) sollte gestripped sein: {result2!r}")


class TestClassifyDateCategoryNonFrist(unittest.TestCase):
    """classify_date_category klassifiziert Geburts-/Beschäftigungsdaten als Referenzdatum."""

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
    """_filter_llm_eckdaten entfernt Geburts-/Beschäftigungsdaten."""

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
    """'seit dem' und 'angestellt seit' als Negativ-Keywords."""

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
