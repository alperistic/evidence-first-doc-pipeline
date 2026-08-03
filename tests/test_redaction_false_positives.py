"""
test_redaction_false_positives.py

Tests that common legal terms, table headers, contract boilerplate,
and footer text are NOT falsely recognised as person entities.
"""

import unittest

from governance.redaction import (
    pseudonymize_people,
    _is_person_candidate,
)


class FalsePositiveTableHeaderTests(unittest.TestCase):
    """Table-header terms like 'Rate', 'Fälligkeit', 'Betrag' must not be tagged."""

    def test_rate_faelligkeit_betrag_not_person(self):
        text = "Rate  Fälligkeit  Betrag\n1  01.01.2025  500,00 EUR"
        result, mapping = pseudonymize_people(text)
        self.assertEqual(mapping, {}, f"Unexpected persons found: {mapping}")
        self.assertEqual(result, text)

    def test_verzug_ratenplan_not_person(self):
        text = "Bei Verzug greift der Ratenplan gemäß §3."
        result, mapping = pseudonymize_people(text)
        self.assertEqual(mapping, {})
        self.assertEqual(result, text)


class FalsePositiveBoilerplateTests(unittest.TestCase):
    """Footer boilerplate like 'Fiktives Testdokument' must not be tagged."""

    def test_fiktives_testdokument_not_person(self):
        text = "Fiktives Testdokument – nur zur Systemprüfung"
        result, mapping = pseudonymize_people(text)
        self.assertEqual(mapping, {}, f"Unexpected persons found: {mapping}")
        self.assertEqual(result, text)

    def test_nur_zur_systempruefung_not_person(self):
        text = "Dieses Dokument dient nur zur Systemprüfung."
        result, mapping = pseudonymize_people(text)
        self.assertEqual(mapping, {})


class FalsePositiveAllUppercaseTests(unittest.TestCase):
    """Lines consisting only of uppercase letters/digits/special chars are not names."""

    def test_all_uppercase_line_not_person(self):
        self.assertFalse(
            _is_person_candidate("RATE FÄLLIGKEIT BETRAG", 0, 22, "RATE FÄLLIGKEIT BETRAG")
        )

    def test_aktenzeichen_fragment_not_person(self):
        self.assertFalse(
            _is_person_candidate("AZ 123/2024-B", 0, 13, "AZ 123/2024-B")
        )


class FalsePositiveStoplistTests(unittest.TestCase):
    """Stoplist terms must be rejected by _is_person_candidate."""

    def test_stoplist_fiktives_testdokument(self):
        text = "Fiktives Testdokument"
        self.assertFalse(
            _is_person_candidate(text, 0, len(text), text)
        )

    def test_stoplist_vertragliche_vereinbarung(self):
        text = "Vertragliche Vereinbarung"
        self.assertFalse(
            _is_person_candidate(text, 0, len(text), text)
        )

    def test_stoplist_rechtliche_begruendung(self):
        text = "Rechtliche Begründung"
        self.assertFalse(
            _is_person_candidate(text, 0, len(text), text)
        )

    def test_stoplist_besondere_vereinbarungen(self):
        text = "Besondere Vereinbarungen"
        self.assertFalse(
            _is_person_candidate(text, 0, len(text), text)
        )


class RealPersonStillDetectedTests(unittest.TestCase):
    """Ensure real person names are still detected after the false-positive fixes."""

    def test_real_person_with_role_context(self):
        text = "Kläger: Max Mustermann hat den Vertrag unterzeichnet."
        result, mapping = pseudonymize_people(text)
        self.assertTrue(len(mapping) > 0, "Real person should be detected")
        self.assertNotIn("Max Mustermann", result)

    def test_real_person_dr_title(self):
        text = "Beklagte: Dr. Anna Schmidt wurde verurteilt."
        result, mapping = pseudonymize_people(text)
        self.assertTrue(len(mapping) > 0, "Dr. Anna Schmidt should be detected")
        self.assertNotIn("Anna Schmidt", result)


class ExtendedRoleLabelTests(unittest.TestCase):
    """Names after extended role labels must be pseudonymized (Fix 1)."""

    _ROLE_TEXTS = [
        ("Arbeitnehmer: Lara Neumann", "Lara Neumann"),
        ("Arbeitnehmerin: Lara Neumann", "Lara Neumann"),
        ("Arbeitgeber: Hans Meier", "Hans Meier"),
        ("Gläubiger: Lara Neumann", "Lara Neumann"),
        ("Gläubigerin: Lara Neumann", "Lara Neumann"),
        ("Schuldner: Lara Neumann", "Lara Neumann"),
        ("Schuldnerin: Lara Neumann", "Lara Neumann"),
        ("Mandant: Lara Neumann", "Lara Neumann"),
        ("Mandantin: Lara Neumann", "Lara Neumann"),
        ("Auftraggeber: Lara Neumann", "Lara Neumann"),
        ("Auftragnehmer: Lara Neumann", "Lara Neumann"),
        ("Antragsteller: Lara Neumann", "Lara Neumann"),
        ("Antragsgegner: Lara Neumann", "Lara Neumann"),
        ("Darlehensnehmer: Lara Neumann", "Lara Neumann"),
        ("Darlehensgeber: Lara Neumann", "Lara Neumann"),
        ("Versicherungsnehmer: Lara Neumann", "Lara Neumann"),
        ("Gesellschafter: Lara Neumann", "Lara Neumann"),
        ("Geschäftsführer: Lara Neumann", "Lara Neumann"),
        ("Bürge: Lara Neumann", "Lara Neumann"),
    ]

    def test_extended_roles_pseudonymize_name(self):
        for text, name in self._ROLE_TEXTS:
            with self.subTest(text=text):
                result, mapping = pseudonymize_people(text)
                self.assertNotIn(name, result, f"Name leaked in: {text} -> {result}")
                self.assertTrue(len(mapping) > 0, f"No mapping for: {text}")


class PR9AddressLeakTests(unittest.TestCase):
    """Fix A: Adress-Leaks – komplette Adresse als eine Einheit maskieren."""

    def test_durlacher_allee_fully_masked(self):
        from governance.redaction import prepare_for_llm

        text = "Wohnsitz: Durlacher Allee 72, 76131 Karlsruhe"
        ctx = prepare_for_llm(text, {"pseudonymize_people": False})
        self.assertNotIn("Durlacher Allee 72", ctx.redacted_text, "Straßenname darf nicht im Klartext bleiben")
        self.assertNotIn("76131", ctx.redacted_text, "PLZ darf nicht im Klartext bleiben")

    def test_auftraggeber_and_auftragnehmer_both_masked(self):
        text = "Auftraggeber: Hans Meier\nAuftragnehmer: Lara Neumann"
        result, mapping = pseudonymize_people(text)
        self.assertNotIn("Hans Meier", result, "Auftraggeber-Name muss maskiert sein")
        self.assertNotIn("Lara Neumann", result, "Auftragnehmer-Name muss maskiert sein")
        self.assertGreaterEqual(len(mapping), 2, "Beide Parteien müssen im Mapping erscheinen")

    def test_projektbezogene_tagessaetze_not_masked(self):
        text = "Die Vergütung umfasst projektbezogene Tagessätze gemäß Anlage."
        result, mapping = pseudonymize_people(text)
        self.assertIn("projektbezogene Tagessätze", result, "Fachbegriff darf nicht maskiert werden")
        self.assertEqual(mapping, {}, f"Keine Person erwartet: {mapping}")

    def test_lara_neumann_masked(self):
        text = "Auftragnehmer: Lara Neumann"
        result, mapping = pseudonymize_people(text)
        self.assertNotIn("Lara Neumann", result)
        self.assertTrue(len(mapping) > 0)


class PR10JobTitelParteikonsistenzTests(unittest.TestCase):
    """Fix A': Jobtitel-Ausnahmen und konsistente Partei-Maskierung.

    Root Cause: _COMPOUND_TERM_SUFFIXES enthielt keine Berufsbezeichnungen,
    daher wurden Zwei-Wort-Jobtitel wie 'Software Engineer' als Person erkannt.
    """

    def test_software_engineer_not_masked(self):
        """'Software Engineer' darf NICHT als PERSON maskiert werden."""
        text = "Die Arbeitnehmerin wird als Software Engineer eingestellt."
        result, mapping = pseudonymize_people(text)
        self.assertIn(
            "Software Engineer", result,
            f"Jobtitel 'Software Engineer' muss im Klartext bleiben, war: {result!r}",
        )

    def test_geschaeftsfuehrer_single_word_not_masked(self):
        """'Geschäftsführer' als Einzelwort darf NICHT als PERSON maskiert werden."""
        text = "Die Stelle als Geschäftsführer ist vakant."
        result, mapping = pseudonymize_people(text)
        self.assertIn(
            "Geschäftsführer", result,
            f"Jobtitel 'Geschäftsführer' muss im Klartext bleiben, war: {result!r}",
        )

    def test_auftraggeber_auftragnehmer_both_masked_konsistent(self):
        """Auftraggeber + Auftragnehmer: beide Parteien-Namen müssen maskiert sein."""
        text = "Auftraggeber: Max Mustermann\nAuftragnehmer: Anna Schmidt\n"
        result, mapping = pseudonymize_people(text)
        self.assertNotIn("Max Mustermann", result, "Auftraggeber-Name muss maskiert sein")
        self.assertNotIn("Anna Schmidt", result, "Auftragnehmer-Name muss maskiert sein")
        self.assertGreaterEqual(len(mapping), 2, "Beide Partei-Namen müssen im Mapping erscheinen")

    def test_vermieterin_mieter_both_masked_konsistent(self):
        """Vermieterin + Mieter: beide Parteien-Namen müssen konsistent maskiert sein."""
        text = "Vermieterin: Petra Maier\nMieter: Klaus Berger\n"
        result, mapping = pseudonymize_people(text)
        self.assertNotIn("Petra Maier", result, "Vermieterin-Name muss maskiert sein")
        self.assertNotIn("Klaus Berger", result, "Mieter-Name muss maskiert sein")
        self.assertGreaterEqual(len(mapping), 2, "Beide Partei-Namen müssen im Mapping erscheinen")


class PR11FalsePositiveP5Tests(unittest.TestCase):
    """P5 fix: Satzanfangswörter nach Zeilenumbruch / Klauselstart (IT-Rahmenvertrag §9, §12)
    dürfen NICHT als PERSON_X maskiert werden.
    Root Cause: _NON_PERSON_TOKENS fehlten häufige juristische Satzanfangswörter.
    """

    def test_soweit_ansprueche_not_masked(self):
        """'Soweit Ansprüche' taucht in §9 Haftungsklausel auf – kein Personenname."""
        text = "§9 Haftungsklausel\nSoweit Ansprüche geltend gemacht werden."
        result, mapping = pseudonymize_people(text)
        self.assertEqual(mapping, {}, f"False positive in §9: {mapping}")
        self.assertIn("Soweit Ansprüche", result)

    def test_soweit_leistungen_not_masked(self):
        """'Soweit Leistungen' – Klauseltext, kein Name."""
        text = "Soweit Leistungen nicht vertragsgemäß erbracht werden."
        result, mapping = pseudonymize_people(text)
        self.assertEqual(mapping, {}, f"False positive: {mapping}")

    def test_waehrend_vertragslaufzeit_not_masked(self):
        """'Während Vertragslaufzeit' – Satzanfang in §12 Kündigung, kein Name."""
        text = "Während Vertragslaufzeit gelten folgende Regelungen."
        result, mapping = pseudonymize_people(text)
        self.assertEqual(mapping, {}, f"False positive: {mapping}")

    def test_soweit_zahlungen_not_masked(self):
        """'Soweit Zahlungen' – §12 Kündigungsabschnitt, kein Name."""
        text = "§12 Kündigung\nSoweit Zahlungen ausständig sind."
        result, mapping = pseudonymize_people(text)
        self.assertEqual(mapping, {}, f"False positive: {mapping}")

    def test_aenderungen_fristen_not_masked(self):
        """'Änderungen Fristen' – Klauselköpfe, keine Personen."""
        text = "Änderungen Fristen bedürfen der Schriftform."
        result, mapping = pseudonymize_people(text)
        self.assertEqual(mapping, {}, f"False positive: {mapping}")


class PR11FalsePositiveP6Tests(unittest.TestCase):
    """P6 fix: Klauseltext und Tabellenanfänge (Mixed PDF) dürfen NICHT als
    PERSON_X maskiert werden.
    Root Cause: _NON_PERSON_TOKENS fehlten Substantive aus Vertragstabellen
    und §6 Wettbewerbsverbotstitel.
    """

    def test_nachvertragliches_wettbewerbsverbot_not_masked(self):
        """§6 'Nachvertragliches Wettbewerbsverbot' – Paragraphentitel, kein Name."""
        text = "§6 Nachvertragliches Wettbewerbsverbot gilt für 12 Monate."
        result, mapping = pseudonymize_people(text)
        self.assertEqual(mapping, {}, f"False positive in §6: {mapping}")
        self.assertIn("Nachvertragliches Wettbewerbsverbot", result)

    def test_mahnkosten_verzugszinsen_not_masked(self):
        """Tabellenkopf 'Mahnkosten / Verzugszinsen' – kein Personenname."""
        text = "Mahnkosten\tVerzugszinsen\n10,00 EUR\t5 %"
        result, mapping = pseudonymize_people(text)
        self.assertEqual(mapping, {}, f"False positive in Tabelle: {mapping}")

    def test_gegenüber_ansprüchen_not_masked(self):
        """'Gegenüber Ansprüchen' – juristische Phrase, kein Name."""
        text = "Gegenüber Ansprüchen Dritter haftet jede Partei selbst."
        result, mapping = pseudonymize_people(text)
        self.assertEqual(mapping, {}, f"False positive: {mapping}")

    def test_real_person_after_fix_still_detected(self):
        """Nach dem Fix müssen echte Namen weiterhin erkannt werden."""
        text = "Kläger: Max Mustermann hat das Wettbewerbsverbot verletzt."
        result, mapping = pseudonymize_people(text)
        self.assertNotIn("Max Mustermann", result, "Echter Name muss maskiert bleiben")
        self.assertTrue(len(mapping) > 0)


class PR11PartyConsistencyP9Tests(unittest.TestCase):
    """P9 fix: Wenn eine Vertragspartei eine GmbH/AG ist und die andere Partei
    eine natürliche Person, müssen BEIDE Parteien konsistent maskiert werden.
    Root Cause: _PARTY_PERSON_RE matcht nur 2 Wörter ({1}), daher wurde
    'Nordstadt LegalTech GmbH' nur partiell zu '[PARTEI_1] GmbH' maskiert.
    Fix: _PARTY_COMPANY_RE erfasst vollständige Firmennamen nach Rollenbezeichnung.
    """

    def test_camelcase_gmbh_auftraggeber_fully_masked(self):
        """'Nordstadt LegalTech GmbH' als Auftraggeber muss vollständig maskiert sein."""
        text = "Auftraggeber: Nordstadt LegalTech GmbH\nAuftragnehmer: Hans Meier"
        result, mapping = pseudonymize_people(text)
        self.assertNotIn(
            "Nordstadt LegalTech GmbH", result,
            f"Auftraggeber-GmbH muss vollständig maskiert sein, war: {result!r}",
        )
        # Kein 'GmbH'-Orphan darf übrig bleiben
        self.assertNotIn(
            "] GmbH", result,
            f"Kein Teil-Orphan erlaubt, war: {result!r}",
        )

    def test_auftragnehmer_person_still_masked(self):
        """Auftragnehmer-Person muss ebenfalls maskiert sein."""
        text = "Auftraggeber: Nordstadt LegalTech GmbH\nAuftragnehmer: Hans Meier"
        result, mapping = pseudonymize_people(text)
        self.assertNotIn("Hans Meier", result, "Auftragnehmer-Person muss maskiert sein")

    def test_both_parties_get_partei_label(self):
        """Beide Parteien erhalten [PARTEI_N]-Labels."""
        text = "Auftraggeber: Nordstadt LegalTech GmbH\nAuftragnehmer: Hans Meier"
        result, mapping = pseudonymize_people(text)
        self.assertIn("[PARTEI_1]", result, "Erste Partei muss [PARTEI_1] erhalten")
        self.assertIn("[PARTEI_2]", result, "Zweite Partei muss [PARTEI_2] erhalten")

    def test_company_name_in_mapping(self):
        """Vollständiger Firmenname muss im Mapping hinterlegt sein."""
        text = "Auftraggeber: Nordstadt LegalTech GmbH\nAuftragnehmer: Hans Meier"
        result, mapping = pseudonymize_people(text)
        self.assertIn(
            "Nordstadt LegalTech GmbH", mapping,
            f"Firmenname muss im original_to_label-Mapping stehen, mapping={mapping}",
        )

    def test_simple_gmbh_auftraggeber_still_works(self):
        """Einfache 'Müller GmbH' (2 Wörter) muss weiterhin korrekt maskiert werden."""
        text = "Auftraggeber: Müller GmbH\nAuftragnehmer: Hans Meier"
        result, mapping = pseudonymize_people(text)
        self.assertNotIn("Müller GmbH", result, "Einfache GmbH muss maskiert sein")
        self.assertNotIn("Hans Meier", result, "Person muss maskiert sein")
        self.assertGreaterEqual(len(mapping), 2)


if __name__ == "__main__":
    unittest.main()
