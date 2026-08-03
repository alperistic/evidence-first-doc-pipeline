"""
tests/test_betragsmapping_baeckerei.py – Betragsmapping Bäckerei-Mietstreit

Blocker: Kaution und Rückstand werden in der Zusammenfassung vertauscht,
weil nur der Betragswert extrahiert wird, nicht der Kontext.
Fix: Betrag + umliegender Satzkontext gemeinsam extrahiert und per
Keyword-Mapping klassifiziert.
"""

import unittest

from core.labels import klassifiziere_betrag, BETRAG_KONTEXT_KEYWORDS
from core.evidence import build_evidence_map
from core.export_render import render_segmented_summary_text


BAECKEREI_FIXTURE = """
Mietverhältnis: Bäckerei Goldkruste GmbH, Marktplatz 5, 76133 Karlsruhe
Monatliche Kaltmiete: 2.400 €
Mietrückstand: 3 Monate × 2.400 € = 7.200 €
Kaution: 7.200 € (3 Monatsmieten, hinterlegt am 01.03.2023)
Der Mieter schuldet einen Mietrückstand von 7.200 €.
Die Kaution beträgt ebenfalls 7.200 € und wurde als Sicherheitsleistung hinterlegt.
"""


class TestKautionKorrektKlassifiziert(unittest.TestCase):
    """Betrag der Kaution wird als 'kaution' klassifiziert, nicht als 'rueckstand'."""

    def test_kaution_korrekt_klassifiziert(self):
        kontext = "Die Kaution beträgt ebenfalls 7.200 € und wurde als Sicherheitsleistung hinterlegt."
        self.assertEqual(klassifiziere_betrag(kontext), "kaution")

    def test_kaution_im_satz(self):
        kontext = "Kaution: 7.200 € (3 Monatsmieten, hinterlegt am 01.03.2023)"
        self.assertEqual(klassifiziere_betrag(kontext), "kaution")

    def test_sicherheitsleistung_als_kaution(self):
        kontext = "Eine Sicherheitsleistung in Höhe von 5.000 € ist zu zahlen."
        self.assertEqual(klassifiziere_betrag(kontext), "kaution")


class TestRueckstandKorrektKlassifiziert(unittest.TestCase):
    """Mietrückstand wird als 'rueckstand' klassifiziert, nicht als 'kaution'."""

    def test_mietrueckstand_korrekt_klassifiziert(self):
        kontext = "Mietrückstand: 3 Monate × 2.400 € = 7.200 €"
        self.assertEqual(klassifiziere_betrag(kontext), "rueckstand")

    def test_rueckstand_im_satz(self):
        kontext = "Der Mieter schuldet einen Mietrückstand von 7.200 €."
        self.assertEqual(klassifiziere_betrag(kontext), "rueckstand")

    def test_zahlungsrueckstand(self):
        kontext = "Zahlungsrückstand EUR 3.600,00 zum 01.02.2025"
        self.assertEqual(klassifiziere_betrag(kontext), "rueckstand")


class TestGleicheBetraegeUnterschiedlicheFelder(unittest.TestCase):
    """Wenn Kaution und Rückstand denselben Betrag haben (7.200 €),
    werden sie trotzdem korrekt unterschiedlichen Feldern zugeordnet."""

    def test_gleiche_betraege_unterschiedliche_felder(self):
        pages = [{"page_number": 1, "text": BAECKEREI_FIXTURE}]
        ev = build_evidence_map(pages)

        kaution_items = [
            item for item in ev
            if item.get("field") == "kaution" and item.get("value") != "missing"
        ]
        rueckstand_items = [
            item for item in ev
            if item.get("field") == "rueckstand" and item.get("value") != "missing"
        ]

        self.assertTrue(
            kaution_items,
            f"Kaution-Feld muss mindestens einen Eintrag haben. Evidence: {ev}",
        )
        self.assertTrue(
            rueckstand_items,
            f"Rückstand-Feld muss mindestens einen Eintrag haben. Evidence: {ev}",
        )

        # Beide Felder existieren gleichzeitig – keiner verdrängt den anderen
        kaution_values = {item["value"] for item in kaution_items}
        rueckstand_values = {item["value"] for item in rueckstand_items}
        # Beide können 7.200 € enthalten, aber in verschiedenen Feldern
        self.assertTrue(
            any("7.200" in v for v in kaution_values),
            f"Kaution muss 7.200 enthalten, hat: {kaution_values}",
        )
        self.assertTrue(
            any("7.200" in v for v in rueckstand_values),
            f"Rückstand muss 7.200 enthalten, hat: {rueckstand_values}",
        )


class TestMieteKorrektKlassifiziert(unittest.TestCase):
    """2.400 € wird als 'miete' klassifiziert."""

    def test_kaltmiete_als_miete(self):
        kontext = "Monatliche Kaltmiete: 2.400 €"
        self.assertEqual(klassifiziere_betrag(kontext), "miete")

    def test_miete_in_evidence_map(self):
        pages = [{"page_number": 1, "text": BAECKEREI_FIXTURE}]
        ev = build_evidence_map(pages)
        miete_items = [
            item for item in ev
            if item.get("field") == "miete" and item.get("value") != "missing"
        ]
        self.assertTrue(
            miete_items,
            f"Miete-Feld muss mindestens einen Eintrag haben. Evidence: {ev}",
        )
        self.assertTrue(
            any("2.400" in item["value"] for item in miete_items),
            f"Miete muss 2.400 enthalten, hat: {[i['value'] for i in miete_items]}",
        )


class TestKlassifiziereBetragFallback(unittest.TestCase):
    """Unbekannter Kontext gibt 'betrag' zurück (kein Crash)."""

    def test_fallback_bei_unbekanntem_kontext(self):
        self.assertEqual(klassifiziere_betrag("Irgendein Text ohne Keywords"), "betrag")

    def test_fallback_bei_leerem_string(self):
        self.assertEqual(klassifiziere_betrag(""), "betrag")

    def test_fallback_bei_zahlen_nur(self):
        self.assertEqual(klassifiziere_betrag("EUR 1.234,56"), "betrag")


class TestKeinBetragVertauschtInZusammenfassung(unittest.TestCase):
    """In der generierten Zusammenfassung steht Kaution nicht beim
    Rückstandsfeld und umgekehrt."""

    def test_kein_betrag_vertauscht_in_zusammenfassung(self):
        pages = [{"page_number": 1, "text": BAECKEREI_FIXTURE}]
        ev = build_evidence_map(pages)

        segment_results = [
            {
                "segment_meta": {
                    "title_guess": "Bäckerei Mietstreit",
                    "page_start": 1,
                    "page_end": 1,
                },
                "analysis": {
                    "dokumenttyp_parteien": "Mietvertrag",
                    "kernaussagen": [
                        {
                            "text": "Mietrückstand von 7.200 €",
                            "citations": [{"page_number": 1, "quote_snippet": "Mietrückstand"}],
                        }
                    ],
                },
                "evidence_map": ev,
            }
        ]

        summary = render_segmented_summary_text(segment_results)

        # Neues Verhalten (post-processing Kaution-Schutz):
        # Wenn Kaution und Rückstand denselben Betrag haben, wird der Rückstand-
        # Eintrag entfernt (Kaution hat Priorität).
        lines = summary.split("\n")
        kaution_lines = [l for l in lines if "Kaution" in l and "7.200" in l]
        rueckstand_7200_lines = [l for l in lines if "Rückstand" in l and "7.200" in l]

        self.assertTrue(
            kaution_lines,
            f"Zusammenfassung muss 'Kaution: ...7.200...' enthalten.\nSummary:\n{summary}",
        )
        # Rückstand: 7.200 € wird jetzt durch post-processing entfernt,
        # weil 7.200 € bereits als Kaution gelistet ist (Kaution hat Priorität).
        self.assertFalse(
            rueckstand_7200_lines,
            f"'Rückstand: 7.200 €' darf nicht erscheinen — gleicher Betrag ist als "
            f"Kaution gelistet (post-processing Kaution-Schutz).\nSummary:\n{summary}",
        )

        # Kaution-Zeile darf nicht 'Rückstand' enthalten
        for line in kaution_lines:
            self.assertNotIn(
                "Rückstand",
                line.replace("Kaution", ""),
                f"Kaution-Zeile darf nicht 'Rückstand' enthalten: {line}",
            )


if __name__ == "__main__":
    unittest.main()
