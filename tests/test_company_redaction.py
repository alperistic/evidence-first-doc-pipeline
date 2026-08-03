"""
test_company_redaction.py

Fix 4: Firmennamen konfigurierbar per policy.yaml.
redact_company_names: true → [FIRMA_N], false → unverändert.
"""

import unittest

from governance.redaction import prepare_for_llm, restore_from_llm


class CompanyRedactionEnabledTests(unittest.TestCase):
    """When redact_company_names=True, company names are replaced with [FIRMA_N]."""

    def _make_policy(self, *, redact: bool) -> dict:
        return {
            "dictionary_entities": [],
            "dictionary_fuzzy": False,
            "dictionary_threshold": 92,
            "pseudonymize_people": False,
            "redact_company_names": redact,
        }

    def test_gmbh_is_redacted(self):
        text = "Vertrag mit Müller GmbH."
        ctx = prepare_for_llm(text, self._make_policy(redact=True))
        self.assertNotIn("Müller GmbH", ctx.redacted_text)
        self.assertIn("[FIRMA_1]", ctx.redacted_text)
        self.assertEqual(ctx.mapping["[FIRMA_1]"], "Müller GmbH")

    def test_ag_is_redacted(self):
        text = "Partner: Schmidt AG"
        ctx = prepare_for_llm(text, self._make_policy(redact=True))
        self.assertNotIn("Schmidt AG", ctx.redacted_text)
        self.assertIn("[FIRMA_", ctx.redacted_text)

    def test_roundtrip_restore(self):
        text = "Die Müller GmbH hat mit Schmidt AG einen Vertrag."
        ctx = prepare_for_llm(text, self._make_policy(redact=True))
        restored = restore_from_llm(ctx.redacted_text, ctx.mapping)
        self.assertIn("Müller GmbH", restored)
        self.assertIn("Schmidt AG", restored)

    def test_same_company_gets_same_placeholder(self):
        text = "Müller GmbH kauft. Müller GmbH verkauft."
        ctx = prepare_for_llm(text, self._make_policy(redact=True))
        self.assertEqual(ctx.redacted_text.count("[FIRMA_1]"), 2)


class CompanyRedactionDisabledTests(unittest.TestCase):
    """When redact_company_names=False (default), company names stay unchanged."""

    def _make_policy(self) -> dict:
        return {
            "dictionary_entities": [],
            "dictionary_fuzzy": False,
            "dictionary_threshold": 92,
            "pseudonymize_people": False,
            "redact_company_names": False,
        }

    def test_gmbh_stays_visible(self):
        text = "Vertrag mit Müller GmbH."
        ctx = prepare_for_llm(text, self._make_policy())
        self.assertIn("Müller GmbH", ctx.redacted_text)
        self.assertNotIn("[FIRMA_", ctx.redacted_text)

    def test_no_firma_mapping(self):
        text = "Schmidt AG und Partner."
        ctx = prepare_for_llm(text, self._make_policy())
        firma_keys = [k for k in ctx.mapping if "FIRMA" in k]
        self.assertEqual(firma_keys, [])


class CompanyRedactionDefaultTests(unittest.TestCase):
    """Without redact_company_names key, default is False."""

    def test_default_is_off(self):
        text = "Müller GmbH ist Partei."
        ctx = prepare_for_llm(text, {
            "dictionary_entities": [],
            "dictionary_fuzzy": False,
            "dictionary_threshold": 92,
            "pseudonymize_people": False,
        })
        self.assertIn("Müller GmbH", ctx.redacted_text)


if __name__ == "__main__":
    unittest.main()
