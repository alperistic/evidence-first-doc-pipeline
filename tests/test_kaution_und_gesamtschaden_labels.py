"""
tests/test_kaution_und_gesamtschaden_labels.py

Prüft die Prompt-Präzisierungen (Regeln 4–6) in core/prompts.py:

4. Kaution vs. Rückstand:
   "Kaution verwerten" / "Kaution i.H.v." → "Kaution", niemals "Rückstand"
   (auch wenn "Rückstand" im selben Kontext vorkommt)

5. Gesamtschaden vs. Miete:
   "Gesamtschaden: EUR X" im Mietstreit-Kontext → "Schadensersatz", nicht "Miete"

Fix A:
   BETRAG_KONTEXT_KEYWORDS["schadensersatz"] enthält "gesamtschaden",
   "schadensposten", "reparaturkosten" → klassifiziere_betrag_mit_kontext()
   gibt "schadensersatz" mit confidence 1.0 zurück (Stufe 1, Einzelzeile).
"""

import unittest

from core.labels import (
    _amount_label_from_context,
    _human_label_for_evidence,
    klassifiziere_betrag_mit_confidence,
    klassifiziere_betrag_mit_kontext,
)


class KautionVsRueckstandTests(unittest.TestCase):
    """Kaution-Kontext gibt niemals 'Rückstand' zurück — auch mit Rückstand im Satz."""

    def test_kaution_verwerten_nicht_rueckstand(self):
        """'Kaution verwerten' → 'Kaution', nicht 'Rückstand'."""
        ctx = (
            "Wir werden die Kaution i.H.v. EUR 12.600,00 verwerten, "
            "da ein Mietrückstand von 3 Monaten besteht."
        )
        label = _amount_label_from_context(ctx)
        self.assertNotEqual(
            label,
            "Rückstand",
            f"'Kaution verwerten'-Kontext darf nicht 'Rückstand' ergeben, war: {label!r}",
        )
        self.assertEqual(
            label,
            "Kaution",
            f"'Kaution verwerten'-Kontext muss 'Kaution' ergeben, war: {label!r}",
        )

    def test_kaution_ihv_in_mahnung_ist_kaution(self):
        """'Kaution i.H.v.' in Mahnung → 'Kaution', auch neben Rückstand-Formulierung."""
        ctx = (
            "Anderenfalls werden wir die Mietsicherheit i.H.v. EUR 9.600,00 "
            "zur Deckung des offenen Rückstands in Anspruch nehmen."
        )
        label = _amount_label_from_context(ctx)
        self.assertEqual(
            label,
            "Kaution",
            f"'Mietsicherheit'-Kontext muss 'Kaution' ergeben, war: {label!r}",
        )
        self.assertNotEqual(
            label,
            "Rückstand",
            f"'Mietsicherheit'-Kontext darf nicht 'Rückstand' sein, war: {label!r}",
        )

    def test_human_label_kaution_verwerten(self):
        """_human_label_for_evidence: Kaution-Verwertungskontext → 'Kaution'."""
        ctx = "Kaution i.H.v. EUR 12.600,00 verwerten wegen Mietrückstand"
        label = _human_label_for_evidence("betraege", "EUR 12.600,00", ctx, "mietvertrag")
        self.assertEqual(
            label,
            "Kaution",
            f"_human_label_for_evidence muss 'Kaution' zurückgeben, war: {label!r}",
        )

    def test_regression_reiner_rueckstand_bleibt_rueckstand(self):
        """Regression: Reiner Rückstand-Kontext (ohne Kaution) liefert weiterhin 'Rückstand'."""
        ctx = "Offener Rückstand EUR 7.230,00 für Januar bis März 2025 nicht geleistet"
        label = _amount_label_from_context(ctx)
        self.assertEqual(
            label,
            "Rückstand",
            f"Reiner Rückstand-Kontext muss weiterhin 'Rückstand' ergeben, war: {label!r}",
        )

    def test_regression_reine_kaution_bleibt_kaution(self):
        """Regression: Reiner Kaution-Kontext (ohne Rückstand) liefert weiterhin 'Kaution'."""
        ctx = "Die Kaution in Höhe von EUR 4.800,00 wurde am 01.03.2020 überwiesen"
        label = _amount_label_from_context(ctx)
        self.assertEqual(
            label,
            "Kaution",
            f"Reiner Kaution-Kontext muss weiterhin 'Kaution' ergeben, war: {label!r}",
        )


class KautionZahlungsaufforderungGuardTests(unittest.TestCase):
    """Bug 1: Kaution + IBAN/BIC-Kontext → Zahlungsaufforderung, nicht Kaution-Hinterlegung."""

    def test_kaution_mit_iban_raw_nicht_kaution(self):
        """'Kaution' + roher IBAN-Text → kein 'Kaution'-Label (Zahlungsaufforderung)."""
        ctx = (
            "Bitte überweisen Sie die ausstehende Kaution i.H.v. EUR 12.600,00 "
            "auf unser Konto: IBAN: DE89 5005 0201 0000 1234 56 BIC: HELADEF1822"
        )
        label = _amount_label_from_context(ctx)
        self.assertNotEqual(
            label,
            "Kaution",
            f"Kaution + IBAN-Kontext (Zahlungsaufforderung) darf nicht 'Kaution' ergeben, war: {label!r}",
        )

    def test_kaution_mit_iban_placeholder_nicht_kaution(self):
        """'Kaution' + redacted Placeholder [iban_1]/[bic_1] → kein 'Kaution'-Label."""
        ctx = (
            "Wir fordern Sie auf, die Kaution EUR 12.600,00 auf folgendes Konto "
            "zu überweisen: [IBAN_1] [BIC_1] Verwendungszweck: Kaution/Bergmann"
        )
        label = _amount_label_from_context(ctx)
        self.assertNotEqual(
            label,
            "Kaution",
            f"Kaution + Placeholder-Kontext darf nicht 'Kaution' ergeben, war: {label!r}",
        )

    def test_kaution_ohne_iban_bleibt_kaution_regression(self):
        """Regression U9: 'Kaution' ohne IBAN/BIC → weiterhin 'Kaution' (U9-Fix bleibt intakt)."""
        ctx = (
            "Anderenfalls werden wir die Kaution i.H.v. EUR 12.600,00 verwerten, "
            "da ein Mietrückstand von 3 Monaten besteht."
        )
        label = _amount_label_from_context(ctx)
        self.assertEqual(
            label,
            "Kaution",
            f"Kaution ohne IBAN (U9-Regression) muss weiterhin 'Kaution' ergeben, war: {label!r}",
        )


class GesamtschadenVsMieteImMietstreitTests(unittest.TestCase):
    """Gesamtschaden im Mietstreit → 'Schadensersatz', niemals 'Miete'."""

    def test_gesamtschaden_im_mietstreit_ist_schadensersatz(self):
        """'Gesamtschaden' mit 'Mietausfall' im Kontext → 'Schadensersatz', nicht 'Miete'."""
        ctx = (
            "Gesamtschaden: EUR 12.900,00 "
            "(Reparaturkosten 8.400,00 EUR + Mietausfall 4.500,00 EUR)."
        )
        label = _amount_label_from_context(ctx)
        self.assertEqual(
            label,
            "Schadensersatz",
            f"Gesamtschaden im Mietstreit muss 'Schadensersatz' ergeben, war: {label!r}",
        )
        self.assertNotEqual(
            label,
            "Miete",
            f"Gesamtschaden darf nicht 'Miete' sein, war: {label!r}",
        )

    def test_human_label_gesamtschaden_mietstreit(self):
        """_human_label_for_evidence: Gesamtschaden im Mietstreit → 'Schadensersatz'."""
        ctx = "Gesamtschaden: EUR 12.900,00 (Reparaturkosten + Mietausfall)"
        label = _human_label_for_evidence("betraege", "EUR 12.900,00", ctx, "klageschrift")
        self.assertEqual(
            label,
            "Schadensersatz",
            f"_human_label_for_evidence muss 'Schadensersatz' zurückgeben, war: {label!r}",
        )
        self.assertNotEqual(
            label,
            "Miete",
            f"Gesamtschaden darf nicht als 'Miete' gelabelt werden, war: {label!r}",
        )

    def test_regression_echte_miete_bleibt_miete(self):
        """Regression: Echter Miete-Kontext (ohne Schadensposten) → weiterhin 'Miete'."""
        ctx = "Die monatliche Grundmiete beträgt EUR 4.200,00 netto"
        label = _amount_label_from_context(ctx)
        self.assertEqual(
            label,
            "Miete",
            f"Echter Miete-Kontext muss weiterhin 'Miete' ergeben, war: {label!r}",
        )


class FixAGesamtschadenInBetragKontextKeywordsTests(unittest.TestCase):
    """Fix A: 'gesamtschaden'/'schadensposten'/'reparaturkosten' in
    BETRAG_KONTEXT_KEYWORDS["schadensersatz"] → Stufe-1-Klassifikation mit confidence 1.0."""

    def test_gesamtschaden_confidence_1_auf_einzelzeile(self):
        """klassifiziere_betrag_mit_confidence: 'gesamtschaden' auf Einzelzeile → schadensersatz, confidence 1.0."""
        line = "Gesamtschaden: EUR 12.900,00 (Reparaturkosten EUR 8.400,00 + Mietausfall EUR 4.500,00)"
        result = klassifiziere_betrag_mit_confidence(line)
        self.assertEqual(
            result.feldname,
            "schadensersatz",
            f"'gesamtschaden' muss 'schadensersatz' triggern (Fix A), war: {result.feldname!r}",
        )
        self.assertEqual(
            result.confidence,
            1.0,
            f"confidence muss 1.0 sein, war: {result.confidence}",
        )

    def test_klassifiziere_betrag_mit_kontext_gesamtschaden_stufe1(self):
        """klassifiziere_betrag_mit_kontext: Stufe 1 erkennt 'gesamtschaden' auf der Zeile selbst,
        auch wenn Kontext 'rückstand' und 'streitwert' enthält."""
        line = "Gesamtschaden: EUR 12.900,00 (Reparaturkosten EUR 8.400,00 + Mietausfall EUR 4.500,00)"
        kontext = (
            "Offener Rückstand: EUR 12.600,00 laut Mahnung. "
            + line
            + " Streitwert: EUR 25.500,00"
        )
        result = klassifiziere_betrag_mit_kontext(line, kontext)
        self.assertEqual(
            result.feldname,
            "schadensersatz",
            f"Gesamtschaden-Zeile muss 'schadensersatz' sein (Stufe 1), war: {result.feldname!r}",
        )
        self.assertEqual(result.confidence, 1.0)


if __name__ == "__main__":
    unittest.main()
