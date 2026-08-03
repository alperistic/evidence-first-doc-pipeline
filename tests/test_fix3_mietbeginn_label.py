"""
tests/test_fix3_mietbeginn_label.py

FIX 3: Label "Mietbeginn" darf NUR vergeben werden wenn der doc_type Miet-Kontext hat.
"""

import unittest

from core.labels import _human_label_for_evidence


class TestMietbeginnLabel(unittest.TestCase):
    """FIX 3: Mietbeginn nur für Miet-Dokumente."""

    def test_mietbeginn_for_mietvertrag(self):
        """Gewerbemietvertrag + datum_beginn → Mietbeginn."""
        label = _human_label_for_evidence("datum_beginn", "01.01.2025", "Mietbeginn 01.01.2025", "Gewerbemietvertrag")
        self.assertEqual(label, "Mietbeginn")

    def test_mietbeginn_for_mietvertrag_normalized_key(self):
        """mietvertrag (normalized) + datum_beginn → Mietbeginn."""
        label = _human_label_for_evidence("datum_beginn", "01.01.2025", "Beginn", "mietvertrag")
        self.assertEqual(label, "Mietbeginn")

    def test_no_mietbeginn_for_scheidung(self):
        """Scheidungsfolgenvereinbarung + datum_beginn → Beginn (nicht Mietbeginn)."""
        label = _human_label_for_evidence(
            "datum_beginn", "01.01.2025",
            "Beginn der Unterhaltszahlungen",
            "Scheidungsfolgenvereinbarung"
        )
        self.assertNotEqual(label, "Mietbeginn", f"Got: {label!r}")
        self.assertEqual(label, "Unterhaltsbeginn")

    def test_no_mietbeginn_from_quote_alone(self):
        """doc_type=generic, quote enthält 'miet' → trotzdem KEIN Mietbeginn."""
        label = _human_label_for_evidence(
            "datum_beginn", "01.01.2025",
            "Bezug der gemieteten Fahrzeuge ab diesem Datum",
            "Scheidungsfolgenvereinbarung"
        )
        self.assertNotEqual(label, "Mietbeginn", f"Got: {label!r}")
        self.assertEqual(label, "Beginn")

    def test_beginn_for_unknown_doctype_no_context(self):
        """Unbekannter Dokumenttyp, kein spezifischer Kontext → Beginn."""
        label = _human_label_for_evidence("datum_beginn", "01.06.2024", "", "Unklar")
        self.assertEqual(label, "Beginn")

    def test_beschaeftigungsbeginn_for_arbeitsvertrag(self):
        """Arbeitsvertrag + datum_beginn → Beschäftigungsbeginn."""
        label = _human_label_for_evidence("datum_beginn", "01.03.2024", "Beginn", "Arbeitsvertrag")
        self.assertEqual(label, "Beschäftigungsbeginn")


if __name__ == "__main__":
    unittest.main()
