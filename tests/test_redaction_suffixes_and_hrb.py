"""
test_redaction_suffixes_and_hrb.py

Bug 1 (P1): Rechtsform-Suffixe bei Unternehmensredaktion nicht vollständig maskiert.
  - 'Bäckerei Sonnenschein GmbH & Co. KG' → [ORG_1] statt [PERSON_1] & Co. KG
  - 'RA Dr. Hoffmann & Partner' → [ORG_1] statt [PERSON_1] & Partner

Bug 2 (P1): HRB-Nummern werden in der Mandantenfassung nicht maskiert.
  - 'HRB 445566' → [HRB_1]
  - 'HRA 12345' → [HRB_1]
"""

import unittest

from governance.redaction import prepare_for_llm, restore_from_llm, pseudonymize_people


# ---------------------------------------------------------------------------
# Bug 1: Rechtsform-Suffixe vollständig maskieren
# ---------------------------------------------------------------------------

class Bug1_StandaloneCompanySuffixTests(unittest.TestCase):
    """Unternehmensname ohne Rollenkontext: Suffix muss in [ORG_N] einbezogen sein."""

    def _policy(self, pseudonymize: bool = True) -> dict:
        return {
            "dictionary_entities": [],
            "dictionary_fuzzy": False,
            "dictionary_threshold": 92,
            "pseudonymize_people": pseudonymize,
            "redact_company_names": False,
        }

    # -- GmbH & Co. KG -------------------------------------------------------

    def test_gmbh_co_kg_no_cleartext_suffix(self):
        """'Bäckerei Sonnenschein GmbH & Co. KG' darf kein '& Co. KG' im Klartext lassen."""
        text = "Die Bäckerei Sonnenschein GmbH & Co. KG hat den Vertrag unterzeichnet."
        ctx = prepare_for_llm(text, self._policy())
        self.assertNotIn(
            "& Co. KG",
            ctx.redacted_text,
            f"Suffix '& Co. KG' darf nicht im Klartext bleiben: {ctx.redacted_text!r}",
        )

    def test_gmbh_co_kg_uses_org_placeholder(self):
        """'Bäckerei Sonnenschein GmbH & Co. KG' soll [ORG_N] als Platzhalter erhalten."""
        text = "Bäckerei Sonnenschein GmbH & Co. KG schuldet den Betrag."
        ctx = prepare_for_llm(text, self._policy())
        self.assertIn(
            "[ORG_",
            ctx.redacted_text,
            f"Unternehmensname mit GmbH & Co. KG muss [ORG_N] erhalten: {ctx.redacted_text!r}",
        )

    def test_gmbh_co_kg_in_mapping(self):
        """Der Originalname (inkl. Suffix) muss im Mapping hinterlegt sein."""
        text = "Bäckerei Sonnenschein GmbH & Co. KG ist Partei."
        ctx = prepare_for_llm(text, self._policy())
        self.assertIn(
            "Bäckerei Sonnenschein GmbH & Co. KG",
            ctx.mapping.values(),
            f"Vollständiger Firmenname muss im Mapping stehen: {dict(ctx.mapping)!r}",
        )

    # -- & Partner -----------------------------------------------------------

    def test_ra_and_partner_no_cleartext_suffix(self):
        """'RA Dr. Hoffmann & Partner' darf kein '& Partner' im Klartext lassen."""
        text = "Vertreten wird die Klägerin durch RA Dr. Hoffmann & Partner."
        ctx = prepare_for_llm(text, self._policy())
        self.assertNotIn(
            "& Partner",
            ctx.redacted_text,
            f"Suffix '& Partner' darf nicht im Klartext bleiben: {ctx.redacted_text!r}",
        )

    def test_ra_and_partner_uses_org_placeholder(self):
        """'RA Dr. Hoffmann & Partner' → [ORG_N], nicht [PERSON_N] + Suffix."""
        text = "Rechtsanwalt Müller & Partner betreut den Fall."
        ctx = prepare_for_llm(text, self._policy())
        self.assertIn(
            "[ORG_",
            ctx.redacted_text,
            f"Kanzleiname mit & Partner muss [ORG_N] erhalten: {ctx.redacted_text!r}",
        )

    def test_ra_and_partner_full_text_masked(self):
        """'RA Dr. Hoffmann & Partner' komplett maskiert, kein Namensteil im Klartext."""
        text = "RA Dr. Hoffmann & Partner, Anwaltskanzlei Hamburg"
        ctx = prepare_for_llm(text, self._policy())
        self.assertNotIn(
            "Hoffmann",
            ctx.redacted_text,
            f"Nachname darf nicht im Klartext bleiben: {ctx.redacted_text!r}",
        )

    # -- Weitere Suffixe -----------------------------------------------------

    def test_partnerschaft_mbh_masked(self):
        """'Müller Schreiber PartG mbB' → kein Klartextname."""
        text = "Beauftrage Kanzlei: Müller Schreiber PartG mbB"
        ctx = prepare_for_llm(text, self._policy())
        self.assertNotIn(
            "Müller Schreiber",
            ctx.redacted_text,
            f"PartG mbB-Kanzlei darf nicht im Klartext bleiben: {ctx.redacted_text!r}",
        )

    def test_no_person_n_before_legal_suffix(self):
        """Nach Redaktion darf '[PERSON_N] & Co. KG' nicht im Text erscheinen."""
        text = "Sonnenschein Bäckerei GmbH & Co. KG ist Mieterin."
        ctx = prepare_for_llm(text, self._policy())
        import re
        self.assertNotRegex(
            ctx.redacted_text,
            r"\[PERSON_\d+\]\s*&\s*Co\.",
            f"'[PERSON_N] & Co.' darf nicht entstehen: {ctx.redacted_text!r}",
        )

    def test_no_person_n_before_partner_suffix(self):
        """Nach Redaktion darf '[PERSON_N] & Partner' nicht im Text erscheinen."""
        text = "Beauftragt: RA Dr. Meier & Partner Rechtsanwälte"
        ctx = prepare_for_llm(text, self._policy())
        import re
        self.assertNotRegex(
            ctx.redacted_text,
            r"\[PERSON_\d+\]\s*&\s*Partner",
            f"'[PERSON_N] & Partner' darf nicht entstehen: {ctx.redacted_text!r}",
        )

    # -- Keine Auswirkung wenn pseudonymize_people=False ---------------------

    def test_no_org_when_pseudonymize_disabled(self):
        """Wenn pseudonymize_people=False, kein [ORG_N] durch Standalone-ORG-Detection."""
        text = "Bäckerei Sonnenschein GmbH & Co. KG"
        ctx = prepare_for_llm(text, self._policy(pseudonymize=False))
        # Kein ORG-Platzhalter durch Pseudonymisierungs-Pfad
        # (kann [FIRMA_N] haben wenn redact_company_names=True, aber das ist hier False)
        firma_or_org = [k for k in ctx.mapping if "ORG" in k or "FIRMA" in k]
        self.assertEqual(
            firma_or_org, [],
            f"Ohne Pseudonymisierung darf kein ORG/FIRMA-Platzhalter entstehen: {ctx.mapping!r}",
        )

    # -- Roundtrip -----------------------------------------------------------

    def test_org_roundtrip_restores_original(self):
        """[ORG_N]-Platzhalter muss durch restore_from_llm wiederhergestellt werden."""
        text = "Bäckerei Sonnenschein GmbH & Co. KG hat gezahlt."
        ctx = prepare_for_llm(text, self._policy())
        restored = restore_from_llm(ctx.redacted_text, ctx.mapping)
        self.assertIn(
            "Bäckerei Sonnenschein GmbH & Co. KG",
            restored,
            f"Originalname muss nach Restore erscheinen: {restored!r}",
        )

    # -- Kein Einfluss auf rollenbasierte Partei-Zuweisung -------------------

    def test_role_context_company_still_gets_partei_label(self):
        """'Auftraggeber: Nordstadt GmbH' → [PARTEI_1], nicht [ORG_N]."""
        text = "Auftraggeber: Nordstadt GmbH\nAuftragnehmer: Hans Meier"
        result, mapping = pseudonymize_people(text)
        self.assertIn("[PARTEI_1]", result, "Auftraggeber-Partei muss [PARTEI_1] erhalten")
        self.assertIn("[PARTEI_2]", result, "Auftragnehmer-Partei muss [PARTEI_2] erhalten")


class Bug1_CompanyNameRegexExtensionTests(unittest.TestCase):
    """Wenn redact_company_names=True, müssen erweiterte Suffixe von _COMPANY_NAME_RE erfasst sein."""

    def _policy_redact(self) -> dict:
        return {
            "dictionary_entities": [],
            "dictionary_fuzzy": False,
            "dictionary_threshold": 92,
            "pseudonymize_people": False,
            "redact_company_names": True,
        }

    def test_co_kg_masked_as_firma(self):
        """'Müller & Co. KG' muss als [FIRMA_N] maskiert werden."""
        text = "Partner: Müller & Co. KG"
        ctx = prepare_for_llm(text, self._policy_redact())
        self.assertNotIn(
            "Müller & Co. KG",
            ctx.redacted_text,
            f"'Müller & Co. KG' darf nicht im Klartext stehen: {ctx.redacted_text!r}",
        )
        self.assertIn("[FIRMA_", ctx.redacted_text)

    def test_and_partner_masked_as_firma(self):
        """'Schmidt & Partner' (Kanzlei) muss als [FIRMA_N] maskiert werden."""
        text = "Kanzlei: Schmidt & Partner"
        ctx = prepare_for_llm(text, self._policy_redact())
        self.assertNotIn(
            "& Partner",
            ctx.redacted_text,
            f"'& Partner' darf nicht im Klartext bleiben: {ctx.redacted_text!r}",
        )

    def test_partg_mbb_masked_as_firma(self):
        """'Berger Schreiber PartG mbB' muss als [FIRMA_N] maskiert werden."""
        text = "Auftraggeber ist Berger Schreiber PartG mbB."
        ctx = prepare_for_llm(text, self._policy_redact())
        self.assertNotIn(
            "Berger Schreiber",
            ctx.redacted_text,
            f"PartG mbB-Firma darf nicht im Klartext stehen: {ctx.redacted_text!r}",
        )


# ---------------------------------------------------------------------------
# Bug 2: HRB-Nummern maskieren
# ---------------------------------------------------------------------------

class Bug2_HrbMaskingTests(unittest.TestCase):
    """HRB-Nummern müssen in der Mandantenfassung maskiert sein."""

    def _policy(self) -> dict:
        return {
            "dictionary_entities": [],
            "dictionary_fuzzy": False,
            "dictionary_threshold": 92,
            "pseudonymize_people": False,
        }

    def test_hrb_is_masked(self):
        """'HRB 445566' muss als [HRB_N] maskiert werden."""
        text = "NordCapital Beteiligungen GmbH, HRB 445566, Hamburg"
        ctx = prepare_for_llm(text, self._policy())
        self.assertNotIn(
            "445566",
            ctx.redacted_text,
            f"HRB-Nummer darf nicht im Klartext bleiben: {ctx.redacted_text!r}",
        )
        self.assertIn("[HRB_", ctx.redacted_text, "HRB-Platzhalter muss erscheinen")

    def test_hra_is_masked(self):
        """'HRA 12345' (offene Handelsgesellschaft) muss maskiert werden."""
        text = "Handelsregister: HRA 12345 beim AG Hamburg."
        ctx = prepare_for_llm(text, self._policy())
        self.assertNotIn(
            "12345",
            ctx.redacted_text,
            f"HRA-Nummer darf nicht im Klartext bleiben: {ctx.redacted_text!r}",
        )
        self.assertIn("[HRB_", ctx.redacted_text)

    def test_vr_is_masked(self):
        """'VR 5678' (Vereinsregister) muss maskiert werden."""
        text = "Eintrag unter VR 5678 im Vereinsregister."
        ctx = prepare_for_llm(text, self._policy())
        self.assertNotIn(
            "5678",
            ctx.redacted_text,
            f"VR-Nummer darf nicht im Klartext bleiben: {ctx.redacted_text!r}",
        )

    def test_pr_is_masked(self):
        """'PR 9900' (Partnerschaftsregister) muss maskiert werden."""
        text = "Kanzlei eingetragen unter PR 9900."
        ctx = prepare_for_llm(text, self._policy())
        self.assertNotIn(
            "9900",
            ctx.redacted_text,
            f"PR-Nummer darf nicht im Klartext bleiben: {ctx.redacted_text!r}",
        )

    def test_hrb_in_mapping(self):
        """HRB-Originalwert muss im Mapping stehen."""
        text = "HRB 445566"
        ctx = prepare_for_llm(text, self._policy())
        self.assertIn(
            "HRB 445566",
            ctx.mapping.values(),
            f"HRB-Original muss im Mapping stehen: {dict(ctx.mapping)!r}",
        )

    def test_hrb_in_full_sentence_masked(self):
        """Vollständiger Satz mit Firmenname, HRB und Ort: HRB-Nummer maskiert."""
        text = "NordCapital Beteiligungen GmbH, HRB 445566, Hamburg"
        ctx = prepare_for_llm(text, self._policy())
        self.assertNotIn("445566", ctx.redacted_text)

    def test_hrb_roundtrip(self):
        """[HRB_N] muss durch restore_from_llm wiederhergestellt werden."""
        text = "Handelsregisternummer: HRB 112233"
        ctx = prepare_for_llm(text, self._policy())
        restored = restore_from_llm(ctx.redacted_text, ctx.mapping)
        self.assertIn(
            "HRB 112233",
            restored,
            f"HRB-Nummer muss nach Restore erscheinen: {restored!r}",
        )

    def test_same_hrb_gets_same_placeholder(self):
        """Dieselbe HRB-Nummer im Text bekommt denselben Platzhalter."""
        text = "HRB 445566 wurde geprüft. Nochmals: HRB 445566."
        ctx = prepare_for_llm(text, self._policy())
        count = ctx.redacted_text.count("[HRB_1]")
        self.assertEqual(count, 2, f"Gleiche HRB-Nummer muss gleichen Platzhalter erhalten: {ctx.redacted_text!r}")

    def test_regular_number_after_text_not_masked(self):
        """Beliebige Zahlen ohne HRB/HRA/VR/PR-Prefix werden nicht als HRB maskiert."""
        text = "Vertragsnummer 445566 vom 01.01.2024."
        ctx = prepare_for_llm(text, self._policy())
        self.assertNotIn(
            "[HRB_",
            ctx.redacted_text,
            f"Zahl ohne Register-Prefix darf nicht als HRB maskiert werden: {ctx.redacted_text!r}",
        )

    def test_iban_still_masked_when_hrb_added(self):
        """IBAN muss weiterhin maskiert sein nachdem HRB-Regel hinzugefügt wurde."""
        text = "IBAN: DE89 5005 0201 0000 1234 56, eingetragen HRB 445566"
        ctx = prepare_for_llm(text, self._policy())
        self.assertNotIn("DE89", ctx.redacted_text, "IBAN darf nicht im Klartext bleiben")
        self.assertIn("[IBAN_", ctx.redacted_text)
        self.assertIn("[HRB_", ctx.redacted_text)


if __name__ == "__main__":
    unittest.main()
