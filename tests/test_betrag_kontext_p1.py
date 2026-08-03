"""
tests/test_betrag_kontext_p1.py – P1-Bug: Beträge ohne Kontextlabel

Problem: Beträge werden mit generischem oder doc-typ-basiertem Label ausgegeben
statt mit einem aus dem unmittelbaren Kontext abgeleiteten Label.

Konkrete Bugs:
  1. Gesellschaftsrecht: `Betrag: EUR 6.250,00` → Residualwert aus Tabelle,
     kein direktes Kontext-Label → Betrag muss unterdrückt werden
  2. Arbeitsrecht: `Forderung: EUR 15.600,00` → abgelehntes Erstangebot,
     im Header-Kontext kein spezifisches Label → unterdrücken
  3. Mietrecht: `Betrag: EUR 12.600,00` → IBAN-Kontext mit „Rückstand"
     im Verwendungszweck → „Rückstand" labeln
"""

import unittest

from core.labels import _amount_label_from_context, _human_label_for_evidence
from core.export_render import _evidence_line
from ui.components import _format_summary_evidence_line


# ---------------------------------------------------------------------------
# _amount_label_from_context – Basis-Unit-Tests
# ---------------------------------------------------------------------------

class AmountLabelFromContextTests(unittest.TestCase):
    """Tests für _amount_label_from_context."""

    # --- Spezifische Labels (müssen erkannt werden) ---

    def test_kaufpreis_erkannt(self):
        ctx = "Kaufpreis EUR 4.500.000,00 zahlbar binnen 3 Werktagen nach Closing"
        self.assertEqual(_amount_label_from_context(ctx), "Kaufpreis")

    def test_basiskaufpreis_erkannt(self):
        ctx = "Basiskaufpreis 4.000.000,00 fällig am 05.04.2025"
        self.assertEqual(_amount_label_from_context(ctx), "Basiskaufpreis")

    def test_abfindung_erkannt(self):
        ctx = "Beklagte bietet Abfindung i.H.v. EUR 15.600,00 an"
        self.assertEqual(_amount_label_from_context(ctx), "Abfindung")

    def test_streitwert_erkannt(self):
        ctx = "Streitwert EUR 23.400,00 gemäß § 42 GKG"
        self.assertEqual(_amount_label_from_context(ctx), "Streitwert")

    def test_rueckstand_erkannt(self):
        ctx = "Verwendungszweck: MV Berger Str. 221 / Bergmann / Rückstand Jan-Feb 2025"
        self.assertEqual(_amount_label_from_context(ctx), "Rückstand")

    def test_rueckstand_ohne_umlaut(self):
        ctx = "Offener Rueckstand bitte begleichen: EUR 7.230,00"
        self.assertEqual(_amount_label_from_context(ctx), "Rückstand")

    def test_kaution_erkannt(self):
        ctx = "Die Kaution in Höhe von EUR 12.600,00 wurde am 28. Februar 2020 überwiesen"
        self.assertEqual(_amount_label_from_context(ctx), "Kaution")

    def test_mietsicherheit_erkannt(self):
        ctx = "Mietsicherheit EUR 3.600,00 spätestens bei Übergabe zu zahlen"
        self.assertEqual(_amount_label_from_context(ctx), "Kaution")

    def test_nebenkosten_erkannt(self):
        ctx = "Nebenkosten pauschal EUR 620,00 monatlich zzgl. USt."
        self.assertEqual(_amount_label_from_context(ctx), "Nebenkosten")

    def test_betriebskosten_erkannt(self):
        ctx = "Betriebskosten EUR 450,00 werden jährlich abgerechnet"
        self.assertEqual(_amount_label_from_context(ctx), "Nebenkosten")

    def test_grundmiete_erkannt(self):
        ctx = "Die monatliche Grundmiete beträgt EUR 4.200,00 netto"
        self.assertEqual(_amount_label_from_context(ctx), "Miete")

    def test_monatsmiete_erkannt(self):
        ctx = "Monatsmiete EUR 4.200,00 jeweils zum 3. eines Monats"
        self.assertEqual(_amount_label_from_context(ctx), "Miete")

    def test_vertragsstrafe_erkannt(self):
        ctx = "Vertragsstrafe EUR 10.000,00 je Verstoß gegen § 9"
        self.assertEqual(_amount_label_from_context(ctx), "Vertragsstrafe")

    def test_verstoss_als_vertragsstrafe_erkannt(self):
        ctx = "Vergütung Gehalt § 6 bei Verstoß: EUR 10.000,00 je Verstoß"
        self.assertEqual(_amount_label_from_context(ctx), "Vertragsstrafe")

    def test_bruttogehalt_erkannt(self):
        ctx = "Das monatliche Bruttogehalt beträgt EUR 7.800,00"
        self.assertEqual(_amount_label_from_context(ctx), "Gehalt")

    def test_verguetung_erkannt(self):
        ctx = "Die Vergütung beträgt EUR 4.600,00 monatlich"
        self.assertEqual(_amount_label_from_context(ctx), "Gehalt")

    def test_forderung_erkannt(self):
        ctx = "offene Forderung in Höhe von EUR 1.234,56 unverzüglich begleichen"
        self.assertEqual(_amount_label_from_context(ctx), "Forderung")

    def test_jahrespraemie_erkannt(self):
        ctx = "leistungsabhängige Jahresprämie von bis zu EUR 18.720,00"
        self.assertEqual(_amount_label_from_context(ctx), "Jahresprämie")

    def test_earnout_erkannt(self):
        ctx = "Earnout EUR 500.000,00 sofern EBITDA-Ziel 2025 erreicht"
        self.assertEqual(_amount_label_from_context(ctx), "Earnout")

    # --- Unterdrückung (None) ---

    def test_mietvertrag_header_gibt_none(self):
        """'GEWERBEMIETVERTRAG zwischen ...' enthält 'mietvertrag', aber nicht
        'grundmiete' o.Ä. – kein Label ableitbar → None."""
        ctx = (
            "GEWERBEMIETVERTRAG zwischen Stadtimmobilien Frankfurt AG, Mainzer Landstraße 50, "
            "60325 Frankfurt - nachfolgend: Vermieterin - und Bäckerei Sonnenschein GmbH & Co. KG"
        )
        self.assertIsNone(
            _amount_label_from_context(ctx),
            "Mietvertrag-Header ohne spezifischen Betragsbegriff → kein Label",
        )

    def test_klageerwiderung_header_gibt_none(self):
        """Schriftsatz-Header ohne Betragsbegriff → None."""
        ctx = (
            "SCHRIFTSATZ DER BEKLAGTEN - KLAGEERWIDERUNG Rechtsanwälte Berger & Kollegen "
            "Im Auftrag der Innovatech Solutions GmbH Datum: 14. Februar 2025 AZ Gericht: 12"
        )
        self.assertIsNone(
            _amount_label_from_context(ctx),
            "Klageerwiderung-Header ohne Abfindungs-/Forderungskontext → kein Label",
        )

    def test_stammkapital_tabellen_kontext_gibt_none(self):
        """Stammkapital-Tabelle: EUR 6.250,00 hat keinen direkten Betragsbegriff → None."""
        ctx = (
            "/2025 Stammkapital TechVenture EUR 25.000,00 Anteile Verkäuferin (vor) 75 % = "
            "EUR 18.750,00 Anteile Verkäuferin (nach) 0 % (vollständige Veräußerung dieser Anteile)"
        )
        self.assertIsNone(
            _amount_label_from_context(ctx),
            "Stammkapital-Tabelle ohne direktes Label für EUR 6.250,00 → kein Label",
        )

    def test_miete_wort_allein_gibt_none_wenn_im_mietvertragsheader(self):
        """'mietvertrag' enthält 'miete' als Substring – darf nicht 'Miete' triggern."""
        ctx = "GEWERBEMIETVERTRAG Laufzeit Mietzeitraum ab 01.03.2020"
        # "mietzeitraum" nicht in patterns, aber kein "grundmiete" etc.
        # "mietzins" wäre erkannt, aber hier ist es "mietzeitraum"
        result = _amount_label_from_context(ctx)
        self.assertNotEqual(result, "Miete", "Mietvertrags-Header soll nicht 'Miete' labeln")


# ---------------------------------------------------------------------------
# _human_label_for_evidence – Integration
# ---------------------------------------------------------------------------

class HumanLabelForEvidenceBetraegeTests(unittest.TestCase):
    """Integration: _human_label_for_evidence mit field='betraege'."""

    def test_kaufvertrag_kaufpreis_korrekt(self):
        label = _human_label_for_evidence(
            "betraege", "EUR 4.500.000,00",
            "Kaufpreis EUR 4.500.000,00 fällig 3 Werktage nach Closing",
            "kaufvertrag",
        )
        self.assertEqual(label, "Kaufpreis")

    def test_klageschrift_abfindung_erkennt_abfindung(self):
        """Bug 2 (Arbeitsrecht): Wenn 'Abfindung' im Quote → 'Abfindung', nicht 'Forderung'."""
        label = _human_label_for_evidence(
            "betraege", "EUR 15.600,00",
            "Beklagte bietet Abfindung i.H.v. EUR 15.600,00 und Arbeitszeugnis an",
            "klageschrift",
        )
        self.assertEqual(label, "Abfindung",
                         "Abfindungsbetrag muss als 'Abfindung' gelabelt werden, nicht 'Forderung'")

    def test_klageschrift_header_ohne_kontext_gibt_none(self):
        """Bug 2: Klageerwiderung-Header ohne Betragsbegriff → None → unterdrücken."""
        label = _human_label_for_evidence(
            "betraege", "EUR 15.600,00",
            "SCHRIFTSATZ DER BEKLAGTEN - KLAGEERWIDERUNG Rechtsanwälte Berger & Kollegen "
            "Im Auftrag der Innovatech Solutions GmbH Datum: 14. Februar 2025",
            "klageschrift",
        )
        self.assertIsNone(label,
                          "Betrag ohne spezifischen Kontext muss None zurückgeben (nicht 'Forderung')")

    def test_mietvertrag_grundmiete_korrekt(self):
        """Echter Mietbetrag (Grundmiete) wird korrekt gelabelt."""
        label = _human_label_for_evidence(
            "betraege", "EUR 4.200,00",
            "Die monatliche Grundmiete beträgt EUR 4.200,00 netto zzgl. USt.",
            "mietvertrag",
        )
        self.assertEqual(label, "Miete")

    def test_mietvertrag_kaution_korrekt(self):
        label = _human_label_for_evidence(
            "betraege", "EUR 12.600,00",
            "Kaution in Höhe von EUR 12.600,00 wurde am 28. Februar 2020 überwiesen",
            "mietvertrag",
        )
        self.assertEqual(label, "Kaution")

    def test_mietvertrag_header_gibt_none(self):
        """Bug 3 (Mietrecht, Doc 1): GEWERBEMIETVERTRAG-Header → None statt 'Miete'."""
        label = _human_label_for_evidence(
            "betraege", "EUR 12.600,00",
            "GEWERBEMIETVERTRAG zwischen Stadtimmobilien Frankfurt AG, Mainzer Landstraße 50, "
            "60325 Frankfurt - nachfolgend: Vermieterin - und Bäckerei Sonnenschein GmbH & Co. KG",
            "mietvertrag",
        )
        self.assertIsNone(label,
                          "Mietvertrag-Header ohne Miet-Spezifikum → None (nicht 'Miete')")

    def test_mietvertrag_rueckstand_iban_kontext(self):
        """Bug 3 (Mietrecht, Doc 2): IBAN-Kontext mit 'Rückstand' → 'Rückstand'."""
        label = _human_label_for_evidence(
            "betraege", "EUR 12.600,00",
            "Februar 2025 auf unser Konto zu überweisen: IBAN: DE89 5005 0201 0000 1234 56 "
            "BIC: HELADEF1822 Verwendungszweck: MV Berger Str. 221 / Bergmann / Rückstand Jan-",
            "mietvertrag",
        )
        self.assertEqual(label, "Rückstand",
                         "IBAN-Kontext mit 'Rückstand' im Verwendungszweck → 'Rückstand'")

    def test_kaufvertrag_tabellenkontext_gibt_none(self):
        """Bug 1 (Gesellschaftsrecht): Stammkapital-Tabelle → None statt 'Betrag'."""
        label = _human_label_for_evidence(
            "betraege", "EUR 6.250,00",
            "/2025 Stammkapital TechVenture EUR 25.000,00 Anteile Verkäuferin (vor) 75 % = "
            "EUR 18.750,00 Anteile Verkäuferin (nach) 0 % (vollständige Veräußerung dieser Ant",
            "kaufvertrag",
        )
        self.assertIsNone(label,
                          "Residualwert aus Stammkapital-Tabelle → None (nicht 'Betrag')")

    # --- Regressionen ---

    def test_arbeitsvertrag_bruttogehalt_regression(self):
        """Regression PR13 P3: Gehalts-Betrag wird weiterhin als 'Gehalt' gelabelt."""
        label = _human_label_for_evidence(
            "betraege", "EUR 4.600,00",
            "Das monatliche Bruttogehalt beträgt EUR 4.600,00",
            "arbeitsvertrag",
        )
        self.assertEqual(label, "Gehalt",
                         "Echter Gehalts-Betrag muss weiterhin 'Gehalt' gelabelt werden")

    def test_arbeitsvertrag_vertragsstrafe_nicht_gehalt(self):
        """Regression PR13 P3: Vertragsstrafe-Betrag darf nicht 'Gehalt' sein."""
        label = _human_label_for_evidence(
            "betraege", "EUR 10.000,00",
            "Gehalt EUR 4.600 Vertragsstrafe EUR 10.000,00 je Verstoß",
            "arbeitsvertrag",
        )
        self.assertNotEqual(label, "Gehalt",
                            "Vertragsstrafe-Betrag darf nicht 'Gehalt' sein")

    def test_fristen_termine_unberuehrt(self):
        """Regression: Nicht-Betrags-Felder bleiben unverändert."""
        label = _human_label_for_evidence(
            "fristen_termine", "15.12.2024",
            "Kündigung zum 31.03.2025",
            "arbeitsvertrag",
        )
        self.assertEqual(label, "Frist/Termin")

    def test_datum_beginn_mietvertrag_regression(self):
        """Regression: datum_beginn im Mietvertrag → 'Mietbeginn'."""
        label = _human_label_for_evidence(
            "datum_beginn", "01.03.2020",
            "Mietbeginn ab 01.03.2020",
            "mietvertrag",
        )
        self.assertEqual(label, "Mietbeginn")


# ---------------------------------------------------------------------------
# Render-Ebene: Unterdrückung bei None-Label
# ---------------------------------------------------------------------------

class EvidenceLineSuppressionTests(unittest.TestCase):
    """_evidence_line und _format_summary_evidence_line unterdrücken bei None-Label."""

    GENERIC_TABLE_ITEM = {
        "field": "betraege",
        "value": "EUR 6.250,00",
        "page": 3,
        "quote": (
            "/2025 Stammkapital TechVenture EUR 25.000,00 Anteile Verkäuferin (vor) 75 % = "
            "EUR 18.750,00 Anteile Verkäuferin (nach) 0 % (vollständige Veräußerung dieser Ant"
        ),
    }

    HEADER_ITEM = {
        "field": "betraege",
        "value": "EUR 15.600,00",
        "page": 10,
        "quote": (
            "SCHRIFTSATZ DER BEKLAGTEN - KLAGEERWIDERUNG Rechtsanwälte Berger & Kollegen "
            "Im Auftrag der Innovatech Solutions GmbH Datum: 14. Februar 2025"
        ),
    }

    KAUFPREIS_ITEM = {
        "field": "betraege",
        "value": "EUR 4.500.000,00",
        "page": 3,
        "quote": "Kaufpreis EUR 4.500.000,00 fällig 3 Werktage nach Closing",
    }

    def test_evidence_line_suppresses_table_context(self):
        """_evidence_line gibt '' zurück wenn kein spezifisches Label ableitbar."""
        result = _evidence_line(self.GENERIC_TABLE_ITEM, "kaufvertrag")
        self.assertEqual(result, "",
                         "_evidence_line muss '' zurückgeben wenn kein Label erkannt")

    def test_evidence_line_suppresses_header_context(self):
        """_evidence_line unterdrückt Klageerwiderung-Header."""
        result = _evidence_line(self.HEADER_ITEM, "klageschrift")
        self.assertEqual(result, "",
                         "_evidence_line muss '' zurückgeben für Klageerwiderung-Header")

    def test_evidence_line_renders_kaufpreis(self):
        """_evidence_line rendert Kaufpreis-Betrag korrekt."""
        result = _evidence_line(self.KAUFPREIS_ITEM, "kaufvertrag")
        self.assertIn("Kaufpreis", result)
        self.assertIn("4.500.000,00", result)
        self.assertNotEqual(result, "")

    def test_format_summary_suppresses_table_context(self):
        """_format_summary_evidence_line gibt '' zurück für Tabellen-Kontext."""
        result = _format_summary_evidence_line(self.GENERIC_TABLE_ITEM, "kaufvertrag")
        self.assertEqual(result, "",
                         "_format_summary_evidence_line muss '' zurückgeben wenn kein Label")

    def test_format_summary_suppresses_header_context(self):
        """_format_summary_evidence_line gibt '' zurück für Klageerwiderung-Header."""
        result = _format_summary_evidence_line(self.HEADER_ITEM, "klageschrift")
        self.assertEqual(result, "")

    def test_format_summary_renders_kaufpreis(self):
        """_format_summary_evidence_line rendert Kaufpreis korrekt."""
        result = _format_summary_evidence_line(self.KAUFPREIS_ITEM, "kaufvertrag")
        self.assertIn("Kaufpreis", result)
        self.assertNotEqual(result, "")

    def test_evidence_line_uses_full_quote_not_truncated(self):
        """_evidence_line muss das VOLLE Quote für Label-Ableitung verwenden,
        nicht das auf 160 Zeichen gekürzte Display-Excerpt."""
        # Quote: 'Abfindung' erscheint erst nach den ersten 160 Zeichen
        long_header = "A" * 160  # 160 Zeichen Header ohne 'Abfindung'
        quote_with_abfindung_after_160 = long_header + " Abfindung EUR 23.400,00"
        item = {
            "field": "betraege",
            "value": "EUR 23.400,00",
            "page": 12,
            "quote": quote_with_abfindung_after_160,
        }
        result = _evidence_line(item, "klageschrift")
        self.assertIn("Abfindung", result,
                      "_evidence_line muss das volle Quote lesen (nicht nur die ersten 160 Zeichen)")


if __name__ == "__main__":
    unittest.main()
