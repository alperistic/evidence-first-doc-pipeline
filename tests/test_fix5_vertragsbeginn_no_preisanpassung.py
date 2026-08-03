"""
tests/test_fix5_vertragsbeginn_no_preisanpassung.py

FIX 5: Datum aus Preisanpassungsklausel wird NICHT als Vertragsbeginn (datum_beginn)
extrahiert.
"""

import unittest

from core.evidence_map import build_evidence_map


def _make_page(text: str, page_no: int = 1) -> dict:
    return {"page_number": page_no, "text": text}


class TestVertragsbeginnPreisanpassungFilter(unittest.TestCase):
    def test_preisanpassung_stichtag_not_datum_beginn(self):
        """Preisanpassungs-Stichtag darf nicht als datum_beginn erscheinen."""
        pages = [_make_page(
            "Der Vertrag beginnt am 01.05.2025. "
            "Preisanpassungen sind frühestens 12 Monate nach Vertragsbeginn (Stichtag 01.05.2026) zulässig."
        )]
        ev = build_evidence_map(pages)
        beginn_values = [
            item["value"] for item in ev if item.get("field") == "datum_beginn" and item["value"] != "missing"
        ]
        # 01.05.2026 ist ein Preisanpassungs-Stichtag — darf NICHT als Beginn erscheinen
        self.assertNotIn("01.05.2026", beginn_values, f"datum_beginn: {beginn_values}")

    def test_echter_vertragsbeginn_bleibt(self):
        """Echter Vertragsbeginn wird weiterhin extrahiert."""
        pages = [_make_page(
            "Der Mietvertrag beginnt am 01.05.2025 und endet regulär nach 5 Jahren."
        )]
        ev = build_evidence_map(pages)
        beginn_values = [
            item["value"] for item in ev if item.get("field") == "datum_beginn" and item["value"] != "missing"
        ]
        self.assertIn("01.05.2025", beginn_values, f"datum_beginn: {beginn_values}")

    def test_monate_nach_context_not_datum_beginn(self):
        """'X Monate nach Vertragsbeginn' mit Datum → nicht als Vertragsbeginn."""
        pages = [_make_page(
            "Eine Anpassung ist 12 Monate nach dem Vertragsbeginn am 01.06.2026 möglich."
        )]
        ev = build_evidence_map(pages)
        beginn_values = [
            item["value"] for item in ev if item.get("field") == "datum_beginn" and item["value"] != "missing"
        ]
        self.assertNotIn("01.06.2026", beginn_values, f"datum_beginn: {beginn_values}")


if __name__ == "__main__":
    unittest.main()
