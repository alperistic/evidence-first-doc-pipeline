import unittest

from core.snippet import is_valid_evidence_source, _select_best_sentence_snippet


class EvidenceSourceFilterTests(unittest.TestCase):
    """FIX 4: Header-Zeilen und Metadaten aus Evidence-Quellen filtern."""

    def test_rejects_short_text(self):
        self.assertFalse(is_valid_evidence_source("Seite 1"))
        self.assertFalse(is_valid_evidence_source(""))
        self.assertFalse(is_valid_evidence_source("kurz"))

    def test_rejects_fiktives_testdokument(self):
        self.assertFalse(is_valid_evidence_source("Fiktives Testdokument für Kanzlei-Systemprüfung"))

    def test_rejects_kanzlei_testpaket(self):
        self.assertFalse(is_valid_evidence_source("Kanzlei-Testpaket Dokumentenmappe Version 1.0"))

    def test_rejects_nur_zur_systempruefung(self):
        self.assertFalse(is_valid_evidence_source("Dieses Dokument dient nur zur Systemprüfung."))

    def test_rejects_keine_rechtsberatung(self):
        self.assertFalse(is_valid_evidence_source("Hinweis: Keine Rechtsberatung enthalten."))

    def test_rejects_seite_only(self):
        self.assertFalse(is_valid_evidence_source("Seite 42"))

    def test_rejects_doc_id_header(self):
        self.assertFalse(is_valid_evidence_source("Doc-ID: PKG3-01 Typ: Klageschrift"))

    def test_rejects_az_header(self):
        self.assertFalse(is_valid_evidence_source("AZ: 11 C 100/26 Mietvertrag Kanzlei"))

    def test_rejects_aktenzeichen_header(self):
        self.assertFalse(is_valid_evidence_source("Aktenzeichen: AKTE-2026-001 Dokumentenmappe"))

    def test_rejects_hinweis_header(self):
        self.assertFalse(is_valid_evidence_source("Hinweis: Dies ist ein automatisch generierter Vermerk."))

    def test_rejects_version_line(self):
        self.assertFalse(is_valid_evidence_source("version 2.1"))

    def test_accepts_substantive_content(self):
        self.assertTrue(is_valid_evidence_source(
            "Der Mieter hat die Miete bis zum 3. Werktag eines jeden Monats zu entrichten."
        ))

    def test_accepts_legal_clause(self):
        self.assertTrue(is_valid_evidence_source(
            "Kündigungsfrist beträgt 3 Monate zum Quartalsende gemäß § 7 Abs. 2."
        ))

    def test_accepts_payment_sentence(self):
        self.assertTrue(is_valid_evidence_source(
            "Zahlung bis spätestens 17.06.2026 auf das benannte Konto."
        ))


class PR9ExtendedBlacklistTests(unittest.TestCase):
    """Fix B: Erweiterte Blacklist + EUR-Betrag-Bevorzugung."""

    def test_rejects_az_line_at_start(self):
        self.assertFalse(is_valid_evidence_source("AZ: AKTE-MIX-2026-0422 Dokumentenmappe"))

    def test_rejects_doc_id_line_at_start(self):
        self.assertFalse(is_valid_evidence_source("DOC-ID: MIX-PAKET-2026-04-22 / Version 1.0"))

    def test_rejects_standalone_version_line(self):
        self.assertFalse(is_valid_evidence_source("Version 1.0"))

    def test_rejects_short_line_under_25_chars(self):
        self.assertFalse(is_valid_evidence_source("Kurztext unter 25"))

    def test_eur_clause_preferred_over_meta_line(self):
        """EUR-Betrag in Klausel wird gegenüber Meta-Zeile bevorzugt."""
        text = (
            "AZ: AKTE-MIX-2026-0422 Dokumentenmappe\n"
            "Der vereinbarte Betrag von EUR 10.000,00 ist bis 30.06.2026 fällig."
        )
        result = _select_best_sentence_snippet(text, "EUR 10.000,00", field_hint="fristen_termine")
        self.assertNotIn("AZ:", result, "Meta-Zeile darf nicht als Anker gewählt werden")
        self.assertIn("10.000,00", result)


class PR10RealStringBlacklistTests(unittest.TestCase):
    """Fix B': Echte Dokument-Strings 1-4 als Evidence-Quellen ablehnen.

    Root Cause: String 1 enthält 'AZ: AKTE-' INLINE (nach führendem Text), nicht
    am Zeilenanfang → die ^AZ-Muster aus PR9 greifen nicht. Neues Inline-Muster
    r'\\bAZ\\s*:\\s*[A-Z]{2,}[-/]' matcht überall im String.
    Strings 2-4 waren durch PR9-Patterns bereits abgedeckt – Tests dokumentieren
    diese Abdeckung explizit mit den echten Output-Strings.
    """

    def test_rejects_string1_az_inline(self):
        """String 1: 'AZ: AKTE-' erscheint inline nach führendem Text → rejected."""
        s = (
            "Übergabeprotokoll, Hausordnung und Anlagen Aktenzeichen: AZ: AKTE-MIX-2026-0422\n"
            "Dokumenten-ID: DOC-ID: MIX-PAKET-2026-04-22 / Version 1.0 Dokumenttyp: Wohnraum..."
        )
        self.assertFalse(
            is_valid_evidence_source(s),
            "String 1 (AZ: AKTE-MIX inline) muss als ungültige Evidence-Quelle abgelehnt werden",
        )

    def test_rejects_string2_kanzlei_testpaket_fiktives(self):
        """String 2: 'Kanzlei-Testpaket' + 'Fiktives Testdokument' → rejected."""
        s = (
            "Kanzlei-Testpaket (fiktiv) - Mixed PDF mit mehreren Dokumenttypen AZ: AKTE-MIX-2026-0422\n"
            "Fiktives Testdokument - nur zur Systemprüfung (Segmentierung/Extraktio..."
        )
        self.assertFalse(
            is_valid_evidence_source(s),
            "String 2 (Kanzlei-Testpaket + Fiktives Testdokument) muss abgelehnt werden",
        )

    def test_rejects_string4_rahmenvertrag_meta(self):
        """String 4: 'Fiktives Testdokument' + 'keine Rechtsberatung' → rejected."""
        s = (
            "Rahmenvertrag über IT-Dienstleistungen und Auftragsverarbeitung (Testdokument) AZ:\n"
            "AKTE-TEST-2026-0417-ITRV Fiktives Testdokument - keine Rechtsberatung - nur..."
        )
        self.assertFalse(
            is_valid_evidence_source(s),
            "String 4 (Fiktives Testdokument + keine Rechtsberatung) muss abgelehnt werden",
        )

    def test_accepts_real_content_clause_mietbetrag(self):
        """Echter Klausel-Satz mit EUR-Betrag → accepted."""
        self.assertTrue(
            is_valid_evidence_source(
                "Die monatliche Nettokaltmiete beträgt EUR 1.090,00 zzgl. Betriebskosten."
            ),
            "Echter Inhalts-Satz muss als gültige Evidence-Quelle akzeptiert werden",
        )

    def test_select_snippet_skips_az_inline_sentence(self):
        """_select_best_sentence_snippet bevorzugt echten Klausel-Satz über AZ-Inline-Zeile.

        Fallback-Logik: abgelehnte Sätze (is_valid_evidence_source=False) werden
        übersprungen; der nächstbeste nicht-abgelehnte Satz wird gewählt.
        """
        text = (
            "Übergabeprotokoll, Hausordnung und Anlagen Aktenzeichen: AZ: AKTE-MIX-2026-0422\n"
            "Die monatliche Nettokaltmiete beträgt EUR 1.090,00 zzgl. Betriebskosten."
        )
        result = _select_best_sentence_snippet(text, "EUR 1.090,00", field_hint="betraege")
        self.assertNotIn("AZ:", result, "AZ-Metadaten-Zeile darf nicht als Quote-Anker gewählt werden")
        self.assertIn("1.090,00", result, "EUR-Betrag muss im gewählten Snippet enthalten sein")


if __name__ == "__main__":
    unittest.main()
