"""
tests/test_pflichtfelder_extraktion.py – Pflichtfelder Aktenzeichen, Gericht,
Streitwert, Beweismittel (feature/pflichtfelder-extraktion)

Testet:
1. AZ-Regex erkennt "Az. 123 C 456/25", "Aktenzeichen:", "Geschäftszeichen:"
2. Gericht-Regex erkennt "Amtsgericht Frankfurt", "Landgericht Berlin", "OLG München"
3. Streitwert läuft durch _REQUIRED_EVIDENCE_FIELDS (war zuvor silent drop)
4. Beweismittel aus LLM-Output werden in ECKDATEN synthetisiert (Label + Render-Pfad)
5. Felder die nicht im Dokument vorkommen → kein Eintrag (kein "nicht genannt")
"""

import unittest

from core.evidence_map import build_evidence_map
from core.labels import _SUMMARY_LABELS
from core.export_render import render_segmented_summary_text, _collect_evidence_values


def _make_page(text: str, page_number: int = 1) -> dict:
    return {"page_number": page_number, "text": text}


class AktenzeichenRegexTests(unittest.TestCase):
    """AZ-Regex extrahiert Aktenzeichen korrekt aus Seitentext."""

    def _get_az_values(self, text: str) -> list[str]:
        pages = [_make_page(text)]
        ev = build_evidence_map(pages)
        return [item["value"] for item in ev if item.get("field") == "aktenzeichen" and item.get("value") != "missing"]

    def test_az_punkt_format(self):
        """Az. 123 C 456/25 → extrahiert als Aktenzeichen."""
        values = self._get_az_values("Az. 4 O 321/24")
        self.assertTrue(any("4 O 321/24" in v for v in values), f"Kein AZ-Treffer: {values}")

    def test_az_doppelpunkt_format(self):
        """Az.: 4 O 321/24 → extrahiert."""
        values = self._get_az_values("Az.: 4 O 321/24")
        self.assertTrue(any("4 O 321/24" in v for v in values), f"Kein AZ-Treffer: {values}")

    def test_aktenzeichen_label(self):
        """'Aktenzeichen: 123 C 456/25' → extrahiert."""
        values = self._get_az_values("Aktenzeichen: 123 C 456/25")
        self.assertTrue(any("123 C 456/25" in v for v in values), f"Kein AZ-Treffer: {values}")

    def test_geschaeftszeichen_label(self):
        """'Geschäftszeichen: ABC-2025-001' → extrahiert."""
        values = self._get_az_values("Geschäftszeichen: ABC-2025-001")
        self.assertTrue(any("ABC-2025-001" in v for v in values), f"Kein GZ-Treffer: {values}")

    def test_unser_zeichen_label(self):
        """'Unser Zeichen: KZ-2026-007' → extrahiert."""
        values = self._get_az_values("Unser Zeichen: KZ-2026-007")
        self.assertTrue(any("KZ-2026-007" in v for v in values), f"Kein UZ-Treffer: {values}")

    def test_kein_az_bei_fehlendem_marker(self):
        """Zeile ohne AZ-Marker → kein Aktenzeichen-Item."""
        values = self._get_az_values("Der Vertrag wurde am 01.01.2026 geschlossen.")
        self.assertEqual(values, [], f"Unerwarteter AZ-Treffer: {values}")


class GerichtRegexTests(unittest.TestCase):
    """Gericht-Regex extrahiert Gerichtsnamen korrekt."""

    def _get_gericht_values(self, text: str) -> list[str]:
        pages = [_make_page(text)]
        ev = build_evidence_map(pages)
        return [item["value"] for item in ev if item.get("field") == "gericht" and item.get("value") != "missing"]

    def test_amtsgericht_mit_ort(self):
        """'Amtsgericht Frankfurt' → extrahiert."""
        values = self._get_gericht_values("Klageschrift beim Amtsgericht Frankfurt eingereicht.")
        self.assertTrue(any("Amtsgericht" in v and "Frankfurt" in v for v in values), f"Kein Gericht-Treffer: {values}")

    def test_landgericht_mit_ort(self):
        """'Landgericht Berlin' → extrahiert."""
        values = self._get_gericht_values("Zuständig ist das Landgericht Berlin.")
        self.assertTrue(any("Landgericht" in v and "Berlin" in v for v in values), f"Kein Gericht-Treffer: {values}")

    def test_olg_mit_ort(self):
        """'OLG München' → extrahiert."""
        values = self._get_gericht_values("Berufung vor dem OLG München.")
        self.assertTrue(any("OLG" in v and "München" in v for v in values), f"Kein Gericht-Treffer: {values}")

    def test_bgh_ohne_ort(self):
        """'BGH' allein → extrahiert."""
        values = self._get_gericht_values("Die Revision wurde beim BGH eingelegt.")
        self.assertTrue(any("BGH" in v for v in values), f"Kein Gericht-Treffer: {values}")

    def test_kein_gericht_bei_normaler_zeile(self):
        """Zeile ohne Gerichtsname → kein Gericht-Item."""
        values = self._get_gericht_values("Der Mieter schuldet EUR 1.200,00 Rückstand.")
        self.assertEqual(values, [], f"Unerwarteter Gericht-Treffer: {values}")


class StreitwertRequiredFieldsTest(unittest.TestCase):
    """Streitwert ist in _REQUIRED_EVIDENCE_FIELDS → wird nicht silent-dropped."""

    def test_streitwert_in_evidence_map(self):
        """Streitwert-Betrag mit Keyword im Text → field='streitwert' im Output."""
        pages = [_make_page("Streitwert: EUR 12.600,00")]
        ev = build_evidence_map(pages)
        fields = [item.get("field") for item in ev]
        self.assertIn("streitwert", fields, f"streitwert fehlt in evidence_map fields: {fields}")

    def test_streitwert_value_not_missing(self):
        """Extrahierter Streitwert hat value != 'missing'."""
        pages = [_make_page("Der Gegenstandswert beträgt EUR 5.000,00.")]
        ev = build_evidence_map(pages)
        streitwert_items = [it for it in ev if it.get("field") == "streitwert" and it.get("value") != "missing"]
        self.assertTrue(len(streitwert_items) > 0, "Kein Streitwert-Item mit echtem Wert")


class BeweismittelLabelTest(unittest.TestCase):
    """Beweismittel-Label in _SUMMARY_LABELS und Render-Pfad funktioniert."""

    def test_beweismittel_label_in_summary_labels(self):
        self.assertIn("beweismittel", _SUMMARY_LABELS)
        self.assertEqual(_SUMMARY_LABELS["beweismittel"], "Beweismittel")

    def test_aktenzeichen_label_in_summary_labels(self):
        self.assertIn("aktenzeichen", _SUMMARY_LABELS)
        self.assertEqual(_SUMMARY_LABELS["aktenzeichen"], "Aktenzeichen")

    def test_gericht_label_in_summary_labels(self):
        self.assertIn("gericht", _SUMMARY_LABELS)
        self.assertEqual(_SUMMARY_LABELS["gericht"], "Gericht")

    def test_beweismittel_in_eckdaten_output(self):
        """Wenn LLM beweismittel liefert → erscheint in ECKDATEN der Zusammenfassung."""
        segment_result = {
            "segment_meta": {
                "title_guess": "Klageschrift",
                "page_start": 1,
                "page_end": 3,
            },
            "analysis": {
                "dokumenttyp_parteien": "Dokumenttyp: Klageschrift\nKlägerin: [PARTEI_A]\nBeklagter: [PARTEI_B]",
                "kurzueberblick": "Klageschrift vor Amtsgericht Frankfurt.",
                "kernaussagen": [
                    {"text": "Klägerin fordert EUR 12.600,00.", "citations": [{"page_number": 1, "quote_snippet": "fordert EUR 12.600,00"}]}
                ],
                "beweismittel": [
                    {
                        "text": "Sachverständigengutachten des Dipl.-Ing. Werner Knauß",
                        "citations": [{"page_number": 2, "quote_snippet": "Beweis: Sachverständigengutachten"}],
                    }
                ],
                "auffaelligkeiten": [],
                "prueffragen": [],
                "todos": [],
                "missing_info": [],
            },
            "evidence_map": [],
        }
        result = render_segmented_summary_text([segment_result])
        self.assertIn("Beweismittel", result, "Label 'Beweismittel' fehlt im Output")
        self.assertIn("Sachverständigengutachten", result, "Beweismittel-Wert fehlt im Output")

    def test_beweismittel_leer_kein_eintrag(self):
        """Wenn LLM kein beweismittel liefert → kein 'Beweismittel'-Eintrag in ECKDATEN."""
        segment_result = {
            "segment_meta": {
                "title_guess": "Mietvertrag",
                "page_start": 1,
                "page_end": 2,
            },
            "analysis": {
                "dokumenttyp_parteien": "Dokumenttyp: Mietvertrag\nKlägerin: [PARTEI_A]\nBeklagter: [PARTEI_B]",
                "kurzueberblick": "Mietvertrag.",
                "kernaussagen": [
                    {"text": "Miete beträgt EUR 900,00.", "citations": [{"page_number": 1, "quote_snippet": "Miete EUR 900,00"}]}
                ],
                "beweismittel": [],
                "auffaelligkeiten": [],
                "prueffragen": [],
                "todos": [],
                "missing_info": [],
            },
            "evidence_map": [],
        }
        result = render_segmented_summary_text([segment_result])
        self.assertNotIn("Beweismittel:", result, "Beweismittel-Eintrag obwohl leer")

    def test_collect_evidence_values_includes_beweismittel(self):
        """_collect_evidence_values gibt beweismittel-Items zurück wenn in der Map."""
        evidence_map = [
            {"field": "beweismittel", "value": "Zeuge Thomas Müller", "page": 3, "quote": "Zeuge: Thomas Müller"},
            {"field": "betraege", "value": "EUR 1.000,00", "page": 1, "quote": "EUR 1.000,00"},
        ]
        result = _collect_evidence_values(
            evidence_map,
            ("betraege", "beweismittel"),
            max_items=20,
        )
        fields = [it["field"] for it in result]
        self.assertIn("beweismittel", fields)
        self.assertIn("betraege", fields)
