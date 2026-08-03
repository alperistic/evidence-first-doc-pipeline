"""
test_redaction_bic_and_birthdate.py

Bug 2a (P0): Geburtsdaten werden nicht durch die Redaktion maskiert (§203-Lücke).
Bug 3  (P0): BIC-Codes werden nicht maskiert obwohl IBAN korrekt maskiert wird.
"""

import unittest
import re

from governance.redaction import prepare_for_llm


class Bug3BicMaskingTests(unittest.TestCase):
    """BIC-Codes müssen in der Mandantenfassung (redacted_text) maskiert sein."""

    def test_bic_heladef1822_is_masked(self):
        """HELADEF1822 aus dem Mietrecht-Test muss als [BIC_1] maskiert werden."""
        text = (
            "Bitte überweisen Sie den Betrag auf unser Konto: "
            "IBAN: DE89 5005 0201 0000 1234 56 BIC: HELADEF1822 "
            "Verwendungszweck: MV Berger Str. 221"
        )
        ctx = prepare_for_llm(text, {"pseudonymize_people": False})
        self.assertNotIn(
            "HELADEF1822",
            ctx.redacted_text,
            f"BIC darf nicht im Klartext bleiben: {ctx.redacted_text!r}",
        )
        self.assertIn("[BIC_", ctx.redacted_text, "BIC-Platzhalter muss im Text erscheinen")

    def test_bic_8chars_is_masked(self):
        """BIC mit 8 Zeichen (ohne Branch-Code) muss maskiert werden."""
        text = "Kontoverbindung: IBAN: DE44500105175407324931 BIC: COBADEFF"
        ctx = prepare_for_llm(text, {"pseudonymize_people": False})
        self.assertNotIn(
            "COBADEFF",
            ctx.redacted_text,
            f"8-stelliger BIC muss maskiert sein: {ctx.redacted_text!r}",
        )

    def test_bic_11chars_is_masked(self):
        """BIC mit 11 Zeichen muss maskiert werden."""
        text = "SWIFT/BIC: DEUTDEDBHAM"
        ctx = prepare_for_llm(text, {"pseudonymize_people": False})
        self.assertNotIn(
            "DEUTDEDBHAM",
            ctx.redacted_text,
            f"11-stelliger BIC muss maskiert sein: {ctx.redacted_text!r}",
        )

    def test_iban_still_masked_when_bic_added(self):
        """IBAN muss weiterhin maskiert sein nachdem BIC-Regel hinzugefügt wurde."""
        text = "IBAN: DE89 5005 0201 0000 1234 56 BIC: HELADEF1822"
        ctx = prepare_for_llm(text, {"pseudonymize_people": False})
        self.assertNotIn("DE89", ctx.redacted_text, "IBAN darf nicht im Klartext bleiben")
        self.assertIn("[IBAN_", ctx.redacted_text, "IBAN-Platzhalter muss erscheinen")

    def test_bic_in_mapping(self):
        """BIC-Original muss im Mapping gespeichert sein."""
        text = "BIC: HELADEF1822"
        ctx = prepare_for_llm(text, {"pseudonymize_people": False})
        self.assertIn(
            "HELADEF1822",
            ctx.mapping.values(),
            f"BIC-Originalwert muss im Mapping stehen: {dict(ctx.mapping)!r}",
        )

    def test_regular_uppercase_abbreviation_not_masked_as_bic(self):
        """Normale Abkürzungen wie 'GmbH' oder 'ENTWURF' dürfen nicht als BIC maskiert werden."""
        text = "Die GmbH hat den ENTWURF vorgelegt."
        ctx = prepare_for_llm(text, {"pseudonymize_people": False})
        # GmbH = 4 chars, doesn't match BIC pattern
        # ENTWURF = 7 chars, doesn't match 8/11 char BIC pattern
        self.assertNotIn("[BIC_", ctx.redacted_text, f"Falsch-Positiv BIC-Maskierung: {ctx.redacted_text!r}")


class Bug2aBirthDateRedactionTests(unittest.TestCase):
    """Geburtsdaten müssen vor LLM-Übergabe maskiert werden (§203-Konformität)."""

    def test_geboren_am_date_is_masked(self):
        """'geboren am DD.MM.YYYY' muss im redacted_text maskiert sein."""
        text = (
            "Frau Dr. Ingrid Pfeiffer, geboren am 22.09.1968, "
            "wohnhaft Dornbuschweg 12, Frankfurt am Main"
        )
        ctx = prepare_for_llm(text, {"pseudonymize_people": False})
        self.assertNotIn(
            "22.09.1968",
            ctx.redacted_text,
            f"Geburtsdatum darf nicht im Klartext an LLM gesendet werden: {ctx.redacted_text!r}",
        )
        self.assertIn("[DATUM_", ctx.redacted_text, "DATUM-Platzhalter muss erscheinen")

    def test_geb_abbreviation_date_is_masked(self):
        """'geb. DD.MM.YYYY' muss im redacted_text maskiert sein."""
        text = "Thomas Bergmann, geb. 14.03.1982, wohnhaft Ginnheimer Stadtweg 44, Frankfurt"
        ctx = prepare_for_llm(text, {"pseudonymize_people": False})
        self.assertNotIn(
            "14.03.1982",
            ctx.redacted_text,
            f"Geburtsdatum (geb.) darf nicht im Klartext bleiben: {ctx.redacted_text!r}",
        )

    def test_geboren_am_keeps_context_words(self):
        """Der Kontext 'geboren am' muss im redacted_text erhalten bleiben."""
        text = "Thomas Bergmann, geboren am 14.03.1982, wohnhaft Frankfurt"
        ctx = prepare_for_llm(text, {"pseudonymize_people": False})
        self.assertIn(
            "geboren am",
            ctx.redacted_text.lower(),
            f"Kontext 'geboren am' muss erhalten bleiben: {ctx.redacted_text!r}",
        )

    def test_birthdate_in_mapping(self):
        """Geburtsdatum-Original muss im Mapping hinterlegt sein (für Wiederherstellung)."""
        text = "geboren am 22.09.1968"
        ctx = prepare_for_llm(text, {"pseudonymize_people": False})
        self.assertIn(
            "22.09.1968",
            ctx.mapping.values(),
            f"Geburtsdatum muss im Mapping stehen: {dict(ctx.mapping)!r}",
        )

    def test_regular_date_without_born_context_not_masked_as_datum(self):
        """Datum ohne Geburtskontext ('15.12.2024') darf NICHT als [DATUM] maskiert werden."""
        text = "Kündigung vom 15.12.2024 zum 31.03.2025."
        ctx = prepare_for_llm(text, {"pseudonymize_people": False})
        # Keine DATUM-Maskierung für normale Vertragsdaten
        self.assertNotIn(
            "[DATUM_",
            ctx.redacted_text,
            f"Normales Vertragsdatum darf nicht als DATUM maskiert werden: {ctx.redacted_text!r}",
        )

    def test_geb_punkt_date_masked(self):
        """'geb. 15.03.1975' → Datum maskiert."""
        text = "Herr Klaus Müller, geb. 15.03.1975, Gesellschafter"
        ctx = prepare_for_llm(text, {"pseudonymize_people": False})
        self.assertNotIn(
            "15.03.1975",
            ctx.redacted_text,
            f"Geburtsdatum (geb.) muss maskiert sein: {ctx.redacted_text!r}",
        )


if __name__ == "__main__":
    unittest.main()
