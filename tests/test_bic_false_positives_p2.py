"""
test_bic_false_positives_p2.py

FIX 5 (P2): BIC-Regex erzeugte Fehlalarme für deutsche Großbuchstaben-Wörter.

"SCHRIFTSATZ" (11 Zeichen) wurde als BIC erkannt, weil das Pattern
[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})? keine Ländercode-Validierung hatte.

Fix: validate=_is_valid_bic prüft Positionen 5-6 gegen ISO-3166-1-Alpha-2-Codes.
"""

import unittest

from governance.redaction import prepare_for_llm, _is_valid_bic


def _policy() -> dict:
    return {
        "dictionary_entities": [],
        "dictionary_fuzzy": False,
        "dictionary_threshold": 92,
        "pseudonymize_people": False,
        "redact_company_names": False,
    }


class BicValidIngddeffTests(unittest.TestCase):
    """Valide BIC-Codes (INGDDEFFXXX, HELADEF1822) müssen weiterhin maskiert werden."""

    def test_bic_valid_ingddeffxxx(self):
        """INGDDEFFXXX (ING, DE, FF, XXX) → gültiger BIC, muss maskiert werden."""
        text = "BIC: INGDDEFFXXX"
        ctx = prepare_for_llm(text, _policy())
        self.assertNotIn("INGDDEFFXXX", ctx.redacted_text,
                         f"Gültiger BIC INGDDEFFXXX muss maskiert sein: {ctx.redacted_text!r}")
        self.assertIn("[BIC_", ctx.redacted_text,
                      f"[BIC_N]-Platzhalter fehlt: {ctx.redacted_text!r}")

    def test_bic_valid_heladef1822(self):
        """HELADEF1822 (Helaba, DE, F1, 822) → gültiger BIC, muss maskiert werden."""
        text = "BIC: HELADEF1822"
        ctx = prepare_for_llm(text, _policy())
        self.assertNotIn("HELADEF1822", ctx.redacted_text,
                         f"Gültiger BIC HELADEF1822 muss maskiert sein: {ctx.redacted_text!r}")
        self.assertIn("[BIC_", ctx.redacted_text,
                      f"[BIC_N]-Platzhalter fehlt: {ctx.redacted_text!r}")

    def test_bic_valid_sskmdemmxxx(self):
        """SSKMDEMMXXX (Sparkasse, DE, MM, XXX) → gültiger BIC, muss maskiert werden."""
        text = "BIC SSKMDEMMXXX, IBAN DE89 3704 0044 0532 0130 00"
        ctx = prepare_for_llm(text, _policy())
        self.assertNotIn("SSKMDEMMXXX", ctx.redacted_text,
                         f"Gültiger BIC SSKMDEMMXXX muss maskiert sein: {ctx.redacted_text!r}")

    def test_bic_valid_is_valid_bic_function(self):
        """_is_valid_bic gibt True für gültige SWIFT-BICs zurück."""
        self.assertTrue(_is_valid_bic("INGDDEFFXXX"), "INGDDEFFXXX muss valide sein")
        self.assertTrue(_is_valid_bic("HELADEF1822"), "HELADEF1822 muss valide sein")
        self.assertTrue(_is_valid_bic("SSKMDEMMXXX"), "SSKMDEMMXXX muss valide sein")
        self.assertTrue(_is_valid_bic("DEUTDEDB"), "DEUTDEDB (8 Zeichen) muss valide sein")


class BicFalsePositiveSchriftsatzTests(unittest.TestCase):
    """Falsch-Positive Wörter dürfen NICHT als BIC maskiert werden."""

    def test_bic_false_positive_schriftsatz(self):
        """'SCHRIFTSATZ' (11 Zeichen, IF ist kein ISO-3166-Code) → NICHT maskiert."""
        text = "SCHRIFTSATZ vom Kläger eingereicht."
        ctx = prepare_for_llm(text, _policy())
        self.assertIn("SCHRIFTSATZ", ctx.redacted_text,
                      f"'SCHRIFTSATZ' darf nicht als BIC maskiert werden: {ctx.redacted_text!r}")
        self.assertNotIn("[BIC_", ctx.redacted_text,
                         f"Kein [BIC_N]-Platzhalter für 'SCHRIFTSATZ': {ctx.redacted_text!r}")

    def test_bic_false_positive_november(self):
        """'NOVEMBER' (8 Zeichen, MB ist kein ISO-3166-Code) → NICHT maskiert."""
        text = "Die Frist endet im NOVEMBER dieses Jahres."
        ctx = prepare_for_llm(text, _policy())
        self.assertIn("NOVEMBER", ctx.redacted_text,
                      f"'NOVEMBER' darf nicht als BIC maskiert werden: {ctx.redacted_text!r}")
        self.assertNotIn("[BIC_", ctx.redacted_text,
                         f"Kein [BIC_N]-Platzhalter für 'NOVEMBER': {ctx.redacted_text!r}")

    def test_bic_false_positive_kurzwoerter(self):
        """Häufige deutsche Rechtswörter in Großbuchstaben → NICHT maskiert."""
        words = ["ANTRAG", "KLAGE", "URTEIL", "BESCHLUSS"]
        for word in words:
            with self.subTest(word=word):
                ctx = prepare_for_llm(word, _policy())
                self.assertIn(word, ctx.redacted_text,
                              f"'{word}' darf nicht als BIC maskiert werden: {ctx.redacted_text!r}")

    def test_bic_is_valid_bic_false_for_words(self):
        """_is_valid_bic gibt False für Nicht-BIC-Wörter zurück."""
        self.assertFalse(_is_valid_bic("SCHRIFTSATZ"), "SCHRIFTSATZ ist kein BIC")
        self.assertFalse(_is_valid_bic("NOVEMBER"), "NOVEMBER ist kein BIC")
        self.assertFalse(_is_valid_bic("ANTRAG"), "ANTRAG (6 Zeichen) ist kein BIC")
        self.assertFalse(_is_valid_bic("ABCDEFGH"), "ABCDEF ist kein gültiger Ländercode")

    def test_bic_valid_bic_in_context(self):
        """Valider BIC in realistischem Kontext wird korrekt maskiert, Wörter bleiben."""
        text = (
            "Der SCHRIFTSATZ enthält folgende Bankverbindung:\n"
            "IBAN: DE89 3704 0044 0532 0130 00\n"
            "BIC: COBADEFFXXX\n"
            "Zahlung bis NOVEMBER fällig.\n"
        )
        ctx = prepare_for_llm(text, _policy())
        self.assertIn("SCHRIFTSATZ", ctx.redacted_text,
                      f"'SCHRIFTSATZ' darf nicht maskiert werden: {ctx.redacted_text!r}")
        self.assertIn("NOVEMBER", ctx.redacted_text,
                      f"'NOVEMBER' darf nicht maskiert werden: {ctx.redacted_text!r}")
        self.assertNotIn("COBADEFFXXX", ctx.redacted_text,
                         f"Gültiger BIC COBADEFFXXX muss maskiert sein: {ctx.redacted_text!r}")
        self.assertNotIn("DE89 3704 0044 0532 0130 00", ctx.redacted_text,
                         f"IBAN muss maskiert sein: {ctx.redacted_text!r}")


if __name__ == "__main__":
    unittest.main()
