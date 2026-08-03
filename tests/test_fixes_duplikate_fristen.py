"""
test_fixes_duplikate_fristen.py

Tests für drei Fixes:
- Fix 1: _GERMAN_MONTH_DATE_RE extrahiert Long-Format-Daten als DD.MM.YYYY
- Fix 2: _collect_unclear_items dedupliziert über missing_info + auffaelligkeiten
- Fix 3: _sanitize_fristen_value akzeptiert Long-Format-Daten (Defense in Depth)
- Fix 3b: "nicht genannt" erscheint nicht neben echten Fristen
"""

import unittest

from core.evidence_map import build_evidence_map
from core.export_render import (
    _collect_unclear_items,
    _sanitize_fristen_value,
    _filter_fallback_fristen_if_real_exist,
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


if __name__ == "__main__":
    unittest.main()
