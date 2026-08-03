"""
tests/test_betrag_parsing.py – PR#13: Betrags-Parsing-Korrekturen

P3 (Mittel): Vertragsstrafe-Betrag (EUR 10.000,00) wird fälschlich als
             "Gehalt" gelabelt, weil das Quote-Fenster "gehalt"/"vergütung"
             enthält; echtes Gehalt (EUR 4.600,00) wird ignoriert.

P4 (Mittel): Jahreszahl "2026" aus Datums-Token "01.07.2026 EUR 1.000,00"
             wird als "2026 EUR" extrahiert; echte Raten-Beträge fehlen
             oder werden durch den Jahreszahl-Artefakt verdrängt.
"""

import unittest

from core.evidence import build_evidence_map
from core.export_render import _human_label_for_evidence as render_human_label
from ui.components import _human_label_for_evidence as ui_human_label


class PR13P3VertragsstrafeNotGehaltTests(unittest.TestCase):
    """P3: Vertragsstrafe-Betrag darf nicht als 'Gehalt' gelabelt werden.

    Bug: Wenn das Quote-Fenster um EUR 10.000,00 das Wort 'Gehalt' enthält
    (z.B. weil §3 Gehalt und §6 Vertragsstrafe auf einer Seite nebeneinander
    stehen), gibt _human_label_for_evidence fälschlicherweise 'Gehalt' zurück,
    obwohl der eigentliche Kontext eine Vertragsstrafe ist.
    """

    # Quote, das BEIDES enthält: 'Gehalt' (von §3-Nähe) UND 'Vertragsstrafe'/'Verstoß'
    # — exakt der Fall aus dem Mixed PDF, Dok 2.
    MIXED_QUOTE = "Gehalt EUR 4.600 Vertragsstrafe EUR 10.000,00 je Verstoß"
    GEHALT_QUOTE = "Das monatliche Bruttogehalt beträgt EUR 4.600,00"

    def test_render_mixed_quote_vertragsstrafe_not_gehalt(self):
        """export_render: Quote mit 'Gehalt' + 'Vertragsstrafe' → kein 'Gehalt'-Label für 10.000."""
        label = render_human_label(
            "betraege", "EUR 10.000,00", self.MIXED_QUOTE, "arbeitsvertrag"
        )
        self.assertNotEqual(
            label, "Gehalt",
            f"EUR 10.000,00 mit Vertragsstrafe-Kontext darf nicht 'Gehalt' sein, war: {label!r}",
        )

    def test_ui_mixed_quote_vertragsstrafe_not_gehalt(self):
        """ui.components: Quote mit 'Gehalt' + 'Vertragsstrafe' → kein 'Gehalt'-Label für 10.000."""
        label = ui_human_label(
            "betraege", "EUR 10.000,00", self.MIXED_QUOTE, "arbeitsvertrag"
        )
        self.assertNotEqual(
            label, "Gehalt",
            f"EUR 10.000,00 mit Vertragsstrafe-Kontext darf nicht 'Gehalt' sein, war: {label!r}",
        )

    def test_render_verstos_token_not_gehalt(self):
        """export_render: Quote mit 'Verstoß' → kein 'Gehalt'-Label, auch ohne 'Vertragsstrafe'."""
        quote = "Vergütung Gehalt § 6 bei Verstoß: EUR 10.000,00 je Verstoß"
        label = render_human_label("betraege", "EUR 10.000,00", quote, "arbeitsvertrag")
        self.assertNotEqual(
            label, "Gehalt",
            f"EUR 10.000,00 mit 'Verstoß' im Kontext darf nicht 'Gehalt' sein, war: {label!r}",
        )

    def test_render_gehalt_only_quote_still_gehalt(self):
        """Regression: Quote NUR mit Gehalts-Kontext → weiterhin 'Gehalt' gelabelt."""
        label = render_human_label(
            "betraege", "EUR 4.600,00", self.GEHALT_QUOTE, "arbeitsvertrag"
        )
        self.assertEqual(
            label, "Gehalt",
            f"Echter Gehalts-Betrag muss weiterhin 'Gehalt' gelabelt werden, war: {label!r}",
        )

    def test_ui_gehalt_only_quote_still_gehalt(self):
        """Regression: Quote NUR mit Gehalts-Kontext → 'Gehalt' (ui)."""
        label = ui_human_label(
            "betraege", "EUR 4.600,00", self.GEHALT_QUOTE, "arbeitsvertrag"
        )
        self.assertEqual(
            label, "Gehalt",
            f"Echter Gehalts-Betrag muss weiterhin 'Gehalt' gelabelt werden, war: {label!r}",
        )

    def test_build_evidence_map_vertragsstrafe_nicht_gehalt_labeled(self):
        """build_evidence_map: Betraege mit Vertragsstrafe-Quote nicht als 'Gehalt' labeln.

        Reproduziert den P3-Bug: Gehalt + Vertragsstrafe auf einer Seite,
        EUR 10.000,00 liegt im 50-Zeichen-Fenster von 'Gehalt' → falsches Label.
        """
        # Kompaktes Layout: 'Gehalt' ist innerhalb des 50-Zeichen-Fensters von EUR 10.000,00
        pages = [
            {
                "page_number": 1,
                "text": (
                    "Vergütung Gehalt: EUR 4.600,00. "
                    "Vertragsstrafe bei Verstoß: EUR 10.000,00 je Verstoß."
                ),
            }
        ]
        ev = build_evidence_map(pages)
        betraege_items = [
            item for item in ev
            if item.get("field") == "betraege" and item.get("value") != "missing"
        ]
        self.assertTrue(betraege_items, "Mindestens ein Betrag muss extrahiert werden")

        for item in betraege_items:
            value = str(item.get("value", ""))
            quote = str(item.get("quote", ""))
            quote_l = quote.lower()
            is_vertragsstrafe_context = any(
                t in quote_l for t in ("vertragsstrafe", "verstoß", "verstoss")
            )
            if is_vertragsstrafe_context:
                for label_fn in (render_human_label, ui_human_label):
                    label = label_fn("betraege", value, quote, "arbeitsvertrag")
                    self.assertNotEqual(
                        label, "Gehalt",
                        f"Betrag {value!r} mit Vertragsstrafe-Kontext darf nicht 'Gehalt' sein "
                        f"(quote: {quote!r}), war: {label!r}",
                    )


class PR13P4JahreszahlNichtAlsBetragTests(unittest.TestCase):
    """P4: Jahreszahl aus Datums-Token soll nicht als Betrag extrahiert werden.

    Bug: 'amount_re' dritte Alternative (\d{1,4}...\s*EUR) matcht '2026 EUR',
    wenn direkt nach 'dd.mm.2026' die Währungsangabe folgt.
    Reproduzierbarer Fall: Text ohne anderen Betrag → '2026 EUR' wird top-betrag.
    """

    def test_jahreszahl_2026_isoliert_nicht_als_betrag(self):
        """'2026 EUR' allein (kein echter Betrag auf der Seite) darf nicht erscheinen."""
        # Reproduziert P4: "Fällig am 01.07.2026 EUR" → "2026 EUR" als Artefakt
        ev = build_evidence_map([{
            "page_number": 1,
            "text": "Fällig am 01.07.2026 EUR bitte zahlen.",
        }])
        betrag_values = [
            item["value"] for item in ev
            if item.get("field") == "betraege" and item.get("value") != "missing"
        ]
        jahres_betraege = [v for v in betrag_values if "2026" in v]
        self.assertEqual(
            jahres_betraege, [],
            f"'2026 EUR' (Jahreszahl aus Datums-Token) darf nicht als Betrag erscheinen, war: {betrag_values}",
        )

    def test_echter_ratenbetrag_1000_extrahiert(self):
        """Echter Ratenbetrag 'EUR 1.000,00' muss extrahiert werden."""
        ev = build_evidence_map([{
            "page_number": 1,
            "text": "Rate 1 | 01.07.2026 | EUR 1.000,00",
        }])
        betrag_values = [
            item["value"] for item in ev
            if item.get("field") == "betraege" and item.get("value") != "missing"
        ]
        has_1000 = any("1.000" in v or "1000" in v for v in betrag_values)
        self.assertTrue(
            has_1000,
            f"'EUR 1.000,00' muss als Betrag extrahiert werden, war: {betrag_values}",
        )

    def test_echter_ratenbetrag_997_isoliert_extrahiert(self):
        """Echter Ratenbetrag 'EUR 997,40' (allein auf der Seite) muss extrahiert werden."""
        ev = build_evidence_map([{
            "page_number": 1,
            "text": "Rate 2 | 01.08.2026 | EUR 997,40",
        }])
        betrag_values = [
            item["value"] for item in ev
            if item.get("field") == "betraege" and item.get("value") != "missing"
        ]
        has_997 = any("997" in v for v in betrag_values)
        self.assertTrue(
            has_997,
            f"'EUR 997,40' muss als Betrag extrahiert werden, war: {betrag_values}",
        )

    def test_normaler_kleinstbetrag_240_bleibt(self):
        """Regression: Normaler Kleinstbetrag '240 EUR' bleibt erhalten."""
        ev = build_evidence_map([{
            "page_number": 1,
            "text": "Gebühr: 240 EUR für Bearbeitungskosten.",
        }])
        betrag_values = [
            item["value"] for item in ev
            if item.get("field") == "betraege" and item.get("value") != "missing"
        ]
        has_240 = any("240" in v for v in betrag_values)
        self.assertTrue(
            has_240,
            f"'240 EUR' (normaler Kleinstbetrag) muss weiterhin extrahiert werden, war: {betrag_values}",
        )

    def test_jahreszahl_1990_in_datum_nicht_betrag(self):
        """Jahreszahl '1990 EUR' (aus Datum 01.01.1990 EUR) darf nicht als Betrag erscheinen."""
        ev = build_evidence_map([{
            "page_number": 1,
            "text": "Fällig am 01.01.1990 EUR bitte zahlen.",
        }])
        betrag_values = [
            item["value"] for item in ev
            if item.get("field") == "betraege" and item.get("value") != "missing"
        ]
        jahres_betraege = [v for v in betrag_values if "1990" in v]
        self.assertEqual(
            jahres_betraege, [],
            f"'1990 EUR' (Jahreszahl) darf nicht als Betrag erscheinen, war: {betrag_values}",
        )


if __name__ == "__main__":
    unittest.main()
