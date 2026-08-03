import json
import re
import unittest

from governance.redaction import (
    apply_dictionary_redaction,
    prepare_for_llm,
    redact_with_preview,
    restore_from_llm,
)


class DictionaryRedactionTests(unittest.TestCase):
    def test_exact_matching_is_case_insensitive_with_umlauts(self):
        text = "Vertreterin: dr. anna weiß\nPartei: MÜLLER GMBH"
        redacted, mapping, report = apply_dictionary_redaction(
            text=text,
            entities=["Dr. Anna Weiß", "Müller GmbH", "müller gmbh"],
            fuzzy=False,
            threshold=92,
        )

        self.assertEqual(redacted, "Vertreterin: [DICT_1]\nPartei: [DICT_2]")
        self.assertEqual(mapping["[DICT_1]"].casefold(), "dr. anna weiss")
        self.assertEqual(mapping["[DICT_2]"].casefold(), "müller gmbh")
        self.assertEqual(report["matched_entities_count"], 2)
        self.assertEqual(report["matched_masks"], ["[DICT_1]", "[DICT_2]"])
        self.assertEqual(report["mask_counts"], {"[DICT_1]": 1, "[DICT_2]": 1})

    def test_whole_word_default_does_not_match_street_suffix(self):
        text = "Müllerstraße 5 gehört Müller."
        redacted, _, report = apply_dictionary_redaction(
            text=text,
            entities=["Müller"],
            fuzzy=False,
            threshold=92,
        )

        self.assertEqual(redacted, "Müllerstraße 5 gehört [DICT_1].")
        self.assertEqual(report["mask_counts"], {"[DICT_1]": 1})

    def test_sorting_by_length_prevents_partial_breakage(self):
        text = "Dr. Anna Weiß spricht mit Weiß."
        redacted, _, report = apply_dictionary_redaction(
            text=text,
            entities=["Weiß", "Dr. Anna Weiß"],
            fuzzy=False,
            threshold=92,
        )

        self.assertEqual(redacted, "[DICT_1] spricht mit [DICT_2].")
        self.assertEqual(report["mask_counts"], {"[DICT_1]": 1, "[DICT_2]": 1})

    def test_exact_matching_handles_hyphen_and_linebreak(self):
        text = "Kontakt: Dr.\nAnna Weiß-Schmidt"
        redacted, _, report = apply_dictionary_redaction(
            text=text,
            entities=["Dr. Anna Weiß-Schmidt"],
            fuzzy=False,
            threshold=92,
        )

        self.assertEqual(redacted, "Kontakt: [DICT_1]")
        self.assertEqual(report["matched_masks"], ["[DICT_1]"])

    def test_fuzzy_matching_is_optional_and_default_off(self):
        text = "Vertreterin: Dr Anna Wess"
        entity = ["Dr. Anna Weiß"]

        redacted_exact, _, report_exact = apply_dictionary_redaction(
            text=text,
            entities=entity,
            fuzzy=False,
            threshold=92,
        )
        redacted_fuzzy, _, report_fuzzy = apply_dictionary_redaction(
            text=text,
            entities=entity,
            fuzzy=True,
            threshold=92,
        )

        self.assertEqual(redacted_exact, text)
        self.assertEqual(report_exact["matched_entities_count"], 0)
        self.assertEqual(redacted_fuzzy, "Vertreterin: [DICT_1]")
        self.assertEqual(report_fuzzy["matched_entities_count"], 1)

    def test_fuzzy_threshold_controls_replacement(self):
        text = "Vertreterin: Dr Anna Wess"
        entity = ["Dr. Anna Weiß"]

        redacted_high, _, report_high = apply_dictionary_redaction(
            text=text,
            entities=entity,
            fuzzy=True,
            threshold=98,
        )
        redacted_low, _, report_low = apply_dictionary_redaction(
            text=text,
            entities=entity,
            fuzzy=True,
            threshold=90,
        )

        self.assertEqual(redacted_high, text)
        self.assertEqual(report_high["matched_entities_count"], 0)
        self.assertEqual(redacted_low, "Vertreterin: [DICT_1]")
        self.assertEqual(report_low["matched_entities_count"], 1)

    def test_redact_with_preview_returns_mask_only_reporting(self):
        text = "Klägerin: Dr. Anna Weiß, E-Mail anna@example.com"
        redacted, findings, dictionary_report = redact_with_preview(
            text=text,
            entities=["Dr. Anna Weiß"],
            fuzzy=False,
            threshold=92,
        )

        self.assertIn("[DICT_1]", redacted)
        self.assertIn("[EMAIL_1]", redacted)
        self.assertEqual(dictionary_report["matched_entities_count"], 1)
        self.assertEqual(dictionary_report["matched_masks"], ["[DICT_1]"])
        serialized_findings = json.dumps(findings, ensure_ascii=False)
        self.assertNotIn("Anna Weiß", serialized_findings)
        self.assertNotIn("anna@example.com", serialized_findings)

    def test_roundtrip_restore_restores_original_values(self):
        original = "Mandantin: Dr. Anna Weiß, E-Mail anna@example.com"
        context = prepare_for_llm(
            original,
            {
                "dictionary_entities": ["Dr. Anna Weiß"],
                "dictionary_fuzzy": False,
                "dictionary_threshold": 92,
                "pseudonymize_people": True,
            },
        )
        llm_output = "Zusammenfassung: [DICT_1] meldete sich per [EMAIL_1]."
        restored = restore_from_llm(llm_output, context.mapping)
        self.assertIn("Dr. Anna Weiß", restored)
        self.assertIn("anna@example.com", restored)

    def test_restore_replaces_only_exact_allowed_tokens(self):
        mapping = {
            "[PERSON_1]": "Max Mustermann",
            "[ADDR_1]": "Reeperbahn 77, 20359 Hamburg",
            "[EMAIL_1]": "max@example.org",
        }
        llm_output = (
            "Kontakt: [PERSON_1], Adresse [ADDR_1], Mail [EMAIL_1], "
            "invalid [PERSON_ABC], unmatched [ADDR_999]"
        )

        restored = restore_from_llm(llm_output, mapping)

        self.assertIn("Max Mustermann", restored)
        self.assertIn("Reeperbahn 77, 20359 Hamburg", restored)
        self.assertIn("max@example.org", restored)
        self.assertIn("[PERSON_ABC]", restored)
        self.assertIn("[ADDR_999]", restored)
        self.assertIsNone(re.search(r"\[PERSON_\[|\[ADDR_\[", restored))

    def test_addr_redaction_does_not_touch_money_or_date(self):
        text = (
            "Forderung: 28.512,00 EUR. Frist: 07.02.2026. "
            "Anschrift: Reeperbahn 77, 20359 Hamburg."
        )
        ctx = prepare_for_llm(
            text,
            {
                "dictionary_entities": [],
                "dictionary_fuzzy": False,
                "dictionary_threshold": 92,
                "pseudonymize_people": False,
            },
        )

        self.assertIn("28.512,00 EUR", ctx.redacted_text)
        self.assertIn("07.02.2026", ctx.redacted_text)
        self.assertRegex(ctx.redacted_text, r"\[(?:ADDR)_[0-9]+\]")
        self.assertNotRegex(ctx.redacted_text, r"\[(?:ADDR)_[0-9]+\]\d")
        self.assertNotRegex(ctx.redacted_text, r"\d\[(?:ADDR)_[0-9]+\]")

    def test_prepare_for_llm_avoids_placeholder_collision_with_pseudonymization(self):
        text = "Rechtsanwalt Max Mustermann spricht mit Alice Alpha, Bob Beta und Carol Gamma."
        ctx = prepare_for_llm(
            text,
            {
                "dictionary_entities": [],
                "dictionary_fuzzy": False,
                "dictionary_threshold": 92,
                "pseudonymize_people": True,
            },
        )

        self.assertEqual(len(ctx.mapping), 4)
        self.assertIn("Rechtsanwalt Max Mustermann", ctx.mapping.values())
        self.assertIn("Alice Alpha", ctx.mapping.values())
        self.assertIn("Bob Beta", ctx.mapping.values())
        self.assertIn("Carol Gamma", ctx.mapping.values())
        self.assertIn("[PERSON_1]", ctx.redacted_text)
        self.assertIn("[PARTEI_1]", ctx.redacted_text)
        self.assertIn("[PARTEI_2]", ctx.redacted_text)
        self.assertIn("[PERSON_2]", ctx.redacted_text)


if __name__ == "__main__":
    unittest.main()
