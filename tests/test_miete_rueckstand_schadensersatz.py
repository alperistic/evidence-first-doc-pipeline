"""
tests/test_miete_rueckstand_schadensersatz.py

Prüft die Unterscheidung zwischen Monatsmiete, Rückstand und Schadensersatz
gemäß den Prompt-Präzisierungen in core/prompts.py AKTENVERMERK_PROMPT.

1. 4.820 EUR als Monatsmiete erkannt (nicht Rückstand)
2. 12.900 EUR als Schadensersatz erkannt (nicht Miete)
"""

import unittest

from core.labels import _amount_label_from_context, _human_label_for_evidence
from core.evidence_map import build_evidence_map


class MonatsmieteVsRueckstandTests(unittest.TestCase):
    """EUR 4.820,00 im Monatsmiete-Kontext → 'Monatsmiete', nicht 'Rückstand'."""

    KONTEXT = (
        "Die monatliche Zahllast (Grundmiete EUR 4.120,00 zzgl. Betriebskostenvorauszahlung "
        "EUR 700,00) beträgt EUR 4.820,00 fällig jeweils am 3. Werktag des Monats."
    )

    def test_amount_label_monatsmiete(self):
        """_amount_label_from_context: monatliche Zahllast → 'Monatsmiete'."""
        label = _amount_label_from_context(self.KONTEXT)
        self.assertEqual(
            label,
            "Monatsmiete",
            f"EUR 4.820,00 im Zahllast-Kontext muss 'Monatsmiete' ergeben, war: {label!r}",
        )

    def test_amount_label_nicht_rueckstand(self):
        """_amount_label_from_context: monatliche Zahllast → nicht 'Rückstand'."""
        label = _amount_label_from_context(self.KONTEXT)
        self.assertNotEqual(
            label,
            "Rückstand",
            f"EUR 4.820,00 im Zahllast-Kontext darf nicht 'Rückstand' sein, war: {label!r}",
        )

    def test_human_label_monatsmiete(self):
        """_human_label_for_evidence: monatliche Zahllast → 'Monatsmiete'."""
        label = _human_label_for_evidence("betraege", "EUR 4.820,00", self.KONTEXT, "mietvertrag")
        self.assertEqual(
            label,
            "Monatsmiete",
            f"_human_label_for_evidence muss 'Monatsmiete' zurückgeben, war: {label!r}",
        )

    def test_faellig_jeweils_am_als_monatsmiete(self):
        """'fällig jeweils am' allein reicht als Monatsmiete-Signal."""
        ctx = "Mietzins EUR 4.820,00 fällig jeweils am 5. Werktag"
        label = _amount_label_from_context(ctx)
        self.assertEqual(
            label,
            "Monatsmiete",
            f"'fällig jeweils am' muss 'Monatsmiete' triggern, war: {label!r}",
        )

    def test_regression_rueckstand_weiterhin_erkannt(self):
        """Regression: Echter Rückstand-Kontext liefert weiterhin 'Rückstand'."""
        ctx = "Offener Rückstand EUR 7.230,00 für Januar bis März 2025 nicht geleistet"
        label = _amount_label_from_context(ctx)
        self.assertEqual(
            label,
            "Rückstand",
            f"Echter Rückstand-Kontext muss weiterhin 'Rückstand' ergeben, war: {label!r}",
        )


class SchadensersatzVsMieteTests(unittest.TestCase):
    """EUR 12.900,00 als Gesamtschaden → 'Schadensersatz', nicht 'Miete'."""

    KONTEXT = (
        "Gesamtschaden: EUR 12.900,00 als Summe der Schadensposten "
        "(Reparaturkosten 8.400,00 EUR + Mietausfall 4.500,00 EUR)."
    )

    def test_amount_label_schadensersatz(self):
        """_amount_label_from_context: Gesamtschaden → 'Schadensersatz'."""
        label = _amount_label_from_context(self.KONTEXT)
        self.assertEqual(
            label,
            "Schadensersatz",
            f"EUR 12.900,00 im Gesamtschaden-Kontext muss 'Schadensersatz' ergeben, war: {label!r}",
        )

    def test_amount_label_nicht_miete(self):
        """_amount_label_from_context: Gesamtschaden → nicht 'Miete'."""
        label = _amount_label_from_context(self.KONTEXT)
        self.assertNotEqual(
            label,
            "Miete",
            f"EUR 12.900,00 im Gesamtschaden-Kontext darf nicht 'Miete' sein, war: {label!r}",
        )

    def test_human_label_schadensersatz(self):
        """_human_label_for_evidence: Gesamtschaden → 'Schadensersatz'."""
        label = _human_label_for_evidence("betraege", "EUR 12.900,00", self.KONTEXT, "klageschrift")
        self.assertEqual(
            label,
            "Schadensersatz",
            f"_human_label_for_evidence muss 'Schadensersatz' zurückgeben, war: {label!r}",
        )

    def test_schadensposten_als_schadensersatz(self):
        """'Schadensposten' allein reicht als Schadensersatz-Signal."""
        ctx = "Summe der Schadensposten EUR 12.900,00 laut Gutachten"
        label = _amount_label_from_context(ctx)
        self.assertEqual(
            label,
            "Schadensersatz",
            f"'Schadensposten'-Kontext muss 'Schadensersatz' ergeben, war: {label!r}",
        )

    def test_regression_miete_weiterhin_erkannt(self):
        """Regression: Echter Miete-Kontext liefert weiterhin 'Miete'."""
        ctx = "Die monatliche Grundmiete beträgt EUR 4.200,00 netto"
        label = _amount_label_from_context(ctx)
        self.assertEqual(
            label,
            "Miete",
            f"Echter Miete-Kontext muss weiterhin 'Miete' ergeben, war: {label!r}",
        )


class GesamtzahllastPipelineTests(unittest.TestCase):
    """EUR 4.820,00 als Gesamtzahllast erscheint in ECKDATEN als 'Monatsmiete'."""

    FIXTURE_TEXT = (
        "§ 5 Mietzins\n"
        "Die monatliche Zahllast (Grundmiete EUR 4.120,00 zzgl. "
        "Betriebskostenvorauszahlung EUR 700,00) beträgt EUR 4.820,00 "
        "fällig jeweils am 3. Werktag des Monats.\n"
    )

    def _get_ev(self):
        pages = [{"page_number": 1, "text": self.FIXTURE_TEXT}]
        return build_evidence_map(pages)

    def test_gesamtzahllast_in_evidence_map(self):
        """EUR 4.820,00 landet als 'betraege'-Item in der Evidence Map."""
        ev = self._get_ev()
        betraege_items = [
            i for i in ev
            if i.get("field") == "betraege" and "4.820" in i.get("value", "")
        ]
        self.assertTrue(
            betraege_items,
            f"EUR 4.820,00 muss als 'betraege'-Item in der Evidence Map erscheinen. ev={ev}",
        )

    def test_gesamtzahllast_label_monatsmiete(self):
        """EUR 4.820,00 bekommt das Label 'Monatsmiete' in ECKDATEN."""
        ev = self._get_ev()
        for item in ev:
            if item.get("field") == "betraege" and "4.820" in item.get("value", ""):
                label = _human_label_for_evidence(
                    item["field"], item["value"], item.get("quote", ""), "mietvertrag"
                )
                self.assertEqual(
                    label,
                    "Monatsmiete",
                    f"EUR 4.820,00 muss 'Monatsmiete' ergeben, war: {label!r}",
                )
                return
        self.fail("EUR 4.820,00 als 'betraege'-Item nicht gefunden.")

    def test_nebenkosten_bleibt_erhalten(self):
        """EUR 700,00 (Betriebskosten) bleibt als 'nebenkosten'-Item erhalten."""
        ev = self._get_ev()
        nk_items = [
            i for i in ev
            if i.get("field") == "nebenkosten" and i.get("value", "missing") != "missing"
        ]
        self.assertTrue(
            nk_items,
            f"Betriebskosten-Item muss als 'nebenkosten' erhalten bleiben. ev={ev}",
        )

    def test_gesamtzahllast_nicht_als_nebenkosten(self):
        """EUR 4.820,00 darf nicht als 'nebenkosten'-Item klassifiziert werden."""
        ev = self._get_ev()
        nk_4820 = [
            i for i in ev
            if i.get("field") == "nebenkosten" and "4.820" in i.get("value", "")
        ]
        self.assertFalse(
            nk_4820,
            f"EUR 4.820,00 darf nicht als 'nebenkosten' erscheinen, war: {nk_4820}",
        )


class GesamtzahllastPattern2Tests(unittest.TestCase):
    """Pattern 2: 'Zahllast: EUR X' (Doppelpunkt-Label) → 'betraege' / 'Monatsmiete'."""

    FIXTURE_TEXT = (
        "§ 5 Mietzins\n"
        "Gesamte monatliche Zahllast: EUR 4.820,00 zzgl. USt., "
        "fällig jeweils am 3. Werktag des Monats.\n"
    )

    def _get_ev(self):
        pages = [{"page_number": 1, "text": self.FIXTURE_TEXT}]
        return build_evidence_map(pages)

    def test_p2_in_evidence_map(self):
        """Pattern 2: EUR 4.820,00 landet als 'betraege'-Item."""
        ev = self._get_ev()
        items = [i for i in ev if i.get("field") == "betraege" and "4.820" in i.get("value", "")]
        self.assertTrue(
            items,
            f"EUR 4.820,00 (Pattern 2) muss als 'betraege'-Item erscheinen. ev={ev}",
        )

    def test_p2_label_monatsmiete(self):
        """Pattern 2: EUR 4.820,00 bekommt Label 'Monatsmiete'."""
        ev = self._get_ev()
        for item in ev:
            if item.get("field") == "betraege" and "4.820" in item.get("value", ""):
                label = _human_label_for_evidence(
                    item["field"], item["value"], item.get("quote", ""), "mietvertrag"
                )
                self.assertEqual(
                    label,
                    "Monatsmiete",
                    f"EUR 4.820,00 (Pattern 2) muss 'Monatsmiete' ergeben, war: {label!r}",
                )
                return
        self.fail("EUR 4.820,00 als 'betraege'-Item nicht gefunden.")


class ZahllastOverrideGuardTests(unittest.TestCase):
    """Zahllast-Override darf ohne 'zahllast' im linken Kontext nicht auslösen.

    Regression-Guard: Nebenkosten-Betrag mit 'beträgt … fällig jeweils am' (alter
    False-Positive-Trigger) muss als 'nebenkosten' erhalten bleiben.
    """

    FIXTURE_TEXT = (
        "§ 7 Nebenkosten\n"
        "Die Betriebskostenvorauszahlung beträgt EUR 350,00 "
        "fällig jeweils am Monatsende.\n"
    )

    def _get_ev(self):
        pages = [{"page_number": 1, "text": self.FIXTURE_TEXT}]
        return build_evidence_map(pages)

    def test_nebenkosten_ohne_zahllast_kein_override(self):
        """EUR 350,00 (Betriebskosten) ohne 'zahllast' bleibt 'nebenkosten', kein Override."""
        ev = self._get_ev()
        betraege_350 = [
            i for i in ev
            if "350" in i.get("value", "") and i.get("field") == "betraege"
        ]
        self.assertFalse(
            betraege_350,
            f"EUR 350,00 ohne 'zahllast' darf nicht als 'betraege' erscheinen. ev={ev}",
        )

    def test_nebenkosten_ohne_zahllast_bleibt_nebenkosten(self):
        """EUR 350,00 verbleibt als 'nebenkosten'-Item."""
        ev = self._get_ev()
        nk_350 = [
            i for i in ev
            if "350" in i.get("value", "") and i.get("field") == "nebenkosten"
        ]
        self.assertTrue(
            nk_350,
            f"EUR 350,00 muss als 'nebenkosten' erhalten bleiben. ev={ev}",
        )


if __name__ == "__main__":
    unittest.main()
