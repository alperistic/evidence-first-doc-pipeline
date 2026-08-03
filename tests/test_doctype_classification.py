"""
Document-type classification and rendering: which document type is shown
to the user (LLM-extracted vs. heuristic segmenter guess), the always-on
auto_classify pipeline setting, doc-type-dependent label choice for the
"Beginn"/"Mietbeginn"/"Beschäftigungsbeginn" Eckdaten field (both in the
evidence-map label function and in the LLM-regex merge step), and the
audit-block document class aggregation (LLM-analyzed doc type over the
segmenter's guess).
"""

import re
import pathlib
import unittest

from core.export_render import extract_document_type_for_render, _merge_llm_regex_eckdaten
from core.labels import _human_label_for_evidence
from governance.doc_class_policy import normalize_doc_class


class DoctypeRenderFix1Tests(unittest.TestCase):
    """extract_document_type_for_render soll bei vorhandenem
    document_type_extracted den LLM-Wert zeigen statt den Heuristik-Wert aus
    dokumenttyp_parteien."""

    def test_document_type_extracted_takes_priority_over_heuristic(self):
        """LLM-extracted doctype beats dokumenttyp_parteien heuristic."""
        parsed = {
            "document_type_extracted": "Scheidungsfolgenvereinbarung",
            "dokumenttyp_parteien": "Dokumenttyp: Mietvertrag\nVermieter: ...",
        }
        result = extract_document_type_for_render(parsed)
        self.assertEqual(result, "Scheidungsfolgenvereinbarung")

    def test_document_type_extracted_beats_document_type_field(self):
        """document_type_extracted has priority over plain document_type."""
        parsed = {
            "document_type_extracted": "Testament",
            "document_type": "Unklar",
        }
        result = extract_document_type_for_render(parsed)
        self.assertEqual(result, "Testament")

    def test_unklar_extracted_falls_through_to_document_type(self):
        """'Unklar' in document_type_extracted is not used; falls back to document_type."""
        parsed = {
            "document_type_extracted": "Unklar",
            "document_type": "Klageschrift",
        }
        result = extract_document_type_for_render(parsed)
        self.assertEqual(result, "Klageschrift")

    def test_missing_extracted_falls_through_to_heuristic(self):
        """Empty document_type_extracted falls back to dokumenttyp_parteien."""
        parsed = {
            "document_type_extracted": "",
            "document_type": "",
            "dokumenttyp_parteien": "Dokumenttyp: Mietvertrag",
        }
        result = extract_document_type_for_render(parsed)
        self.assertEqual(result, "Mietvertrag")

    def test_no_llm_fields_returns_missing(self):
        """No LLM fields and no heuristic → 'missing'."""
        result = extract_document_type_for_render({})
        self.assertEqual(result, "missing")


def test_auto_classify_default_is_true():
    """auto_classify ist kein Toggle mehr – hardcoded True als fester
    Pipeline-Bestandteil. Prüft direkt die Quellcodedatei."""
    app_source = pathlib.Path("app.py").read_text(encoding="utf-8")
    # Entweder hardcoded True oder Toggle mit value=True
    hardcoded = re.search(r'auto_classify\s*=\s*True\b', app_source)
    toggle = re.search(
        r'auto_classify\s*=\s*st\.toggle\([^)]*value\s*=\s*(True|False)',
        app_source,
    )
    if toggle:
        assert toggle.group(1) == "True", (
            f"auto_classify default should be True but got {toggle.group(1)!r}"
        )
    else:
        assert hardcoded is not None, (
            "auto_classify must be hardcoded True or a toggle with value=True in app.py"
        )


class TestMietbeginnZusammenfassung(unittest.TestCase):
    """"Mietbeginn" darf im Zusammenfassung-Pfad (_merge_llm_regex_eckdaten)
    nur für Miet-/Räumungsdokumente erscheinen. Für alle anderen Doktypen wird
    "Mietbeginn" → "Beginn" umgeschrieben."""

    def _llm(self, label: str, wert: str = "01.04.2019") -> dict:
        return {"label": label, "wert": wert, "quelle": "S. 1"}

    def test_mietbeginn_kept_for_mietvertrag(self):
        """Mietvertrag: LLM 'Mietbeginn' bleibt 'Mietbeginn'."""
        merged = _merge_llm_regex_eckdaten([self._llm("Mietbeginn")], [], "mietvertrag")
        labels = [i["label"] for i in merged]
        self.assertIn("Mietbeginn", labels)

    def test_mietbeginn_relabeled_for_scheidungsfolgenvereinbarung(self):
        """Scheidungsfolgenvereinbarung: LLM 'Mietbeginn' → 'Beginn'."""
        merged = _merge_llm_regex_eckdaten(
            [self._llm("Mietbeginn")], [], "Scheidungsfolgenvereinbarung"
        )
        labels = [i["label"] for i in merged]
        self.assertNotIn("Mietbeginn", labels, f"Mietbeginn must be relabeled. Got: {labels}")
        self.assertIn("Beginn", labels, f"Should become 'Beginn'. Got: {labels}")

    def test_mietbeginn_relabeled_for_arbeitsvertrag(self):
        """Arbeitsvertrag: LLM 'Mietbeginn' → 'Beginn'."""
        merged = _merge_llm_regex_eckdaten([self._llm("Mietbeginn")], [], "Arbeitsvertrag")
        labels = [i["label"] for i in merged]
        self.assertNotIn("Mietbeginn", labels)
        self.assertIn("Beginn", labels)

    def test_mietbeginn_relabeled_for_kaufvertrag(self):
        """Kaufvertrag: LLM 'Mietbeginn' → 'Beginn'."""
        merged = _merge_llm_regex_eckdaten([self._llm("Mietbeginn")], [], "kaufvertrag")
        labels = [i["label"] for i in merged]
        self.assertNotIn("Mietbeginn", labels)
        self.assertIn("Beginn", labels)

    def test_mietbeginn_kept_for_raeumungsklage(self):
        """Räumungsklage: LLM 'Mietbeginn' bleibt 'Mietbeginn'."""
        merged = _merge_llm_regex_eckdaten([self._llm("Mietbeginn")], [], "Räumungsklage")
        labels = [i["label"] for i in merged]
        self.assertIn("Mietbeginn", labels)

    def test_other_llm_labels_unaffected(self):
        """Kaution, Miete etc. werden nicht umbenannt."""
        merged = _merge_llm_regex_eckdaten(
            [self._llm("Kaution", "EUR 2.500,00"), self._llm("Miete", "EUR 900,00")],
            [],
            "kaufvertrag",
        )
        labels = [i["label"] for i in merged]
        self.assertIn("Kaution", labels)
        self.assertIn("Miete", labels)


class TestMietbeginnLabel(unittest.TestCase):
    """Label "Mietbeginn" darf NUR vergeben werden wenn der doc_type
    Miet-Kontext hat."""

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


def _build_doc_class_from_segment_results(segment_results):
    """Repliziert die doc_class-Aggregation aus services/analysis_service.py."""
    return ", ".join(
        sorted(
            {
                normalize_doc_class(
                    (item.get("doc_class_fields", {}) or {}).get("llm_doc_class_guess")
                    or (item.get("doc_class_fields", {}) or {}).get("segment_doc_class")
                )
                for item in segment_results
            }
        )
    ) or "Unklar"


class TestAuditDocClass(unittest.TestCase):
    """Dokumentklasse im Audit-Block muss aus den tatsächlichen
    LLM-analysierten doc_type-Werten der Segmente kommen, nicht aus dem
    Segmenter-Guess. llm_doc_class_guess hat Vorrang über segment_doc_class."""

    def test_llm_guess_overrides_segmenter(self):
        """LLM hat 'Notarielle Beurkundung' → Audit-Block zeigt das, nicht Segmenter-Guess."""
        segment_results = [
            {
                "doc_class_fields": {
                    "segment_doc_class": "Arbeitsvertrag",  # Segmenter-Guess (falsch)
                    "llm_doc_class_guess": "Notarielle Beurkundung",  # LLM (korrekt)
                }
            }
        ]
        doc_class = _build_doc_class_from_segment_results(segment_results)
        self.assertEqual(doc_class, "Notarielle Beurkundung")
        self.assertNotIn("Arbeitsvertrag", doc_class)

    def test_fallback_to_segment_doc_class_when_no_llm_guess(self):
        """Kein LLM-Guess → Segmenter-Klasse wird verwendet."""
        segment_results = [
            {
                "doc_class_fields": {
                    "segment_doc_class": "Mietvertrag",
                    # llm_doc_class_guess not set
                }
            }
        ]
        doc_class = _build_doc_class_from_segment_results(segment_results)
        self.assertEqual(doc_class, "Mietvertrag")

    def test_multiple_segments_aggregated(self):
        """Mehrere Segmente: LLM-Guess für jedes Segment wird aggregiert."""
        segment_results = [
            {
                "doc_class_fields": {
                    "segment_doc_class": "Kaufvertrag",  # wrong
                    "llm_doc_class_guess": "Notarielle Beurkundung",
                }
            },
            {
                "doc_class_fields": {
                    "segment_doc_class": "Mietvertrag",  # wrong
                    "llm_doc_class_guess": "Geschäftsanteilsabtretung",
                }
            },
        ]
        doc_class = _build_doc_class_from_segment_results(segment_results)
        self.assertIn("Notarielle Beurkundung", doc_class)
        self.assertIn("Geschäftsanteilsabtretung", doc_class)
        self.assertNotIn("Kaufvertrag", doc_class)
        self.assertNotIn("Mietvertrag", doc_class)

    def test_unklar_when_empty(self):
        """Keine Segmente → 'Unklar'."""
        doc_class = _build_doc_class_from_segment_results([])
        self.assertEqual(doc_class, "Unklar")

    def test_mixed_llm_and_segmenter(self):
        """Segment 1 hat LLM-Guess, Segment 2 nur Segmenter → beide erscheinen."""
        segment_results = [
            {
                "doc_class_fields": {
                    "segment_doc_class": "Kaufvertrag",
                    "llm_doc_class_guess": "Notarielle Beurkundung",
                }
            },
            {
                "doc_class_fields": {
                    "segment_doc_class": "Scheidungsfolgenvereinbarung",
                    # no llm_doc_class_guess
                }
            },
        ]
        doc_class = _build_doc_class_from_segment_results(segment_results)
        self.assertIn("Notarielle Beurkundung", doc_class)
        self.assertIn("Scheidungsfolgenvereinbarung", doc_class)
        self.assertNotIn("Kaufvertrag", doc_class)


class TestDatumBeginnLabel(unittest.TestCase):
    """_human_label_for_evidence vergibt "Mietbeginn" nur für
    Miet-/Räumungsdokumente, "Beschäftigungsbeginn" für Arbeitsrechtsdokumente,
    "Beginn" für alle anderen."""

    def test_mietvertrag_gives_mietbeginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.04.2025", "", "mietvertrag"),
            "Mietbeginn",
        )

    def test_gewerbemietvertrag_gives_mietbeginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.04.2025", "", "Gewerbemietvertrag"),
            "Mietbeginn",
        )

    def test_raeumungsklage_gives_mietbeginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.04.2025", "", "Räumungsklage"),
            "Mietbeginn",
        )

    def test_kuendigungsschutzmandat_gives_beschaeftigungsbeginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.04.2019", "", "Kündigungsschutzmandat"),
            "Beschäftigungsbeginn",
        )

    def test_arbeitsvertrag_gives_beschaeftigungsbeginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.04.2019", "", "arbeitsvertrag"),
            "Beschäftigungsbeginn",
        )

    def test_scheidungsfolgenvereinbarung_gives_beginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.01.2025", "", "Scheidungsfolgenvereinbarung"),
            "Beginn",
        )

    def test_generic_gives_beginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.01.2025", "", "generic"),
            "Beginn",
        )

    def test_none_doc_type_gives_beginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.01.2025", "", None),
            "Beginn",
        )

    def test_empty_doc_type_gives_beginn(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.01.2025", "", ""),
            "Beginn",
        )

    def test_quote_unterhalt_gives_unterhaltsbeginn_for_generic(self):
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.01.2025", "Unterhaltszahlungen ab", "generic"),
            "Unterhaltsbeginn",
        )

    def test_doc_type_takes_precedence_over_quote(self):
        """Doc-type Miet gewinnt auch wenn Quote Arbeits-Keywords hat."""
        self.assertEqual(
            _human_label_for_evidence("datum_beginn", "01.04.2025", "Arbeitsbeginn", "Wohnraummietvertrag"),
            "Mietbeginn",
        )


if __name__ == "__main__":
    unittest.main()
