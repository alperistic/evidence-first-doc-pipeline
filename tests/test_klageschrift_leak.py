"""
test_klageschrift_leak.py

Blocker: Klarnamen und Adressen (Straße + Hausnummer) dürfen nicht in die
Mandantenfassung durchkommen.

Prüft die erweiterten Regex-Pattern für:
- ADRESS_KOMPLETT (Straße + Hausnummer + PLZ + Stadt)
- ADRESS_STRASSE (Straße + Hausnummer)
- ADRESS_PLZ_STADT (PLZ + Stadt)
- NAME_VOLLSTAENDIG (Vor- + Nachname)
"""

import unittest

from governance.redaction import prepare_for_llm, restore_from_llm

KLAGESCHRIFT_FIXTURE = """
Kläger: Max Mustermann, wohnhaft Hauptstraße 23, 80331 München
Beklagter: Anna Schmidt, Bahnhofstr. 4a, 10115 Berlin
Der Kläger Max Mustermann macht geltend, dass die Beklagte
Anna Schmidt die vereinbarte Miete nicht gezahlt hat.
Das Gericht hat entschieden, dass die Klage zulässig ist.
"""


def _mandantenfassung(text: str) -> str:
    """Erzeugt die Mandantenfassung (redacted_text) mit Pseudonymisierung."""
    ctx = prepare_for_llm(text, {
        "dictionary_entities": [],
        "dictionary_fuzzy": False,
        "dictionary_threshold": 92,
        "pseudonymize_people": True,
    })
    return ctx.redacted_text


def _kanzleiakte(text: str) -> str:
    """Erzeugt die Kanzleiakte (restored) über Roundtrip."""
    ctx = prepare_for_llm(text, {
        "dictionary_entities": [],
        "dictionary_fuzzy": False,
        "dictionary_threshold": 92,
        "pseudonymize_people": True,
    })
    return restore_from_llm(ctx.redacted_text, ctx.mapping)


class TestKlarnameNichtInMandantenfassung(unittest.TestCase):
    """Test 1: Klarnamen dürfen nicht im redacted Output stehen."""

    def test_max_mustermann_nicht_in_mandantenfassung(self):
        result = _mandantenfassung(KLAGESCHRIFT_FIXTURE)
        self.assertNotIn(
            "Max Mustermann", result,
            f"Klarname 'Max Mustermann' darf nicht in der Mandantenfassung stehen: {result!r}",
        )

    def test_anna_schmidt_nicht_in_mandantenfassung(self):
        result = _mandantenfassung(KLAGESCHRIFT_FIXTURE)
        self.assertNotIn(
            "Anna Schmidt", result,
            f"Klarname 'Anna Schmidt' darf nicht in der Mandantenfassung stehen: {result!r}",
        )


class TestAdresseNichtInMandantenfassung(unittest.TestCase):
    """Test 2: Straße + Hausnummer dürfen nicht im redacted Output stehen."""

    def test_hauptstrasse_23_nicht_in_mandantenfassung(self):
        result = _mandantenfassung(KLAGESCHRIFT_FIXTURE)
        self.assertNotIn(
            "Hauptstraße 23", result,
            f"Adresse 'Hauptstraße 23' darf nicht in der Mandantenfassung stehen: {result!r}",
        )

    def test_bahnhofstr_4a_nicht_in_mandantenfassung(self):
        result = _mandantenfassung(KLAGESCHRIFT_FIXTURE)
        self.assertNotIn(
            "Bahnhofstr. 4a", result,
            f"Adresse 'Bahnhofstr. 4a' darf nicht in der Mandantenfassung stehen: {result!r}",
        )


class TestPlzNichtInMandantenfassung(unittest.TestCase):
    """Test 3: PLZ + Stadt dürfen nicht im redacted Output stehen."""

    def test_80331_muenchen_nicht_in_mandantenfassung(self):
        result = _mandantenfassung(KLAGESCHRIFT_FIXTURE)
        self.assertNotIn(
            "80331 München", result,
            f"PLZ+Stadt '80331 München' darf nicht in der Mandantenfassung stehen: {result!r}",
        )

    def test_10115_berlin_nicht_in_mandantenfassung(self):
        result = _mandantenfassung(KLAGESCHRIFT_FIXTURE)
        self.assertNotIn(
            "10115 Berlin", result,
            f"PLZ+Stadt '10115 Berlin' darf nicht in der Mandantenfassung stehen: {result!r}",
        )


class TestKanzleiakteEnthaeltKlarnamen(unittest.TestCase):
    """Test 4: Die Kanzleiakte (restored) MUSS die Klarnamen enthalten."""

    def test_kanzleiakte_enthaelt_max_mustermann(self):
        result = _kanzleiakte(KLAGESCHRIFT_FIXTURE)
        self.assertIn(
            "Max Mustermann", result,
            f"Kanzleiakte muss 'Max Mustermann' enthalten: {result!r}",
        )

    def test_kanzleiakte_enthaelt_anna_schmidt(self):
        result = _kanzleiakte(KLAGESCHRIFT_FIXTURE)
        self.assertIn(
            "Anna Schmidt", result,
            f"Kanzleiakte muss 'Anna Schmidt' enthalten: {result!r}",
        )


class TestNeutralePhraseErhalten(unittest.TestCase):
    """Test 5: Neutrale juristische Phrasen müssen im Output erhalten bleiben."""

    def test_das_gericht_hat_entschieden_erhalten(self):
        result = _mandantenfassung(KLAGESCHRIFT_FIXTURE)
        self.assertIn(
            "Das Gericht hat entschieden", result,
            f"Neutrale Phrase 'Das Gericht hat entschieden' muss erhalten bleiben: {result!r}",
        )


class TestKeineDoppeltenReplacements(unittest.TestCase):
    """Test 6: Keine doppelten Replacements für dieselbe Originaladresse."""

    def test_keine_doppelten_addr_platzhalter(self):
        """Eine einzelne Adresse darf nicht zu mehreren aufeinanderfolgenden [ADDR_N] führen."""
        result = _mandantenfassung(KLAGESCHRIFT_FIXTURE)
        import re
        # Prüfe: keine zwei ADDR/PERSON-Platzhalter direkt hintereinander
        # (ohne sinnvollen Text dazwischen) für denselben Typ
        double_addr = re.findall(r"\[ADDR_\d+\]\s*\[ADDR_\d+\]", result)
        self.assertEqual(
            double_addr, [],
            f"Doppelte ADDR-Platzhalter gefunden: {double_addr} in {result!r}",
        )

    def test_einzelne_adresse_ein_platzhalter(self):
        """'Am Marktplatz 2, 50667 Köln' muss als ein einziger ADDR-Platzhalter erscheinen."""
        text = "Wohnsitz: Am Marktplatz 2, 50667 Köln"
        ctx = prepare_for_llm(text, {
            "dictionary_entities": [],
            "dictionary_fuzzy": False,
            "dictionary_threshold": 92,
            "pseudonymize_people": False,
        })
        import re
        addr_placeholders = re.findall(r"\[ADDR_\d+\]", ctx.redacted_text)
        # Genau ein ADDR-Platzhalter für die gesamte Adresse
        self.assertEqual(
            len(addr_placeholders), 1,
            f"Eine vollständige Adresse soll genau einen Platzhalter erzeugen, "
            f"bekam {len(addr_placeholders)}: {ctx.redacted_text!r}",
        )


if __name__ == "__main__":
    unittest.main()
