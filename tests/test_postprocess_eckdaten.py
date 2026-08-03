"""
tests/test_postprocess_eckdaten.py

Tests für _postprocess_eckdaten_items (core/labels.py):

1. Kaution-Schutz: "rueckstand"-Item mit Kautions-Betrag → wird entfernt
   (Bäckerei Dok 2: EUR 12.600,00 darf nicht als Rückstand erscheinen)

2. Label-Duplikat-Dedup: identisches (label, betrag) → zweiter Eintrag entfernt

3. Schadensersatz vs. Miete: Gesamtschaden-Betrag in Mietstreit-Kontext bleibt
   "Schadensersatz", taucht nicht als "Miete" auf
"""

import unittest

from core.labels import _postprocess_eckdaten_items, _amount_label_from_context


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

KAUTIONS_BETRAG = "EUR 12.600,00"
RUECKSTAND_BETRAG = "EUR 7.230,00"
SCHADEN_BETRAG = "EUR 12.900,00"

# Bäckerei Dok 2 – typische Evidence-Map-Items
_KAUTION_ITEM = {
    "field": "kaution",
    "value": KAUTIONS_BETRAG,
    "page": 1,
    "quote": "Kaution i.H.v. EUR 12.600,00 hinterlegt am 01.03.2020",
}
_RUECKSTAND_GLEICHER_BETRAG = {
    "field": "rueckstand",
    "value": KAUTIONS_BETRAG,
    "page": 3,
    "quote": "Rückstand EUR 12.600,00 laut Mahnung",
}
_RUECKSTAND_ITEM = {
    "field": "rueckstand",
    "value": RUECKSTAND_BETRAG,
    "page": 2,
    "quote": "Mietrückstand EUR 7.230,00 für Jan–März 2025",
}
_RUECKSTAND_DUPLIKAT = {
    "field": "rueckstand",
    "value": RUECKSTAND_BETRAG,
    "page": 4,
    "quote": "offener Rückstand EUR 7.230,00 noch nicht beglichen",
}
_SCHADEN_ITEM = {
    "field": "betraege",
    "value": SCHADEN_BETRAG,
    "page": 5,
    "quote": "Gesamtschaden: EUR 12.900,00 (Reparaturkosten + Mietausfall)",
}


class KautionSchutzTests(unittest.TestCase):
    """Kaution-Betrag als 'rueckstand'-Item wird entfernt."""

    def test_rueckstand_mit_kautions_betrag_entfernt(self):
        """rueckstand-Item für bekannten Kautions-Betrag → entfernt."""
        items = [_KAUTION_ITEM, _RUECKSTAND_GLEICHER_BETRAG, _RUECKSTAND_ITEM]
        result = _postprocess_eckdaten_items(items, "mietvertrag")
        values = [it["value"] for it in result]
        # Kaution-Betrag darf nicht als Rückstand erscheinen
        rueckstand_fields = [it["field"] for it in result if it["value"] == KAUTIONS_BETRAG]
        self.assertNotIn(
            "rueckstand",
            rueckstand_fields,
            f"rueckstand-Item für Kautions-Betrag {KAUTIONS_BETRAG!r} muss entfernt werden",
        )

    def test_kaution_item_bleibt_erhalten(self):
        """Das kaution-Item selbst bleibt nach Post-Processing erhalten."""
        items = [_KAUTION_ITEM, _RUECKSTAND_GLEICHER_BETRAG]
        result = _postprocess_eckdaten_items(items, "mietvertrag")
        kaution_items = [it for it in result if it["field"] == "kaution"]
        self.assertTrue(
            kaution_items,
            f"kaution-Item muss nach Post-Processing erhalten bleiben",
        )

    def test_echter_rueckstand_anderer_betrag_bleibt(self):
        """Echter Rückstand (anderer Betrag als Kaution) bleibt erhalten."""
        items = [_KAUTION_ITEM, _RUECKSTAND_GLEICHER_BETRAG, _RUECKSTAND_ITEM]
        result = _postprocess_eckdaten_items(items, "mietvertrag")
        rueckstand_7230 = [it for it in result if it["value"] == RUECKSTAND_BETRAG]
        self.assertTrue(
            rueckstand_7230,
            f"Rückstand EUR 7.230,00 (anderer Betrag als Kaution) muss erhalten bleiben",
        )

    def test_baeckerei_dok2_kein_rueckstand_fuer_kaution(self):
        """Bäckerei Dok 2: EUR 12.600,00 nicht als Rückstand in ECKDATEN."""
        items = [_KAUTION_ITEM, _RUECKSTAND_GLEICHER_BETRAG, _RUECKSTAND_ITEM]
        result = _postprocess_eckdaten_items(items, "mietvertrag")
        # EUR 12.600,00 darf nur als Kaution erscheinen, nicht als Rückstand
        for it in result:
            if it["value"] == KAUTIONS_BETRAG:
                self.assertEqual(
                    it["field"],
                    "kaution",
                    f"EUR 12.600,00 darf nur als 'kaution' erscheinen, war: {it['field']!r}",
                )


class DuplikatDeduplicationTests(unittest.TestCase):
    """Identisches (label, betrag) → nur erster Eintrag bleibt."""

    def test_rueckstand_duplikat_entfernt(self):
        """Zwei rueckstand-Items mit gleichem Betrag → nur einer bleibt."""
        items = [_RUECKSTAND_ITEM, _RUECKSTAND_DUPLIKAT]
        result = _postprocess_eckdaten_items(items, "mietvertrag")
        rueckstand_items = [it for it in result if it["value"] == RUECKSTAND_BETRAG]
        self.assertEqual(
            len(rueckstand_items),
            1,
            f"EUR 7.230,00 Rückstand darf nur einmal erscheinen, war: {len(rueckstand_items)}x",
        )

    def test_erste_quelle_behalten(self):
        """Bei Duplikat wird der erste Eintrag (erste Seite) behalten."""
        items = [_RUECKSTAND_ITEM, _RUECKSTAND_DUPLIKAT]
        result = _postprocess_eckdaten_items(items, "mietvertrag")
        rueckstand_items = [it for it in result if it["value"] == RUECKSTAND_BETRAG]
        self.assertEqual(
            rueckstand_items[0]["page"],
            2,
            "Erste Quelle (S. 2) muss behalten werden, nicht die zweite (S. 4)",
        )

    def test_verschiedene_betraege_beide_behalten(self):
        """Zwei Rückstand-Items mit VERSCHIEDENEN Beträgen → beide bleiben."""
        item_a = {**_RUECKSTAND_ITEM, "value": "EUR 7.230,00"}
        item_b = {**_RUECKSTAND_ITEM, "value": "EUR 3.840,00", "page": 3}
        result = _postprocess_eckdaten_items([item_a, item_b], "mietvertrag")
        rueckstand_values = {it["value"] for it in result if it["field"] == "rueckstand"}
        self.assertIn("EUR 7.230,00", rueckstand_values)
        self.assertIn("EUR 3.840,00", rueckstand_values)


class SchadensersatzVsMietePostprocessTests(unittest.TestCase):
    """Gesamtschaden in Mietstreit: nach Post-Processing kein 'Miete'-Label."""

    def test_gesamtschaden_item_bleibt_schadensersatz(self):
        """'betraege'-Item mit Gesamtschaden-Quote → bleibt im Output (label=Schadensersatz)."""
        items = [_SCHADEN_ITEM]
        result = _postprocess_eckdaten_items(items, "klageschrift")
        self.assertEqual(len(result), 1, "Schadensersatz-Item muss erhalten bleiben")
        # Sicherstellen dass der Betrag als Schadensersatz-Kontext erkannt wird
        from core.labels import _amount_label_from_context
        label = _amount_label_from_context(_SCHADEN_ITEM["quote"])
        self.assertEqual(
            label,
            "Schadensersatz",
            f"Gesamtschaden-Kontext muss 'Schadensersatz' ergeben, war: {label!r}",
        )
        self.assertNotEqual(label, "Miete", "Gesamtschaden darf nicht 'Miete' sein")

    def test_miete_und_schaden_kein_label_konflikt(self):
        """Miete- und Schadensersatz-Item im selben Output → beide erhalten (versch. Label)."""
        miete_item = {
            "field": "miete",
            "value": "EUR 4.820,00",
            "page": 1,
            "quote": "Grundmiete EUR 4.820,00 monatlich",
        }
        items = [miete_item, _SCHADEN_ITEM]
        result = _postprocess_eckdaten_items(items, "klageschrift")
        self.assertEqual(len(result), 2, "Miete- und Schadensersatz-Item müssen beide erhalten bleiben")


class DocumentAnalysisPromptEckdatenTests(unittest.TestCase):
    """_document_analysis_prompt enthält die ECKDATEN-Pflichtregeln."""

    def _get_prompt(self) -> str:
        from core.pipeline import _document_analysis_prompt
        return _document_analysis_prompt(
            segment_doc_class="Mietvertrag",
            allow_llm_doc_class_guess=False,
        )

    def test_kaution_regel_in_prompt(self):
        """Kaution-vs-Rückstand-Regel muss im Analyse-Prompt stehen."""
        prompt = self._get_prompt()
        self.assertIn(
            "Kaution",
            prompt,
            "ECKDATEN-Kaution-Regel fehlt in _document_analysis_prompt",
        )
        self.assertIn(
            "Rückstand",
            prompt,
            "ECKDATEN-Rückstand-Regel fehlt in _document_analysis_prompt",
        )

    def test_parteienregel_in_prompt(self):
        """PARTEIENREGEL (kein Dokumenttitel als Partei) muss im Analyse-Prompt stehen."""
        prompt = self._get_prompt()
        self.assertIn(
            "MÄNGELANZEIGE",
            prompt,
            "PARTEIENREGEL mit MÄNGELANZEIGE fehlt in _document_analysis_prompt",
        )


if __name__ == "__main__":
    unittest.main()
