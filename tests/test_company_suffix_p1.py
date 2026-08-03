"""
test_company_suffix_p1.py

FIX 4 (P1): "[ORG_N] & Co. KG" / "& Co. KGaA" Suffix nicht vollständig maskiert.

_STANDALONE_ORG_RE erfasste bisher nur "GmbH" ohne den "& Co. KGaA"-Suffix.
Fix: KGaA in _STANDALONE_ORG_RE und _COMPANY_NAME_RE ergänzt,
Post-Processing-Schritt entfernt verbleibende Orphan-Suffixe.
"""

import unittest

from governance.redaction import prepare_for_llm, _ORPHAN_COMPANY_SUFFIX_RE


def _policy(company: bool = True, pseudonymize: bool = True) -> dict:
    return {
        "dictionary_entities": [],
        "dictionary_fuzzy": False,
        "dictionary_threshold": 92,
        "pseudonymize_people": pseudonymize,
        "redact_company_names": company,
    }


class GmbhCoKgVollstaendigMaskiertTests(unittest.TestCase):
    """GmbH & Co. KG muss vollständig als ein Platzhalter erscheinen."""

    def test_gmbh_co_kg_vollstaendig_maskiert(self):
        """'Sonnenschein GmbH & Co. KG' → kein Klartext-Suffix '& Co. KG' im Output."""
        text = "Sonnenschein GmbH & Co. KG, Frankfurt am Main."
        ctx = prepare_for_llm(text, _policy())
        self.assertNotIn("& Co. KG", ctx.redacted_text,
                         f"Suffix '& Co. KG' darf nicht im Klartext stehen: {ctx.redacted_text!r}")
        self.assertNotIn("Sonnenschein", ctx.redacted_text,
                         f"Firmenname darf nicht im Klartext stehen: {ctx.redacted_text!r}")

    def test_gmbh_co_kg_ohne_firma_company_flag(self):
        """GmbH & Co. KG wird auch ohne company_redaction korrekt maskiert (via pseudonymize)."""
        text = "Die Vertragspartei Sonnenschein GmbH & Co. KG überweist den Betrag."
        ctx = prepare_for_llm(text, _policy(company=False, pseudonymize=True))
        self.assertNotIn("& Co. KG", ctx.redacted_text,
                         f"Suffix '& Co. KG' darf nicht im Klartext stehen: {ctx.redacted_text!r}")


class GmbhCoKgaaVollstaendigMaskiertTests(unittest.TestCase):
    """GmbH & Co. KGaA muss vollständig als ein Platzhalter erscheinen."""

    def test_gmbh_co_kgaa_vollstaendig_maskiert(self):
        """'Alpha Holding GmbH & Co. KGaA' → kein Klartext-Suffix '& Co. KGaA'."""
        text = "Alpha Holding GmbH & Co. KGaA überweist den Kaufpreis."
        ctx = prepare_for_llm(text, _policy())
        self.assertNotIn("& Co. KGaA", ctx.redacted_text,
                         f"Suffix '& Co. KGaA' darf nicht im Klartext stehen: {ctx.redacted_text!r}")
        self.assertNotIn("Alpha Holding", ctx.redacted_text,
                         f"Firmenname darf nicht im Klartext stehen: {ctx.redacted_text!r}")

    def test_gmbh_co_kgaa_in_satzmitte(self):
        """'GmbH & Co. KGaA' in Satzmitte wird vollständig maskiert."""
        text = "Der Verkäufer ist Beta Verwaltung GmbH & Co. KGaA aus Frankfurt."
        ctx = prepare_for_llm(text, _policy())
        self.assertNotIn("GmbH & Co. KGaA", ctx.redacted_text,
                         f"Suffix darf nicht im Klartext stehen: {ctx.redacted_text!r}")

    def test_co_kgaa_standalone_maskiert(self):
        """'Beta Verwaltung Co. KGaA' (ohne GmbH-Präfix) wird maskiert."""
        text = "Beta Verwaltung Co. KGaA ist Gesellschafterin."
        ctx = prepare_for_llm(text, _policy())
        self.assertNotIn("Co. KGaA", ctx.redacted_text,
                         f"'Co. KGaA' darf nicht im Klartext stehen: {ctx.redacted_text!r}")


class SuffixCoNachPlaceholderEntferntTests(unittest.TestCase):
    """Der Post-Processing-Schritt muss Orphan-Suffixe nach Platzhaltern entfernen."""

    def test_suffix_co_nach_placeholder_entfernt(self):
        """'[ORG_1] & Co. KG' → Post-Processing entfernt '& Co. KG'."""
        text = "[ORG_1] & Co. KG ist die Verkäuferin."
        result = _ORPHAN_COMPANY_SUFFIX_RE.sub(r"\1", text)
        self.assertNotIn("& Co. KG", result,
                         f"Orphan-Suffix '& Co. KG' muss entfernt werden: {result!r}")
        self.assertIn("[ORG_1]", result,
                      f"Platzhalter [ORG_1] muss erhalten bleiben: {result!r}")

    def test_suffix_co_kgaa_nach_placeholder_entfernt(self):
        """'[PARTEI_1] & Co. KGaA' → Post-Processing entfernt '& Co. KGaA'."""
        text = "[PARTEI_1] & Co. KGaA überweist den Betrag."
        result = _ORPHAN_COMPANY_SUFFIX_RE.sub(r"\1", text)
        self.assertNotIn("& Co. KGaA", result,
                         f"Orphan-Suffix '& Co. KGaA' muss entfernt werden: {result!r}")
        self.assertIn("[PARTEI_1]", result,
                      f"Platzhalter [PARTEI_1] muss erhalten bleiben: {result!r}")

    def test_gmbh_co_kgaa_im_full_pipeline(self):
        """End-to-End: GmbH & Co. KGaA wird durch den vollen Pipeline korrekt maskiert."""
        text = (
            "RA Dr. Hoffmann & Partner vertritt die Gamma Immobilien GmbH & Co. KGaA.\n"
            "Die Gamma Immobilien GmbH & Co. KGaA hat ihren Sitz in Frankfurt.\n"
        )
        ctx = prepare_for_llm(text, _policy())
        self.assertNotIn("Gamma Immobilien", ctx.redacted_text,
                         f"Firmenname darf nicht im Klartext stehen: {ctx.redacted_text!r}")
        self.assertNotIn("& Co. KGaA", ctx.redacted_text,
                         f"Suffix '& Co. KGaA' darf nicht im Klartext stehen: {ctx.redacted_text!r}")

    def test_keine_kollateralschaeden_bei_co_kg(self):
        """Normaler 'Co.' in anderem Kontext wird nicht versehentlich entfernt."""
        # "Co." as abbreviation in a name, not as part of "& Co. KG" after placeholder
        text = "Herrmann Co. ist kein Firmenname mit KG-Suffix."
        ctx = prepare_for_llm(text, _policy())
        # Should not crash
        self.assertIsNotNone(ctx.redacted_text)
        self.assertIsInstance(ctx.redacted_text, str)


if __name__ == "__main__":
    unittest.main()
