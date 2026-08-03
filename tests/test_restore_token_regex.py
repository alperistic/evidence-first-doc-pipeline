"""
test_restore_token_regex.py

Regression-Tests für restore_from_llm() in governance/redaction.py.

Bug: _RESTORE_TOKEN_RE verwendete Lookbehind (?<![A-Z0-9_]) und Lookahead (?![A-Z0-9_]).
Das verhinderte das Matching wenn Platzhalter unmittelbar nach/vor Großbuchstaben
oder Ziffern standen (z.B. LLM-Ausgabe "GmbH[PARTEI_1]" oder "[PARTEI_1]GmbH").
→ Platzhalter blieben unrestauriert → Export-Block durch _find_internal_export_placeholder_hits.

Fix: Lookbehind/Lookahead aus _RESTORE_TOKEN_RE entfernt.
"""

import unittest

from governance.redaction import restore_from_llm


class RestoreTokenStandardCases(unittest.TestCase):
    """Standard-Kontexte: Platzhalter nach Satzzeichen, Leerzeichen, Klammern, Newline."""

    def test_placeholder_at_sentence_start(self):
        """[PARTEI_1] am Satzanfang wird restauriert."""
        result = restore_from_llm(
            "[PARTEI_1] ist Mieter. Adresse: [ADDR_1].",
            {"[PARTEI_1]": "Max Mustermann", "[ADDR_1]": "Hauptstr. 1, 10115 Berlin"},
        )
        self.assertEqual(result, "Max Mustermann ist Mieter. Adresse: Hauptstr. 1, 10115 Berlin.")

    def test_placeholder_in_parentheses(self):
        """([PARTEI_2]) — Platzhalter in Klammern wird restauriert."""
        result = restore_from_llm("([PARTEI_2])", {"[PARTEI_2]": "Anna Schmidt"})
        self.assertEqual(result, "(Anna Schmidt)")

    def test_placeholder_after_colon_newline(self):
        """Kläger:\\n[PARTEI_1] — Platzhalter nach Doppelpunkt+Zeilenumbruch."""
        result = restore_from_llm("Kläger:\n[PARTEI_1]", {"[PARTEI_1]": "Max Mustermann"})
        self.assertEqual(result, "Kläger:\nMax Mustermann")

    def test_multiple_placeholders_restored(self):
        """Mehrere Platzhalter im selben Text werden alle ersetzt."""
        result = restore_from_llm(
            "Zwischen [PARTEI_1] und [PARTEI_2] wurde ein Vertrag geschlossen.",
            {"[PARTEI_1]": "Max Mustermann", "[PARTEI_2]": "Anna Schmidt"},
        )
        self.assertNotIn("[PARTEI_1]", result)
        self.assertNotIn("[PARTEI_2]", result)
        self.assertIn("Max Mustermann", result)
        self.assertIn("Anna Schmidt", result)


class RestoreTokenBugCases(unittest.TestCase):
    """Edge Cases die durch den Lookbehind/Lookahead-Bug blockiert wurden."""

    def test_uppercase_directly_before_bracket(self):
        """GmbH[PARTEI_1] — Großbuchstabe direkt vor [ → früher: Lookbehind blockiert."""
        result = restore_from_llm(
            "GmbH[PARTEI_1] ist Beklagte.",
            {"[PARTEI_1]": "Max Mustermann"},
        )
        self.assertNotIn("[PARTEI_1]", result)
        self.assertIn("Max Mustermann", result)

    def test_digit_directly_before_bracket(self):
        """Abs. 2[PARTEI_1] — Ziffer direkt vor [ → früher: Lookbehind blockiert."""
        result = restore_from_llm(
            "Abs. 2[PARTEI_1] schuldet Zahlung.",
            {"[PARTEI_1]": "Max Mustermann"},
        )
        self.assertNotIn("[PARTEI_1]", result)
        self.assertIn("Max Mustermann", result)

    def test_uppercase_directly_after_bracket(self):
        """[PARTEI_1]GmbH — Großbuchstabe direkt nach ] → früher: Lookahead blockiert."""
        result = restore_from_llm(
            "[PARTEI_1]GmbH ist betroffen.",
            {"[PARTEI_1]": "Max Mustermann"},
        )
        self.assertNotIn("[PARTEI_1]", result)
        self.assertIn("Max Mustermann", result)

    def test_digit_directly_after_bracket(self):
        """[PARTEI_1]1. Instanz — Ziffer direkt nach ] → früher: Lookahead blockiert."""
        result = restore_from_llm(
            "[PARTEI_1]1. Instanz",
            {"[PARTEI_1]": "Max Mustermann"},
        )
        self.assertNotIn("[PARTEI_1]", result)
        self.assertIn("Max Mustermann", result)

    def test_addr_placeholder_after_colon_space(self):
        """Adresse: [ADDR_1] — ADDR-Typ nach Doppelpunkt+Leerzeichen."""
        result = restore_from_llm(
            "Kanzleiakte:\nAdresse: [ADDR_1].",
            {"[ADDR_1]": "Hauptstr. 1, 10115 Berlin"},
        )
        self.assertNotIn("[ADDR_1]", result)
        self.assertIn("Hauptstr. 1, 10115 Berlin", result)

    def test_partei_uppercase_suffix_not_matched(self):
        """[PARTEI_1A] ist kein gültiger Platzhalter und darf [PARTEI_1] nicht ersetzen."""
        result = restore_from_llm(
            "[PARTEI_1A] und [PARTEI_1]",
            {"[PARTEI_1]": "Max Mustermann"},
        )
        # [PARTEI_1] soll restauriert werden
        self.assertIn("Max Mustermann", result)
        # [PARTEI_1A] ist nicht im Mapping → bleibt als Literal
        self.assertIn("[PARTEI_1A]", result)


class RestoreTokenEdgeCases(unittest.TestCase):
    """Weitere Robustheitstests."""

    def test_empty_text_returns_empty(self):
        result = restore_from_llm("", {"[PARTEI_1]": "Max Mustermann"})
        self.assertEqual(result, "")

    def test_empty_mapping_returns_text_unchanged(self):
        text = "Kein Platzhalter hier."
        result = restore_from_llm(text, {})
        self.assertEqual(result, text)

    def test_unknown_placeholder_stays(self):
        """Platzhalter der nicht im Mapping ist, bleibt unverändert."""
        result = restore_from_llm("[PARTEI_1]", {})
        self.assertEqual(result, "[PARTEI_1]")

    def test_all_placeholder_types_restored(self):
        """DICT, PERSON, ADDR, EMAIL, IBAN, PARTEI, AKTENZEICHEN, PHONE, TAXID — alle Typen."""
        text = "[DICT_1] [PERSON_1] [ADDR_1] [EMAIL_1] [IBAN_1] [PARTEI_1] [AKTENZEICHEN_1] [PHONE_1] [TAXID_1]"
        mapping = {
            "[DICT_1]": "Vertrag",
            "[PERSON_1]": "Max Mustermann",
            "[ADDR_1]": "Hauptstr. 1",
            "[EMAIL_1]": "max@kanzlei.de",
            "[IBAN_1]": "DE89370400440532013000",
            "[PARTEI_1]": "Kläger GmbH",
            "[AKTENZEICHEN_1]": "Az. 5 C 123/24",
            "[PHONE_1]": "030-12345678",
            "[TAXID_1]": "DE123456789",
        }
        result = restore_from_llm(text, mapping)
        for placeholder in mapping:
            self.assertNotIn(placeholder, result)
        for original in mapping.values():
            self.assertIn(original, result)


if __name__ == "__main__":
    unittest.main()
