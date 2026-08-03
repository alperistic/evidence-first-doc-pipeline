"""
test_quote_anchor_fristen_p1.py

FIX 2 (P1): Quote-Anchor greift falsche Sätze bei Fristen.

Der Sentence-Selector soll für Fristen-Felder NUR Sätze auswählen die:
a) Ein erkennbares Datum enthalten (DD.MM.YYYY, "31. März 2025", ISO-Format) ODER
b) Eine explizite Fristbezeichnung enthalten ("Frist", "Termin", "fällig", ...)

Wenn kein solcher Satz gefunden wird → "" zurückgeben.
"""

import unittest

from core.snippet import _select_best_sentence_snippet


class QuoteAnchorKeinDatumGibtLeerTests(unittest.TestCase):
    """Wenn kein Datum/Frist-Keyword im Text, muss der Fristen-Selector '' zurückgeben."""

    def test_quote_anchor_kein_datum_gibt_leer(self):
        """Sätze ohne Datum oder Frist-Keyword → '' für fristen_termine-Feld."""
        text = (
            "Dies ist ein allgemeiner Satz ohne Datum. "
            "Hier steht auch kein Termin. "
            "Nur normaler Text ohne Zeitbezug."
        )
        result = _select_best_sentence_snippet(
            text,
            needle="allgemeiner Satz",
            field_hint="fristen_termine",
            fallback="allgemeiner Satz",
        )
        self.assertEqual(
            result, "",
            f"Satz ohne Datum/Frist-Keyword muss '' ergeben, war: {result!r}",
        )

    def test_quote_anchor_ohne_field_hint_gibt_fallback(self):
        """Ohne field_hint (kein Fristen-Feld): normales Fallback-Verhalten bleibt."""
        text = "Dies ist ein allgemeiner Satz ohne Datum."
        result = _select_best_sentence_snippet(
            text,
            needle="allgemeiner Satz",
            field_hint=None,
            fallback="Fallback-Text",
        )
        # Should return either the sentence or the fallback, NOT ""
        self.assertNotEqual(result, "", "Ohne field_hint kein leeres Ergebnis erwartet")


class QuoteAnchorAdressblockWirdAbgelehntTests(unittest.TestCase):
    """Adressblöcke und Dokumentheader sind keine gültigen Fristen-Snippets."""

    def test_quote_anchor_adressblock_wird_abgelehnt(self):
        """Adressblock enthält kein Datum/Frist-Keyword → '' für fristen_termine."""
        text = (
            "Innovatech Solutions GmbH "
            "Hanauer Landstraße 291, 60314 Frankfurt am Main "
            "Geschäftsführer: Thomas Bergmann"
        )
        result = _select_best_sentence_snippet(
            text,
            needle="Hanauer Landstraße",
            field_hint="fristen_termine",
        )
        self.assertEqual(
            result, "",
            f"Adressblock darf nicht als Fristen-Quote erscheinen: {result!r}",
        )

    def test_quote_anchor_dokumentheader_wird_abgelehnt(self):
        """Vollmacht-Header ohne Datum → '' für fristen_termine."""
        text = (
            "VOLLMACHT "
            "Ich, Thomas Bergmann, wohnhaft Ginnheimer Stadtweg 44, Frankfurt"
        )
        result = _select_best_sentence_snippet(
            text,
            needle="Thomas Bergmann",
            field_hint="kuendigung_fristen",
        )
        self.assertEqual(
            result, "",
            f"Vollmacht-Header darf nicht als Kündigungsfrist-Quote erscheinen: {result!r}",
        )

    def test_quote_anchor_gewerbemietvertrag_header_abgelehnt(self):
        """GEWERBEMIETVERTRAG-Header ohne Datum → '' für fristen_termine."""
        text = (
            "GEWERBEMIETVERTRAG zwischen Stadtimmobilien Frankfurt AG, "
            "Mainzer Landstraße 50, 60325 Frankfurt"
        )
        result = _select_best_sentence_snippet(
            text,
            needle="Stadtimmobilien Frankfurt",
            field_hint="fristen_termine",
        )
        self.assertEqual(
            result, "",
            f"Vertragsheader darf nicht als Fristen-Quote erscheinen: {result!r}",
        )


class QuoteAnchorBetriebszugehoerigkeit_ist_keine_FristTests(unittest.TestCase):
    """'2 Jahre' im Kontext von Betriebszugehörigkeit ist keine Frist."""

    def test_quote_anchor_betriebszugehoerigkeit_ist_keine_frist(self):
        """'2 Jahre Betriebszugehörigkeit' enthält kein Datum/Frist-Keyword → ''."""
        text = (
            "Thomas Bergmann war 2 Jahre im Unternehmen beschäftigt. "
            "Die Betriebszugehörigkeit beträgt 2 Jahre."
        )
        result = _select_best_sentence_snippet(
            text,
            needle="2 Jahre",
            field_hint="kuendigung_fristen",
        )
        self.assertEqual(
            result, "",
            f"Betriebszugehörigkeitssatz darf nicht als Kündigungsfrist erscheinen: {result!r}",
        )

    def test_quote_anchor_kuendigung_mit_datum_wird_gefunden(self):
        """Kündigungsfrist MIT Datum → wird korrekt selektiert."""
        text = (
            "Das Arbeitsverhältnis endet zum 31.03.2025. "
            "Die Kündigung wurde fristgerecht eingereicht. "
            "Kündigungsfrist: 3 Monate zum Quartalsende."
        )
        result = _select_best_sentence_snippet(
            text,
            needle="31.03.2025",
            field_hint="kuendigung_fristen",
        )
        self.assertNotEqual(result, "", f"Datum-Satz muss selektiert werden, war: {result!r}")
        self.assertIn("31.03.2025", result, f"Datum muss im Ergebnis enthalten sein: {result!r}")


class QuoteAnchorEchtesDatumWirdGefundenTests(unittest.TestCase):
    """Echte Datumsangaben werden korrekt als Fristen-Snippet selektiert."""

    def test_quote_anchor_echtes_datum_wird_gefunden(self):
        """Satz mit DD.MM.YYYY wird für fristen_termine korrekt ausgewählt."""
        text = (
            "Der Mietvertrag beginnt am 01.04.2025. "
            "Die monatliche Miete beträgt 1.200 EUR. "
            "Das Mietverhältnis ist bis zum 31.03.2028 befristet."
        )
        result = _select_best_sentence_snippet(
            text,
            needle="01.04.2025",
            field_hint="fristen_termine",
        )
        self.assertNotEqual(result, "", f"Datum-Satz muss gefunden werden, war: {result!r}")
        self.assertIn("01.04.2025", result, f"Datum muss im Snippet stehen: {result!r}")

    def test_quote_anchor_faellig_keyword_akzeptiert(self):
        """Satz mit 'fällig' ohne explizites Datum wird als Fristen-Quote akzeptiert."""
        text = (
            "Der Kaufpreis ist sofort fällig nach Vertragsunterzeichnung. "
            "Die Parteien haben dies vereinbart."
        )
        result = _select_best_sentence_snippet(
            text,
            needle="fällig",
            field_hint="fristen_termine",
        )
        self.assertNotEqual(result, "", f"'fällig'-Satz muss akzeptiert werden: {result!r}")

    def test_quote_anchor_frist_keyword_in_satz_akzeptiert(self):
        """Satz mit Frist-Keyword 'Ablauf' wird als gültiger Fristen-Satz erkannt."""
        text = (
            "Ablauf der Widerspruchsfrist nach gesetzlicher Regelung. "
            "Alle Parteien sind einverstanden."
        )
        result = _select_best_sentence_snippet(
            text,
            needle="Widerspruchsfrist",
            field_hint="fristen_termine",
        )
        self.assertNotEqual(result, "", f"Satz mit Frist-Keyword muss akzeptiert werden: {result!r}")

    def test_quote_anchor_spaetestens_keyword_akzeptiert(self):
        """Satz mit 'spätestens' wird für fristen_termine akzeptiert."""
        text = (
            "Zahlung muss spätestens bis Ende des Monats erfolgen. "
            "Keine weiteren Verlängerungen möglich."
        )
        result = _select_best_sentence_snippet(
            text,
            needle="spätestens",
            field_hint="fristen_termine",
        )
        self.assertNotEqual(result, "", f"'spätestens'-Satz muss akzeptiert werden: {result!r}")


if __name__ == "__main__":
    unittest.main()
