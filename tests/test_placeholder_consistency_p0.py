"""
test_placeholder_consistency_p0.py

FIX 1 (P0): Inconsistent placeholder mapping – Hard Gate Crash
Root cause: _apply_regex_redaction erzeugt [ORG_1], [ORG_2] für RA-Kanzleien,
dann startet pseudonymize_people() _org_counter bei 0 → ebenfalls [ORG_1] für
eine andere Firma → Collision beim _merge_placeholder_mapping → ValueError.
"""

import unittest

from governance.redaction import prepare_for_llm


def _policy(pseudonymize: bool = True) -> dict:
    return {
        "dictionary_entities": [],
        "dictionary_fuzzy": False,
        "dictionary_threshold": 92,
        "pseudonymize_people": pseudonymize,
        "redact_company_names": False,
    }


class PlaceholderConsistencyVerschachtelteEntitiesTests(unittest.TestCase):
    """Gesellschaftsrecht-ähnliche Dokumente mit RA-Kanzleien + Standalone-Firmen
    dürfen keinen Hard Gate / ValueError auslösen."""

    def test_placeholder_consistency_verschachtelte_entities(self):
        """Dokument mit RA & Partner + GmbH + HRB + IBAN darf nicht crashen."""
        text = (
            "Notarielle Urkunde UR-Nr. 0234/2025\n"
            "Verkäuferin: Dr. Ingrid Pfeiffer\n"
            "Käuferin: NordCapital Beteiligungen GmbH\n"
            "Anwalt Verkäuferin: RA Dr. Hoffmann & Partner\n"
            "Anwalt Käuferin: RA Schöne & Partner\n"
            "TechVenture GmbH, eingetragen HRB 112233, Frankfurt\n"
            "Bankverbindung: IBAN DE89 5005 0201 0000 1234 56 BIC HELADEF1822\n"
            "Die Käuferin NordCapital Beteiligungen GmbH überweist den Kaufpreis.\n"
        )
        # Must not raise ValueError("Inconsistent placeholder mapping detected")
        ctx = prepare_for_llm(text, _policy())
        self.assertIsNotNone(ctx.redacted_text)
        # ORG placeholders must be consistent: no same [ORG_N] for two different originals
        mapping = ctx.mapping
        org_entries = {k: v for k, v in mapping.items() if k.startswith("[ORG_")}
        # All ORG keys must be unique
        self.assertEqual(len(org_entries), len(set(org_entries.keys())))

    def test_placeholder_consistency_no_value_error(self):
        """Direkter Test: ValueError darf bei komplexen Dokumenten nicht auftreten."""
        text = (
            "Auftraggeber: RA Dr. Müller & Partner\n"
            "Auftragnehmer: RA Schmidt & Co. KG\n"
            "Vertragsgegenstand: Beratung der Sonnenschein GmbH & Co. KG\n"
            "Sonnenschein GmbH & Co. KG, HRB 99887, München\n"
        )
        try:
            ctx = prepare_for_llm(text, _policy())
        except ValueError as exc:
            self.fail(f"ValueError wurde unerwartet ausgelöst: {exc}")

    def test_org_counter_does_not_restart_at_one(self):
        """Nach RA-ORG-Regex-Regeln (z.B. [ORG_1], [ORG_2]) muss der Standalone-
        ORG-Counter bei [ORG_3] weiterlaufen, nicht wieder bei [ORG_1] beginnen."""
        text = (
            "Kanzlei: RA Dr. Hoffmann & Partner\n"
            "Zweite Kanzlei: RA Schöne & Partner\n"
            "Mandant: DataStream AG, Frankfurt am Main\n"
        )
        ctx = prepare_for_llm(text, _policy())
        # [ORG_1] and [ORG_2] from regex rules (RA firms)
        # [ORG_3] should be DataStream AG from pseudonymization
        self.assertNotIn("[ORG_1]", {k: v for k, v in ctx.mapping.items() if v == "DataStream AG"}.keys(),
                         f"DataStream AG darf nicht [ORG_1] erhalten (Collision): {ctx.mapping}")


class PlaceholderConsistencyHrbPlusIbanTests(unittest.TestCase):
    """HRB + IBAN + BIC in Kombination dürfen keine Mapping-Kollision erzeugen."""

    def test_placeholder_consistency_hrb_plus_iban(self):
        """HRB + IBAN + BIC-Kombination: kein Crash, alle Platzhalter eindeutig."""
        text = (
            "NordCapital Beteiligungen GmbH, HRB 112233, Frankfurt\n"
            "Sonnenschein Bäckerei GmbH, HRB 445566, Hamburg\n"
            "Bankverbindung 1: IBAN DE89 5005 0201 0000 1234 56\n"
            "Bankverbindung 2: IBAN DE44 5001 0517 5407 3249 31\n"
            "BIC: HELADEF1822\n"
        )
        ctx = prepare_for_llm(text, _policy(pseudonymize=False))
        self.assertIn("[HRB_1]", ctx.redacted_text, "HRB 112233 muss maskiert sein")
        self.assertIn("[HRB_2]", ctx.redacted_text, "HRB 445566 muss maskiert sein")
        self.assertIn("[IBAN_", ctx.redacted_text, "IBAN muss maskiert sein")
        self.assertIn("[BIC_", ctx.redacted_text, "BIC muss maskiert sein")
        # No mapping collision (all keys unique)
        self.assertEqual(len(ctx.mapping), len(set(ctx.mapping.keys())))


class PlaceholderConsistencyPersonAlsOrgGfTests(unittest.TestCase):
    """Person die sowohl als natürliche Person als auch als GF einer GmbH erscheint."""

    def test_placeholder_consistency_person_als_org_gf(self):
        """Dr. Ingrid Pfeiffer als Käuferin UND als GF der NordCapital GmbH:
        kein Mapping-Crash, Person bekommt konsistenten Platzhalter."""
        text = (
            "Verkäuferin: Dr. Ingrid Pfeiffer\n"
            "Käuferin: NordCapital Beteiligungen GmbH\n"
            "Geschäftsführerin der NordCapital Beteiligungen GmbH: Dr. Ingrid Pfeiffer\n"
            "Dr. Ingrid Pfeiffer ist zugleich Gesellschafterin.\n"
            "Anwalt: RA Dr. Hoffmann & Partner\n"
            "RA Schöne & Partner\n"
            "TechVenture GmbH, HRB 555123\n"
        )
        try:
            ctx = prepare_for_llm(text, _policy())
        except ValueError as exc:
            self.fail(f"ValueError für Person-als-ORG-GF: {exc}")
        # Dr. Ingrid Pfeiffer muss konsistent maskiert sein
        self.assertNotIn("Ingrid Pfeiffer", ctx.redacted_text,
                         f"Dr. Ingrid Pfeiffer darf nicht im Klartext stehen: {ctx.redacted_text!r}")


if __name__ == "__main__":
    unittest.main()
