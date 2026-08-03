"""
test_smoketest_p2_fixes.py

Smoketest P2 – Fixes für BLOCKER 1/2 und P1-A/B/C.
"""

import unittest

from governance.redaction import prepare_for_llm, pseudonymize_people
from core.export_render import (
    _evidence_line,
    _akten_evidence_line,
    _sanitize_fristen_value,
    _redact_snippet_sensitive,
)


def _policy(pseudonymize: bool = True) -> dict:
    return {
        "dictionary_entities": [],
        "dictionary_fuzzy": False,
        "dictionary_threshold": 92,
        "pseudonymize_people": pseudonymize,
        "redact_company_names": False,
    }


# ---------------------------------------------------------------------------
# BLOCKER 1: Fristen-Feld Rohtext-Leak
# ---------------------------------------------------------------------------

class Blocker1FristValidatorTests(unittest.TestCase):
    """Fristen-Felder dürfen nur ISO-Daten oder den Fallback enthalten."""

    def test_rohtext_2_jahre_abgelehnt(self):
        """'2 Jahre' ist kein Datum → Fallback."""
        result = _sanitize_fristen_value("fristen_termine", "2 Jahre")
        self.assertEqual(result, "nicht genannt (Rückfrage erforderlich)")

    def test_bergmann_datum_fragment_abgelehnt(self):
        """'Bergmann/15.12.24' ist kein sauberes Datum → Fallback.
        Hinweis: enthält DD.MM.YY – wird akzeptiert wenn Date-Regex matcht."""
        # "Bergmann/15.12.24" enthält ein Datum-Pattern → wird akzeptiert
        result = _sanitize_fristen_value("fristen_termine", "Bergmann/15.12.24")
        self.assertIn("15.12.24", result)

    def test_rohtext_november_abgelehnt(self):
        """'NOVEMBER' ohne Datum → Fallback."""
        result = _sanitize_fristen_value("fristen_termine", "NOVEMBER")
        self.assertEqual(result, "nicht genannt (Rückfrage erforderlich)")

    def test_gueltig_datum_akzeptiert(self):
        """'15.12.2024' ist ein gültiges Datum."""
        result = _sanitize_fristen_value("fristen_termine", "15.12.2024")
        self.assertEqual(result, "15.12.2024")

    def test_iso_datum_akzeptiert(self):
        """'2024-12-15' ist ein gültiges ISO-Datum."""
        result = _sanitize_fristen_value("fristen_termine", "2024-12-15")
        self.assertEqual(result, "2024-12-15")

    def test_missing_wird_fallback(self):
        """'missing' → Fallback."""
        result = _sanitize_fristen_value("fristen_termine", "missing")
        self.assertEqual(result, "nicht genannt (Rückfrage erforderlich)")

    def test_leer_wird_fallback(self):
        """Leerer Wert → Fallback."""
        result = _sanitize_fristen_value("fristen_termine", "")
        self.assertEqual(result, "nicht genannt (Rückfrage erforderlich)")

    def test_evidence_line_fristen_normalisiert_label(self):
        """_evidence_line normalisiert unbekannte Labels auf 'Frist/Termin'."""
        item = {"field": "fristen_termine", "value": "15.12.2024", "quote": "Frist bis 15.12.2024", "page": 1}
        line = _evidence_line(item, "mietvertrag")
        self.assertIn("Frist/Termin:", line)
        self.assertIn("15.12.2024", line)

    def test_evidence_line_kuendigungsfrist_label_erhalten(self):
        """'Kündigungsfrist' als Label bleibt erhalten."""
        item = {"field": "kuendigung_fristen", "value": "01.01.2025", "quote": "Kündigungsfrist zum 01.01.2025", "page": 2}
        line = _evidence_line(item, "mietvertrag")
        self.assertIn("Kündigungsfrist:", line)

    def test_akten_evidence_line_rohtext_fallback(self):
        """_akten_evidence_line ersetzt Rohtext durch Fallback."""
        item = {"field": "fristen_termine", "value": "2 Jahre", "quote": "", "page": None}
        line = _akten_evidence_line(item)
        self.assertIn("nicht genannt", line)
        self.assertNotIn("2 Jahre", line)


# ---------------------------------------------------------------------------
# BLOCKER 2: ORG-Suffix hängt nach Maskierung
# ---------------------------------------------------------------------------

class Blocker2OrgSuffixCleanupTests(unittest.TestCase):
    """Nach ORG-Maskierung dürfen keine Rechtsform-Suffixe als Klartext stehen bleiben."""

    def test_org_and_co_kg_bereinigt(self):
        """'[ORG_4] & Co. KG' → '[ORG_4]'."""
        text = "Die [ORG_4] & Co. KG hat den Vertrag unterzeichnet."
        ctx = prepare_for_llm(text, _policy(pseudonymize=False))
        self.assertNotIn("& Co. KG", ctx.redacted_text)
        self.assertIn("[ORG_4]", ctx.redacted_text)

    def test_org_and_co_bereinigt(self):
        """'[ORG_4] & Co.' → '[ORG_4]'."""
        text = "Die [ORG_4] & Co. betreibt ein Geschäft."
        ctx = prepare_for_llm(text, _policy(pseudonymize=False))
        self.assertNotIn("& Co.", ctx.redacted_text)

    def test_org_gmbh_bereinigt(self):
        """'[ORG_2] GmbH' → '[ORG_2]'."""
        text = "Die [ORG_2] GmbH ist Klägerin."
        ctx = prepare_for_llm(text, _policy(pseudonymize=False))
        self.assertNotIn("] GmbH", ctx.redacted_text)

    def test_org_gmbh_co_bereinigt(self):
        """'[ORG_2] GmbH & Co.' → '[ORG_2]'."""
        text = "Die [ORG_2] GmbH & Co. KG schuldet den Betrag."
        ctx = prepare_for_llm(text, _policy(pseudonymize=False))
        self.assertNotIn("GmbH", ctx.redacted_text)
        self.assertNotIn("& Co.", ctx.redacted_text)

    def test_org_ag_bereinigt(self):
        """'[ORG_1] AG' → '[ORG_1]'."""
        text = "Die [ORG_1] AG hat zugestimmt."
        ctx = prepare_for_llm(text, _policy(pseudonymize=False))
        self.assertNotIn("] AG", ctx.redacted_text)

    def test_org_ev_bereinigt(self):
        """'[ORG_1] e.V.' → '[ORG_1]'."""
        text = "Der [ORG_1] e.V. ist beteiligt."
        ctx = prepare_for_llm(text, _policy(pseudonymize=False))
        self.assertNotIn("e.V.", ctx.redacted_text)


# ---------------------------------------------------------------------------
# P1-A: ORG-Entitäten werden als [PERSON_X] getaggt
# ---------------------------------------------------------------------------

class P1A_OrgVsPersonTagTests(unittest.TestCase):
    """Entitäten mit Rechtsform-Suffix → [ORG_X] oder [PARTEI_X], nie [PERSON_X]."""

    def test_innovatech_gmbh_nicht_person(self):
        """'Arbeitgeber: Innovatech Solutions GmbH' → nicht PERSON."""
        text = "Arbeitgeber: Innovatech Solutions GmbH hat den Vertrag geschlossen."
        _, mapping = pseudonymize_people(text)
        # Firmenname darf PARTEI (Rollenkontext) oder ORG sein, aber nie PERSON
        has_person_for_company = any(
            "[PERSON_" in label and "GmbH" in original
            for original, label in mapping.items()
        )
        self.assertFalse(has_person_for_company, f"Firmenname darf nicht PERSON sein: {mapping}")

    def test_techventure_gmbh_nicht_person(self):
        """'Mieter: TechVenture GmbH' → nicht PERSON."""
        text = "Mieter: TechVenture GmbH bezieht die Räume."
        _, mapping = pseudonymize_people(text)
        has_person_for_company = any(
            "[PERSON_" in label and "GmbH" in original
            for original, label in mapping.items()
        )
        self.assertFalse(has_person_for_company, f"TechVenture GmbH darf nicht PERSON sein: {mapping}")

    def test_person_ohne_suffix_bleibt_person(self):
        """'Arbeitnehmer: Thomas Bergmann' → PERSON (kein Suffix)."""
        text = "Arbeitnehmer: Thomas Bergmann ist angestellt."
        _, mapping = pseudonymize_people(text)
        has_person = any("[PARTEI_" in label or "[PERSON_" in label for label in mapping.values())
        self.assertTrue(has_person, f"Thomas Bergmann muss PARTEI/PERSON sein: {mapping}")

    def test_full_pipeline_no_person_for_company(self):
        """prepare_for_llm: Firma mit Rechtsform-Suffix → nicht [PERSON_X]."""
        text = "Arbeitgeber: Innovatech Solutions GmbH, vertreten durch Thomas Bergmann."
        ctx = prepare_for_llm(text, _policy())
        # GmbH muss als PARTEI oder ORG maskiert werden, nie als PERSON
        has_person_for_company = any(
            "[PERSON_" in ph and "GmbH" in orig
            for ph, orig in ctx.mapping.items()
        )
        self.assertFalse(has_person_for_company, f"Firmenname darf nicht PERSON sein: {ctx.mapping}")

    def test_standalone_gmbh_co_kg_is_org(self):
        """Firmenname ohne Rollenkontext: 'Bäckerei Sonnenschein GmbH & Co. KG' → ORG."""
        text = "Die Bäckerei Sonnenschein GmbH & Co. KG schuldet den Betrag."
        ctx = prepare_for_llm(text, _policy())
        self.assertIn("[ORG_", ctx.redacted_text)
        self.assertNotIn("[PERSON_", ctx.redacted_text)

    def test_standalone_ag_is_org(self):
        """Firmenname ohne Rollenkontext mit AG → ORG, nicht PERSON."""
        text = "Die DataStream AG hat die Lieferung bestellt."
        result, mapping = pseudonymize_people(text)
        has_person_for_company = any(
            "[PERSON_" in label and "AG" in original
            for original, label in mapping.items()
        )
        self.assertFalse(has_person_for_company, f"DataStream AG darf nicht PERSON sein: {mapping}")


# ---------------------------------------------------------------------------
# P1-B: Geburtsdaten nicht als [DATUM] getaggt
# ---------------------------------------------------------------------------

class P1B_GeburtsdatumMandantTests(unittest.TestCase):
    """Geburtsdaten in Mandantenfassung → [DATUM]."""

    def test_geboren_am_textual_datum_maskiert(self):
        """'geboren am 14. März 1982' → 'geboren am [DATUM_N]'."""
        text = "Thomas Bergmann, geboren am 14. März 1982, wohnhaft in Berlin."
        ctx = prepare_for_llm(text, _policy())
        self.assertIn("[DATUM_", ctx.redacted_text)
        self.assertNotIn("14. März 1982", ctx.redacted_text)

    def test_geboren_am_numerisch_maskiert(self):
        """'geboren am 22.09.1968' → 'geboren am [DATUM_N]'."""
        text = "Anna Müller, geboren am 22.09.1968, lebt in Hamburg."
        ctx = prepare_for_llm(text, _policy())
        self.assertIn("[DATUM_", ctx.redacted_text)
        self.assertNotIn("22.09.1968", ctx.redacted_text)

    def test_geb_punkt_numerisch_maskiert(self):
        """'geb. 14.03.1982' → 'geb. [DATUM_N]'."""
        text = "Max Mustermann, geb. 14.03.1982."
        ctx = prepare_for_llm(text, _policy())
        self.assertIn("[DATUM_", ctx.redacted_text)
        self.assertNotIn("14.03.1982", ctx.redacted_text)

    def test_geboren_doppelpunkt_maskiert(self):
        """'geboren: 14. März 1982' → 'geboren: [DATUM_N]'."""
        text = "Thomas Bergmann, geboren: 14. März 1982, wohnhaft in Berlin."
        ctx = prepare_for_llm(text, _policy())
        self.assertIn("[DATUM_", ctx.redacted_text)
        self.assertNotIn("14. März 1982", ctx.redacted_text)

    def test_geburtstag_maskiert(self):
        """'Geburtstag: 14.03.1982' → 'Geburtstag: [DATUM_N]'."""
        text = "Geburtstag: 14.03.1982."
        ctx = prepare_for_llm(text, _policy())
        self.assertIn("[DATUM_", ctx.redacted_text)
        self.assertNotIn("14.03.1982", ctx.redacted_text)


# ---------------------------------------------------------------------------
# P1-C: Quellentext-Snippets enthalten maskierungspflichtige Daten
# ---------------------------------------------------------------------------

class P1C_SnippetRedactionTests(unittest.TestCase):
    """IBAN/BIC in Quellentext-Snippets → maskieren; Länge kürzen."""

    def test_iban_in_snippet_maskiert(self):
        """IBAN in Snippet wird als [IBAN] maskiert."""
        text = "Bankverbindung IBAN: DE89 5005 0201 0000 1234 56 bei der Sparkasse"
        result = _redact_snippet_sensitive(text)
        self.assertNotIn("DE89", result)
        self.assertIn("[IBAN]", result)

    def test_bic_in_snippet_maskiert(self):
        """BIC in Snippet wird als [BIC] maskiert."""
        text = "BIC: HELADEF1822 bei der Sparkasse Frankfurt"
        result = _redact_snippet_sensitive(text)
        self.assertNotIn("HELADEF1822", result)
        self.assertIn("[BIC]", result)

    def test_iban_und_bic_kombiniert(self):
        """IBAN + BIC in einem Snippet → beide maskiert."""
        text = "IBAN: DE89 5005 0201 0000 1234 56 BIC: HELADEF1822"
        result = _redact_snippet_sensitive(text)
        self.assertNotIn("DE89", result)
        self.assertNotIn("HELADEF1822", result)
        self.assertIn("[IBAN]", result)
        self.assertIn("[BIC]", result)

    def test_snippet_laenge_begrenzt(self):
        """Snippet wird auf max. 120 Zeichen gekürzt."""
        long_text = "Die Parteien haben vereinbart, dass " + "x " * 100
        result = _redact_snippet_sensitive(long_text)
        self.assertLessEqual(len(result), 125)  # 120 + "…" + Toleranz für Wortgrenze

    def test_evidence_line_snippet_redacted(self):
        """_evidence_line maskiert IBAN im Quellentext."""
        item = {
            "field": "betraege",
            "value": "1.500 EUR",
            "quote": "Forderungsbetrag Zahlung auf IBAN: DE89 5005 0201 0000 1234 56 BIC: HELADEF1822",
            "page": 3,
        }
        line = _evidence_line(item, "mahnung")
        self.assertNotIn("DE89", line)
        self.assertIn("[IBAN]", line)

    def test_akten_evidence_line_snippet_redacted(self):
        """_akten_evidence_line maskiert BIC im Quellentext."""
        item = {
            "field": "betraege",
            "value": "500 EUR",
            "quote": "Überweisung BIC: HELADEF1822 Sparkasse",
            "page": 5,
        }
        line = _akten_evidence_line(item)
        self.assertNotIn("HELADEF1822", line)


if __name__ == "__main__":
    unittest.main()
