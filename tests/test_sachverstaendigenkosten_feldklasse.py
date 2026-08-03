"""
tests/test_sachverstaendigenkosten_feldklasse.py

Bug: EUR 1.200,00 (Sachverständigenkosten, S. 11) wurde als Feldklasse "miete"
klassifiziert, weil das nackte Keyword "miete" in einer Nachbarzeile erschien
und via ±2-Zeilen-Kontext (Stufe 2) in den Proximity-Scorer blutete.

Fix: "miete" aus BETRAG_KONTEXT_KEYWORDS["miete"] entfernt.
Nur Compound-Formen (kaltmiete, grundmiete, nettomiete, monatsmiete,
mietzins, gesamtmiete) lösen das Miete-Feld aus.

Regressionen:
- "Monatsmiete EUR 4.200,00" → bleibt Miete (Keyword "monatsmiete")
- "Grundmiete EUR 4.120,00"  → bleibt Miete (Keyword "grundmiete")
"""

import unittest

from core.labels import klassifiziere_betrag, klassifiziere_betrag_mit_kontext
from core.evidence_map import build_evidence_map


SACHVERSTAENDIGEN_FIXTURE = (
    "§ 12 Mietminderung\n"
    "Die Mietminderung für den streitgegenständlichen Zeitraum beträgt 15 % der Miete.\n"
    "Sachverständigenkosten EUR 1.200,00 laut Gutachten vom 10.01.2025.\n"
    "Die übrigen Mietkosten bleiben unverändert.\n"
)


class SachverstaendigenKostenNichtMieteTests(unittest.TestCase):
    """EUR 1.200,00 (Sachverständigenkosten) darf nicht als 'miete' klassifiziert werden."""

    def test_sachverstaendigenkosten_nicht_miete_klassifiziere_betrag(self):
        """klassifiziere_betrag: 'Miete' in Nachbarzeile darf nicht auf Sachverständigenkosten bluten."""
        # Simuliert den ±2-Zeilen-Kontext wie er in build_evidence_map entsteht
        kontext = (
            "Die Mietminderung für den streitgegenständlichen Zeitraum beträgt 15 % der Miete. "
            "Sachverständigenkosten EUR 1.200,00 laut Gutachten vom 10.01.2025. "
            "Die übrigen Mietkosten bleiben unverändert."
        )
        result = klassifiziere_betrag(kontext)
        self.assertNotEqual(
            result, "miete",
            f"Sachverständigenkosten dürfen nicht als 'miete' klassifiziert werden, war: {result!r}",
        )

    def test_sachverstaendigenkosten_nicht_miete_mit_kontext(self):
        """klassifiziere_betrag_mit_kontext: Einzelzeile ohne Compound-Keyword → kein 'miete'."""
        line = "Sachverständigenkosten EUR 1.200,00 laut Gutachten vom 10.01.2025."
        kontext = (
            "Die Mietminderung für den streitgegenständlichen Zeitraum beträgt 15 % der Miete. "
            "Sachverständigenkosten EUR 1.200,00 laut Gutachten vom 10.01.2025. "
            "Die übrigen Mietkosten bleiben unverändert."
        )
        result = klassifiziere_betrag_mit_kontext(line, kontext)
        self.assertNotEqual(
            result.feldname, "miete",
            f"Sachverständigenkosten dürfen nicht als 'miete' landen, war: {result.feldname!r}",
        )

    def test_sachverstaendigenkosten_evidence_map_nicht_miete(self):
        """build_evidence_map: EUR 1.200,00 in Sachverständigenkosten-Kontext → kein 'miete'-Item."""
        pages = [{"page_number": 11, "text": SACHVERSTAENDIGEN_FIXTURE}]
        ev = build_evidence_map(pages)
        miete_1200 = [
            i for i in ev
            if i.get("field") == "miete" and "1.200" in i.get("value", "")
        ]
        self.assertFalse(
            miete_1200,
            f"EUR 1.200,00 darf nicht als 'miete'-Item erscheinen. Evidence: {ev}",
        )


# ---------------------------------------------------------------------------
# Regressionen: Compound-Miete-Keywords bleiben wirksam
# ---------------------------------------------------------------------------

class MieteCompoundRegressionTests(unittest.TestCase):
    """Compound-Formen lösen 'miete' weiterhin korrekt aus."""

    def test_monatsmiete_bleibt_miete(self):
        kontext = "Monatsmiete EUR 4.200,00 jeweils zum 3. eines Monats"
        self.assertEqual(klassifiziere_betrag(kontext), "miete")

    def test_grundmiete_bleibt_miete(self):
        kontext = "Die monatliche Grundmiete beträgt EUR 4.120,00 netto"
        self.assertEqual(klassifiziere_betrag(kontext), "miete")

    def test_kaltmiete_bleibt_miete(self):
        kontext = "Monatliche Kaltmiete: 2.400 €"
        self.assertEqual(klassifiziere_betrag(kontext), "miete")

    def test_nettomiete_bleibt_miete(self):
        kontext = "Nettomiete EUR 3.800,00 monatlich fällig"
        self.assertEqual(klassifiziere_betrag(kontext), "miete")

    def test_mietzins_bleibt_miete(self):
        kontext = "Mietzins EUR 4.820,00 fällig jeweils am 5. Werktag"
        self.assertEqual(klassifiziere_betrag(kontext), "miete")

    def test_gesamtmiete_bleibt_miete(self):
        kontext = "Gesamtmiete EUR 5.100,00 monatlich fällig"
        self.assertEqual(klassifiziere_betrag(kontext), "miete")

    def test_miete_evidence_map_compound(self):
        """Compound-Keyword in Fixture → 'miete'-Item erscheint in Evidence Map."""
        pages = [{"page_number": 1, "text": "Monatliche Grundmiete: EUR 4.200,00\n"}]
        ev = build_evidence_map(pages)
        miete_items = [
            i for i in ev
            if i.get("field") == "miete" and i.get("value", "missing") != "missing"
        ]
        self.assertTrue(
            miete_items,
            f"Grundmiete-Fixture muss 'miete'-Item erzeugen. Evidence: {ev}",
        )


if __name__ == "__main__":
    unittest.main()
