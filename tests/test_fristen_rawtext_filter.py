"""
test_fristen_rawtext_filter.py

Bug 1 (P0): Fristen-Feld mit Rohtext statt Datum.
Bug 2b (P0): Geburtsdaten werden nicht als Frist/Termin klassifiziert.

Ursache: _render_legacy_deadline_text gibt raw text zurück wenn kein
DD.MM.YYYY-Datum gefunden wird, oder wenn ein Geburtsjahr (< 2000) ohne
Fristkontext vorhanden ist.
"""

import unittest

from core.legacy_deadlines import build_legacy_deadlines, _render_legacy_deadline_text


class Bug1RawtextDroppedTests(unittest.TestCase):
    """Einträge ohne parsebares DD.MM.YYYY-Datum müssen weggelassen werden."""

    def _item(self, text, label="Frist/Termin", snippet=""):
        return {
            "text": text,
            "label": label,
            "snippet": snippet,
            "citations": [{"page_number": 1, "quote_snippet": snippet}] if snippet else [],
        }

    def test_raw_document_header_without_date_dropped(self):
        """Vollständiger Vertragsheader ohne Datum → kein Eintrag."""
        item = self._item(
            "GEWERBEMIETVERTRAG zwischen Stadtimmobilien Frankfurt AG, Mainzer Landstraße 50, "
            "60325 Frankfurt - nachfolgend: Vermieterin - und Bäckerei Sonnenschein GmbH & Co. KG",
        )
        result = _render_legacy_deadline_text(item)
        self.assertEqual(result, "", f"Rohtext ohne Datum muss leer sein, war: {result!r}")

    def test_raw_contract_title_without_date_dropped(self):
        """AUSSERORDENTLICHE KÜNDIGUNG - ENTWURF ohne Datum → kein Eintrag."""
        item = self._item(
            "AUSSERORDENTLICHE KÜNDIGUNG - ENTWURF (noch nicht versandt - zur anwaltlichen Prüfung) "
            "Die Stadtimmobilien Frankfurt AG kündigt hiermit da",
            label="Kündigungsfrist",
        )
        result = _render_legacy_deadline_text(item)
        self.assertEqual(result, "", f"Rohtext ohne Datum muss leer sein, war: {result!r}")

    def test_arbeitsvertrag_header_without_date_dropped(self):
        """ARBEITSVERTRAG (AUSZÜGE) header ohne Datum → kein Eintrag."""
        item = self._item(
            "ARBEITSVERTRAG (AUSZÜGE) Zwischen der Innovatech Solutions GmbH "
            "(nachfolgend: Arbeitgeber) und Thomas Bergmann (nachfolgend: Arbeitnehmer) g",
        )
        result = _render_legacy_deadline_text(item)
        self.assertEqual(result, "", f"Rohtext ohne Datum muss leer sein, war: {result!r}")

    def test_sozialauswahl_text_without_date_dropped(self):
        """Text ohne Datum (Sozialauswahl...) → kein Eintrag."""
        item = self._item(
            "Sozialauswahl nicht vereinbar. STREITWERT Der Streitwert für den "
            "Kündigungsschutzantrag beträgt gemäß § 42 Abs. 2 GKG dr",
        )
        result = _render_legacy_deadline_text(item)
        self.assertEqual(result, "", f"Rohtext ohne Datum muss leer sein, war: {result!r}")

    def test_schriftsatz_header_without_date_dropped(self):
        """Schriftsatz-Header ohne Datum → kein Eintrag."""
        item = self._item(
            "SCHRIFTSATZ DER BEKLAGTEN - KLAGEERWIDERUNG Rechtsanwälte Berger & "
            "Kollegen | Im Auftrag der Innovatech Solutions GmbH Datum: 14. Februar 2025 |",
        )
        result = _render_legacy_deadline_text(item)
        self.assertEqual(result, "", f"Rohtext ohne Datum muss leer sein, war: {result!r}")

    def test_valid_date_entry_kept(self):
        """Eintrag mit gültigem Datum bleibt erhalten."""
        item = self._item("15.12.2024", label="Kündigungsfrist")
        result = _render_legacy_deadline_text(item)
        self.assertIn("15.12.2024", result, f"Gültiges Datum muss erhalten bleiben: {result!r}")

    def test_valid_date_with_context_kept(self):
        """'Zahlung bis spätestens 03.02.2025' → bleibt erhalten."""
        item = self._item(
            "Zahlung bis spätestens 03.02.2025",
            label="Zahlungsfrist",
            snippet="Monat fällig am 03.02.2025",
        )
        result = _render_legacy_deadline_text(item)
        self.assertIn("03.02.2025", result, f"Gültiger Frist-Eintrag muss erhalten bleiben: {result!r}")

    def test_build_legacy_deadlines_drops_raw_header(self):
        """build_legacy_deadlines filtert Rohtext-Einträge heraus."""
        items = [
            {
                "text": "GEWERBEMIETVERTRAG zwischen Stadtimmobilien Frankfurt AG",
                "label": "Frist/Termin",
                "citations": [],
            },
            {
                "text": "03.02.2025",
                "label": "Frist/Termin",
                "citations": [{"page_number": 1, "quote_snippet": "fällig am 03.02.2025"}],
            },
        ]
        out = build_legacy_deadlines(items, limit=25)
        # Der Rohtext-Eintrag darf nicht in der Ausgabe sein
        for entry in out:
            self.assertNotIn("GEWERBEMIETVERTRAG", entry, f"Rohtext darf nicht in Fristen erscheinen: {entry!r}")
        # Das gültige Datum muss erhalten bleiben
        self.assertTrue(
            any("03.02.2025" in e for e in out),
            f"Gültiger Eintrag fehlt in: {out}",
        )


class Bug2bBirthDateFilterTests(unittest.TestCase):
    """Geburtsdaten (Jahr < 2000, kein Fristkontext) werden nicht als Fristen ausgegeben."""

    def _item(self, text, label="Frist/Termin", snippet=""):
        return {
            "text": text,
            "label": label,
            "snippet": snippet,
            "citations": [{"page_number": 1, "quote_snippet": snippet}] if snippet else [],
        }

    def test_birth_date_1968_dropped(self):
        """22.09.1968 (Geburtsjahr) ohne Fristkontext → kein Eintrag."""
        item = self._item("22.09.1968")
        result = _render_legacy_deadline_text(item)
        self.assertEqual(result, "", f"Geburtsdatum muss gefiltert werden, war: {result!r}")

    def test_birth_date_1975_dropped(self):
        """15.03.1975 (Geburtsjahr) ohne Fristkontext → kein Eintrag."""
        item = self._item("15.03.1975")
        result = _render_legacy_deadline_text(item)
        self.assertEqual(result, "", f"Geburtsdatum 1975 muss gefiltert werden, war: {result!r}")

    def test_birth_date_1999_dropped(self):
        """31.12.1999 ohne Fristkontext → kein Eintrag (Grenzwert)."""
        item = self._item("31.12.1999")
        result = _render_legacy_deadline_text(item)
        self.assertEqual(result, "", f"Datum vor 2000 muss gefiltert werden, war: {result!r}")

    def test_modern_date_2024_kept(self):
        """15.12.2024 → bleibt erhalten."""
        item = self._item("15.12.2024", label="Kündigungsfrist")
        result = _render_legacy_deadline_text(item)
        self.assertIn("15.12.2024", result)

    def test_modern_date_2024_kept(self):
        """01.01.2024 → bleibt erhalten (Grenzwert, year >= 2024)."""
        item = self._item("01.01.2024", label="Frist/Termin")
        result = _render_legacy_deadline_text(item)
        self.assertIn("01.01.2024", result)

    def test_date_2000_without_context_filtered(self):
        """01.01.2000 ohne Fristkontext → gefiltert (year < 2024-Regel)."""
        item = self._item("01.01.2000", label="Frist/Termin")
        result = _render_legacy_deadline_text(item)
        self.assertEqual(result, "", f"Year 2000 without context must be filtered: {result!r}")

    def test_pre_2000_date_with_deadline_context_kept(self):
        """Datum vor 2000 MIT Fristkontext ('fällig') → bleibt erhalten."""
        item = self._item(
            "Zahlung fällig bis 15.11.1999",
            label="Zahlungsfrist",
            snippet="fällig bis 15.11.1999",
        )
        result = _render_legacy_deadline_text(item)
        self.assertIn("15.11.1999", result, f"Frist-Datum mit Kontext muss erhalten bleiben: {result!r}")

    def test_vollmacht_birth_date_text_dropped(self):
        """Text mit 'geboren am 14. März 1982' ohne DD.MM.YYYY → kein Eintrag (Bug 1 greift)."""
        item = self._item(
            "VOLLMACHT Ich, Thomas Bergmann, geboren am 14. März 1982, wohnhaft Ginnheimer Stadtweg 44"
        )
        result = _render_legacy_deadline_text(item)
        self.assertEqual(result, "", f"Vollmacht-Rohtext ohne DD.MM.YYYY muss leer sein, war: {result!r}")

    def test_build_legacy_deadlines_drops_birth_date(self):
        """build_legacy_deadlines filtert Geburtsdaten ohne Fristkontext heraus."""
        items = [
            {
                "text": "22.09.1968",
                "label": "Frist/Termin",
                "citations": [{"page_number": 3, "quote_snippet": "NordCapital Beteiligungen GmbH, HRB 445566"}],
            },
            {
                "text": "05.04.2025",
                "label": "Frist/Termin",
                "citations": [{"page_number": 11, "quote_snippet": "Basiskaufpreis fällig am 05.04.2025"}],
            },
        ]
        out = build_legacy_deadlines(items, limit=25)
        for entry in out:
            self.assertNotIn("1968", entry, f"Geburtsdatum 1968 darf nicht in Fristen erscheinen: {entry!r}")
        self.assertTrue(
            any("05.04.2025" in e for e in out),
            f"Gültiger Termin 2025 fehlt in: {out}",
        )


if __name__ == "__main__":
    unittest.main()
