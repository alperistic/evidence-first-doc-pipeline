"""
tests/test_doctype_header_not_partei.py

Fix 1: Dokumenttyp-Header-Bezeichner (MÄNGELANZEIGE, KLAGESCHRIFT, URTEIL, …)
dürfen nicht als Parteienname extrahiert werden.

Fix 2: Cross-segment Kaution-Guard – Rückstand-Item in Dok 2 wird entfernt
wenn der gleiche Betrag aus einer Kaution in Dok 1 bekannt ist.
"""

import unittest

from core.evidence import (
    _is_doc_type_header,
    _extract_party_values_from_text,
    build_evidence_map,
)
from core.labels import (
    _collect_all_kaution_amounts,
    _postprocess_eckdaten_items,
)


# ---------------------------------------------------------------------------
# Fix 1A: _is_doc_type_header Einheitstests
# ---------------------------------------------------------------------------

class IsDocTypeHeaderTests(unittest.TestCase):
    """_is_doc_type_header erkennt Dokumenttyp-Bezeichner korrekt."""

    def test_maengelanzeige_erkannt(self):
        self.assertTrue(_is_doc_type_header("MÄNGELANZEIGE"))

    def test_maengelanzeige_mit_suffix_erkannt(self):
        """'MÄNGELANZEIGE Bäckerei GmbH' → True (Header-Wort am Anfang)."""
        self.assertTrue(_is_doc_type_header("MÄNGELANZEIGE Bäckerei GmbH & Co. KG"))

    def test_klageschrift_erkannt(self):
        self.assertTrue(_is_doc_type_header("KLAGESCHRIFT"))

    def test_urteil_erkannt(self):
        self.assertTrue(_is_doc_type_header("URTEIL"))

    def test_mahnung_erkannt(self):
        self.assertTrue(_is_doc_type_header("MAHNUNG"))

    def test_kuendigung_erkannt(self):
        self.assertTrue(_is_doc_type_header("KÜNDIGUNG"))

    def test_antwortschreiben_erkannt(self):
        self.assertTrue(_is_doc_type_header("ANTWORTSCHREIBEN"))

    def test_sachverstaendigengutachten_erkannt(self):
        self.assertTrue(_is_doc_type_header("SACHVERSTÄNDIGENGUTACHTEN"))

    def test_echte_person_nicht_erkannt(self):
        self.assertFalse(_is_doc_type_header("Max Mustermann"))

    def test_echte_firma_nicht_erkannt(self):
        self.assertFalse(_is_doc_type_header("Bäckerei Goldkruste GmbH"))

    def test_kurzname_nicht_erkannt(self):
        self.assertFalse(_is_doc_type_header("Dr. Anna Weber"))

    def test_case_insensitive(self):
        """Klein-/Großschreibung egal."""
        self.assertTrue(_is_doc_type_header("Mängelanzeige"))
        self.assertTrue(_is_doc_type_header("klageschrift"))


# ---------------------------------------------------------------------------
# Fix 1B: _extract_party_values_from_text filtert Dokumenttyp-Header heraus
# ---------------------------------------------------------------------------

class PartyExtractionFiltersDocTypeTests(unittest.TestCase):
    """Dokumenttyp-Header werden nicht als Parteien extrahiert."""

    def test_maengelanzeige_nicht_als_partei(self):
        """'Klägerin: MÄNGELANZEIGE Bäckerei ...' → MÄNGELANZEIGE wird verworfen,
        echte Partei extrahiert wenn erkennbar."""
        text = (
            "Dokumenttyp: Klageschrift\n"
            "Klägerin: MÄNGELANZEIGE Bäckerei Goldkruste GmbH\n"
            "Beklagter: [PARTEI_B]"
        )
        parties = _extract_party_values_from_text(text)
        # "MÄNGELANZEIGE Bäckerei Goldkruste GmbH" – der gesamte Kandidat
        # enthält MÄNGELANZEIGE → muss verworfen werden
        for party in parties:
            self.assertFalse(
                _is_doc_type_header(party),
                f"Dokumenttyp-Header darf nicht als Partei extrahiert werden: {party!r}",
            )

    def test_urteil_nicht_als_partei(self):
        text = "Klägerin: URTEIL Landgericht Frankfurt"
        parties = _extract_party_values_from_text(text)
        for party in parties:
            self.assertNotIn(
                "URTEIL",
                party.upper(),
                f"'URTEIL' darf nicht Teil eines Parteinamens sein: {party!r}",
            )

    def test_echte_partei_weiterhin_extrahiert(self):
        """Echter Parteiname ohne Dokumenttyp-Header bleibt erhalten."""
        text = "Klägerin: Anna Bergmann\nBeklagter: TechVenture GmbH"
        parties = _extract_party_values_from_text(text)
        self.assertTrue(
            any("Bergmann" in p or "TechVenture" in p for p in parties),
            f"Echte Parteien müssen weiterhin extrahiert werden, war: {parties}",
        )


# ---------------------------------------------------------------------------
# Fix 1C: build_evidence_map liefert keine Dokumenttyp-Header als Parteien
# ---------------------------------------------------------------------------

class BuildEvidenceMapPartyFilterTests(unittest.TestCase):
    """build_evidence_map filtert Dokumenttyp-Header aus parteien-Items."""

    def test_maengelanzeige_im_text_nicht_als_partei(self):
        """Wenn Seitenheader 'MÄNGELANZEIGE' im Text → nicht als parteien-Item."""
        pages = [{
            "page_number": 1,
            "text": (
                "MÄNGELANZEIGE\n"
                "Zwischen Bäckerei Goldkruste GmbH (Klägerin)\n"
                "und Immobilien AG (Beklagte)\n"
                "betreffend Mängel in den Mieträumen."
            ),
        }]
        ev = build_evidence_map(pages)
        party_items = [it for it in ev if it.get("field") == "parteien" and it.get("value") != "missing"]
        for item in party_items:
            val = str(item.get("value", ""))
            self.assertFalse(
                _is_doc_type_header(val),
                f"Dokumenttyp-Header darf nicht als Partei erscheinen: {val!r}",
            )


# ---------------------------------------------------------------------------
# Fix 2: Cross-segment Kaution-Guard
# ---------------------------------------------------------------------------

class CrossSegmentKautionGuardTests(unittest.TestCase):
    """Rückstand-Item mit Kautions-Betrag aus ANDEREM Segment wird entfernt."""

    KAUTION_BETRAG = "EUR 12.600,00"
    RUECKSTAND_BETRAG = "EUR 7.230,00"

    # Dok 1: hat Kaution EUR 12.600,00
    SEG_DOK1 = {
        "segment_meta": {"page_start": 1, "page_end": 3},
        "evidence_map": [
            {"field": "kaution", "value": "EUR 12.600,00", "page": 1,
             "quote": "Kaution i.H.v. EUR 12.600,00 hinterlegt"},
        ],
    }
    # Dok 2: hat Rückstand EUR 12.600,00 (gleicher Betrag wie Kaution aus Dok 1)
    SEG_DOK2 = {
        "segment_meta": {"page_start": 4, "page_end": 6},
        "evidence_map": [
            {"field": "rueckstand", "value": "EUR 12.600,00", "page": 5,
             "quote": "Rückstand EUR 12.600,00 laut Mahnung"},
            {"field": "rueckstand", "value": "EUR 7.230,00", "page": 5,
             "quote": "weiterer Rückstand EUR 7.230,00"},
        ],
    }

    def test_collect_all_kaution_amounts(self):
        """_collect_all_kaution_amounts sammelt Kautions-Beträge aus allen Segmenten."""
        segment_results = [self.SEG_DOK1, self.SEG_DOK2]
        amounts = _collect_all_kaution_amounts(segment_results)
        self.assertIn(
            "eur 12.600,00",
            amounts,
            f"EUR 12.600,00 muss in known_kaution_amounts sein, war: {amounts}",
        )

    def test_rueckstand_gleicher_betrag_cross_segment_entfernt(self):
        """Rückstand EUR 12.600,00 in Dok 2 wird entfernt weil Kaution aus Dok 1 bekannt."""
        segment_results = [self.SEG_DOK1, self.SEG_DOK2]
        known = _collect_all_kaution_amounts(segment_results)

        dok2_items = self.SEG_DOK2["evidence_map"]
        result = _postprocess_eckdaten_items(dok2_items, "mietvertrag", known_kaution_amounts=known)

        rueckstand_12600 = [it for it in result if it["value"] == self.KAUTION_BETRAG]
        self.assertEqual(
            len(rueckstand_12600),
            0,
            f"Rückstand {self.KAUTION_BETRAG!r} aus Dok 2 muss entfernt werden "
            f"(gleicher Betrag ist Kaution in Dok 1), war: {rueckstand_12600}",
        )

    def test_echter_rueckstand_anderer_betrag_bleibt_cross_segment(self):
        """Echter Rückstand EUR 7.230,00 (anderer Betrag als Kaution) bleibt."""
        segment_results = [self.SEG_DOK1, self.SEG_DOK2]
        known = _collect_all_kaution_amounts(segment_results)

        dok2_items = self.SEG_DOK2["evidence_map"]
        result = _postprocess_eckdaten_items(dok2_items, "mietvertrag", known_kaution_amounts=known)

        rueckstand_7230 = [it for it in result if it["value"] == self.RUECKSTAND_BETRAG]
        self.assertTrue(
            rueckstand_7230,
            f"Rückstand {self.RUECKSTAND_BETRAG!r} muss erhalten bleiben (anderer Betrag als Kaution)",
        )

    def test_leeres_known_kaution_keine_aenderung(self):
        """Ohne known_kaution_amounts: kein cross-segment Effekt."""
        dok2_items = self.SEG_DOK2["evidence_map"]
        result = _postprocess_eckdaten_items(dok2_items, "mietvertrag", known_kaution_amounts=frozenset())
        # Ohne cross-segment: beide Rückstand-Items bleiben
        values = {it["value"] for it in result}
        self.assertIn(self.KAUTION_BETRAG, values)
        self.assertIn(self.RUECKSTAND_BETRAG, values)

    def test_collect_empty_segments(self):
        """_collect_all_kaution_amounts mit leerer Liste → leeres Set."""
        amounts = _collect_all_kaution_amounts([])
        self.assertEqual(amounts, frozenset())

    def test_kaution_suffix_prefix_variante_matcht(self):
        """Kaution 'EUR 12.600,00' vs. Rückstand '12.600,00 EUR' → Guard feuert trotzdem."""
        seg_dok1 = {
            "evidence_map": [
                {"field": "kaution", "value": "EUR 12.600,00", "page": 1,
                 "quote": "Kaution i.H.v. EUR 12.600,00"},
            ],
        }
        seg_dok2 = {
            "evidence_map": [
                {"field": "rueckstand", "value": "12.600,00 EUR", "page": 4,
                 "quote": "Rückstand 12.600,00 EUR laut Mahnung"},
            ],
        }
        known = _collect_all_kaution_amounts([seg_dok1, seg_dok2])
        result = _postprocess_eckdaten_items(
            seg_dok2["evidence_map"], "mietvertrag", known_kaution_amounts=known
        )
        values = [it["value"] for it in result]
        self.assertNotIn(
            "12.600,00 EUR", values,
            "Rückstand mit Suffix-EUR muss durch Guard entfernt werden wenn gleicher Betrag Kaution ist",
        )

    def test_betraege_field_kaution_kontext_wird_gesammelt(self):
        """field='betraege' + Kautions-Kontext im Quote → Betrag wird in known_kaution aufgenommen."""
        seg = {
            "evidence_map": [
                {"field": "betraege", "value": "EUR 3.800,00", "page": 1,
                 "quote": "Kaution i.H.v. EUR 3.800,00 hinterlegt"},
            ],
        }
        amounts = _collect_all_kaution_amounts([seg])
        self.assertTrue(
            any("3.800,00" in a for a in amounts),
            f"Betrag mit Kautions-Kontext im Quote muss gesammelt werden, amounts={amounts}",
        )


# ---------------------------------------------------------------------------
# Fix 3B: _is_doc_type_header-Filter in collect_structured_parties_for_render
# ---------------------------------------------------------------------------

class CollectPartiesDocTypeHeaderFilterTests(unittest.TestCase):
    """Dokumenttyp-Header darf nicht als Parteienname in der Render-Ausgabe erscheinen."""

    def test_maengelanzeige_firma_nicht_als_partei(self):
        """'MÄNGELANZEIGE Bäckerei Sonnenschein GmbH & Co.' darf nicht als Partei gerendert werden."""
        from core.export_render import collect_structured_parties_for_render
        analysis = {
            "parties": [
                {"text": "MÄNGELANZEIGE Bäckerei Sonnenschein GmbH & Co.", "citations": []},
                {"text": "Vermieter: [PARTEI_A]", "citations": []},
            ],
        }
        result = collect_structured_parties_for_render(analysis, max_items=4)
        values = [p["value"] for p in result]
        for v in values:
            self.assertFalse(
                _is_doc_type_header(v),
                f"Dokumenttyp-Header '{v}' darf nicht als Partei erscheinen",
            )

    def test_echte_partei_bleibt_erhalten(self):
        """Eine echte juristische Person wird NICHT gefiltert."""
        from core.export_render import collect_structured_parties_for_render
        analysis = {
            "parties": [
                {"text": "Vermieterin: Bäckerei Sonnenschein GmbH & Co. KG", "citations": []},
            ],
        }
        result = collect_structured_parties_for_render(analysis, max_items=4)
        self.assertTrue(result, "Echte Partei muss in der Ausgabe erscheinen")
        self.assertIn("Bäckerei Sonnenschein GmbH & Co. KG", result[0]["value"])


if __name__ == "__main__":
    unittest.main()
