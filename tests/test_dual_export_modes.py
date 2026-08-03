import unittest
import re

from core.export_render import (
    apply_output_mode,
    render_segmented_aktenvermerk_text,
    render_segmented_summary_text,
)
from core.export_content import build_export_content
from governance.redaction import apply_strict_external_mask, restore_payload_from_llm


class DualExportModeTests(unittest.TestCase):
    def _mapping(self) -> dict[str, str]:
        return {
            "[PERSON_1]": "Max Mustermann",
            "[ORG_1]": "Beispiel GmbH",
            "[EMAIL_1]": "max.mustermann@example.org",
        }

    def _segment_payload(self) -> list[dict]:
        return [
            {
                "segment_meta": {"title_guess": "Klageschrift", "start_page": 1, "end_page": 1},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "dokumenttyp_parteien": "Dokumenttyp: Klageschrift\nKlägerin: [PERSON_1]\nBeklagte: [ORG_1]",
                    "kernaussagen": [
                        {
                            "text": "Kontakt erfolgte von [PERSON_1] an [EMAIL_1].",
                            "citations": [
                                {
                                    "page_number": 1,
                                    "quote_snippet": "Quelle: [PERSON_1] schrieb an [EMAIL_1].",
                                }
                            ],
                        }
                    ],
                    "auffaelligkeiten": [],
                    "missing_info": ["Rückfrage zu [PERSON_1]."],
                    "prueffragen": [],
                    "todos": [],
                },
                "evidence_map": [
                    {
                        "field": "source",
                        "value": "[EMAIL_1]",
                        "page": 1,
                        "quote": "source snippet: [PERSON_1] an [EMAIL_1]",
                    },
                    {
                        "field": "betraege",
                        "value": "1.000 EUR",
                        "page": 1,
                        "quote": "Beleg: Zahlung durch [PERSON_1]",
                    },
                ],
            }
        ]

    def _policy_roundtrip_payload(self) -> list[dict]:
        return [
            {
                "segment_meta": {"title_guess": "Schreiben", "start_page": 1, "end_page": 1},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "dokumenttyp_parteien": "Dokumenttyp: Schreiben\nMandantin: [PERSON_1]",
                    "kernaussagen": [
                        {
                            "text": "[PERSON_1] hat den Vertrag geprüft.",
                            "citations": [
                                {
                                    "page_number": 1,
                                    "quote_snippet": "Snippet: [PERSON_1] im Original.",
                                }
                            ],
                        }
                    ],
                    "auffaelligkeiten": [],
                    "missing_info": [],
                    "prueffragen": [],
                    "todos": [],
                },
                "evidence_map": [
                    {
                        "field": "source",
                        "value": "[PERSON_1]",
                        "page": 1,
                        "quote": "Beleg: [PERSON_1] hat unterschrieben.",
                    }
                ],
            }
        ]

    def _build_policy_export_content(
        self,
        mode: str,
        restore_evidence: bool,
    ) -> tuple[dict[str, str], dict]:
        mapping = {"[PERSON_1]": "Petra Sommer"}
        transformed = apply_output_mode(
            self._policy_roundtrip_payload(),
            mapping,
            mode=mode,
            restore_evidence=restore_evidence,
        )
        export_payload = build_export_content(
            {
                "summary": render_segmented_summary_text(transformed, coverage=None),
                "aktennotiz": render_segmented_aktenvermerk_text(transformed, coverage=None),
                "evidence": transformed,
            },
            "Beides",
            mode=mode,
        )
        return mapping, export_payload

    def test_external_export_contains_no_plaintext_values(self):
        mapping = self._mapping()
        transformed = apply_output_mode(self._segment_payload(), mapping, mode="external")

        summary_text = render_segmented_summary_text(transformed, coverage=None)
        akten_text = render_segmented_aktenvermerk_text(transformed, coverage=None)
        combined = f"{summary_text}\n{akten_text}"

        for original in mapping.values():
            self.assertNotIn(original, combined)
        self.assertIn("[PERSON_1]", combined)

    def test_internal_export_restores_main_text_but_not_evidence_by_default(self):
        mapping = self._mapping()
        transformed = apply_output_mode(
            self._segment_payload(),
            mapping,
            mode="internal",
            restore_evidence=False,
        )

        summary_text = render_segmented_summary_text(transformed, coverage=None)
        akten_text = render_segmented_aktenvermerk_text(transformed, coverage=None)
        combined = f"{summary_text}\n{akten_text}"

        self.assertIn(mapping["[PERSON_1]"], combined)
        self.assertIn(mapping["[EMAIL_1]"], combined)
        self.assertNotIn(
            mapping["[PERSON_1]"],
            transformed[0]["analysis"]["kernaussagen"][0]["citations"][0]["quote_snippet"],
        )
        self.assertNotIn(mapping["[PERSON_1]"], transformed[0]["evidence_map"][0]["quote"])

    def test_internal_export_restores_evidence_when_enabled(self):
        mapping = self._mapping()
        transformed = apply_output_mode(
            self._segment_payload(),
            mapping,
            mode="internal",
            restore_evidence=True,
        )

        self.assertIn(
            mapping["[PERSON_1]"],
            transformed[0]["analysis"]["kernaussagen"][0]["citations"][0]["quote_snippet"],
        )
        self.assertIn(mapping["[PERSON_1]"], transformed[0]["evidence_map"][0]["quote"])
        self.assertIn(mapping["[EMAIL_1]"], transformed[0]["evidence_map"][0]["value"])

    def test_internal_export_does_not_restore_evidence_when_toggle_off(self):
        mapping, export_payload = self._build_policy_export_content(
            mode="internal",
            restore_evidence=False,
        )

        summary_and_note = (
            str(export_payload.get("summary", ""))
            + "\n"
            + str(export_payload.get("aktennotiz", ""))
        )
        evidence_snippet = str(export_payload["evidence"][0]["evidence_map"][0]["quote"])

        self.assertIn(mapping["[PERSON_1]"], summary_and_note)
        self.assertIn("[PERSON_1]", evidence_snippet)
        self.assertNotIn(mapping["[PERSON_1]"], evidence_snippet)

    def test_internal_export_restores_evidence_when_toggle_on(self):
        mapping, export_payload = self._build_policy_export_content(
            mode="internal",
            restore_evidence=True,
        )

        evidence_snippet = str(export_payload["evidence"][0]["evidence_map"][0]["quote"])
        self.assertIn(mapping["[PERSON_1]"], evidence_snippet)

    def test_external_export_never_restores(self):
        mapping, export_payload = self._build_policy_export_content(
            mode="external",
            restore_evidence=False,
        )

        summary_and_note = (
            str(export_payload.get("summary", ""))
            + "\n"
            + str(export_payload.get("aktennotiz", ""))
        )
        evidence_snippet = str(export_payload["evidence"][0]["evidence_map"][0]["quote"])

        self.assertNotIn(mapping["[PERSON_1]"], summary_and_note)
        self.assertIn("[PERSON_1]", summary_and_note)
        self.assertNotIn(mapping["[PERSON_1]"], evidence_snippet)
        self.assertIn("[PERSON_1]", evidence_snippet)

    def test_build_export_content_respects_selection(self):
        payload = {
            "summary": "Summary-Teil",
            "aktennotiz": "Aktennotiz-Teil",
            "evidence": [{"field": "betraege", "value": "1.000 EUR"}],
        }

        summary_only = build_export_content(payload, "Zusammenfassung")
        self.assertEqual(summary_only["summary"], "Summary-Teil")
        self.assertEqual(summary_only["aktennotiz"], "")
        self.assertTrue(summary_only["evidence"])

        akten_only = build_export_content(payload, "Aktennotiz")
        self.assertEqual(akten_only["summary"], "")
        self.assertEqual(akten_only["aktennotiz"], "Aktennotiz-Teil")

        both = build_export_content(payload, "Beides")
        self.assertEqual(both["summary"], "Summary-Teil")
        self.assertEqual(both["aktennotiz"], "Aktennotiz-Teil")

    def test_internal_export_payload_can_be_fully_restored_without_placeholders(self):
        mapping = self._mapping()
        transformed = apply_output_mode(
            self._segment_payload(),
            mapping,
            mode="internal",
            restore_evidence=False,
        )
        export_payload = build_export_content(
            {
                "summary": render_segmented_summary_text(transformed, coverage=None),
                "aktennotiz": render_segmented_aktenvermerk_text(transformed, coverage=None),
                "evidence": transformed,
            },
            "Beides",
            mode="internal",
        )
        restored_payload = restore_payload_from_llm(export_payload, mapping)
        combined = (
            str(restored_payload.get("summary", ""))
            + "\n"
            + str(restored_payload.get("aktennotiz", ""))
            + "\n"
            + str(restored_payload.get("evidence", ""))
        )
        self.assertIsNone(
            re.search(r"\[(?:ADDR|PERSON|EMAIL|IBAN|PARTEI|DICT)_[0-9]+\]", combined)
        )

    def test_external_strict_mask_hides_org_and_address_in_parties_block(self):
        payload = [
            {
                "segment_meta": {"title_guess": "Klageschrift", "start_page": 1, "end_page": 1},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "dokumenttyp_parteien": (
                        "Dokumenttyp: Klageschrift\n"
                        "Beklagte: Beispiel GmbH\n"
                        "Anschrift: Reeperbahn 77, 20359 Hamburg"
                    ),
                    "kernaussagen": [],
                    "auffaelligkeiten": [],
                    "missing_info": [],
                    "prueffragen": [],
                    "todos": [],
                },
                "evidence_map": [],
            }
        ]
        transformed = apply_output_mode(payload, mapping={}, mode="external")
        summary = render_segmented_summary_text(transformed, coverage=None)
        self.assertNotIn("Beispiel GmbH", summary)
        self.assertNotIn("Reeperbahn 77, 20359 Hamburg", summary)
        self.assertIn("– nicht genannt", summary)
        self.assertNotRegex(summary, r"\[(?:ORG|ADDR)MASK_[0-9]+\]")

    def test_export_headers_prefer_analysis_document_type_over_unclear_segment_guess(self):
        segment_results = [
            {
                "segment_meta": {"title_guess": "Dokumentsegment", "doc_type_guess": "Unklar", "start_page": 1, "end_page": 2},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "document_type": "Mietvertrag",
                    "document_type_extracted": "Mietvertrag",
                    "dokumenttyp_parteien": "Dokumenttyp: Mietvertrag",
                    "kernaussagen": [],
                    "auffaelligkeiten": [],
                    "missing_info": [],
                    "prueffragen": [],
                    "todos": [],
                },
                "evidence_map": [],
            }
        ]
        summary = render_segmented_summary_text(segment_results, coverage=None)
        akten = render_segmented_aktenvermerk_text(segment_results, coverage=None)

        summary_header = next((line for line in summary.splitlines() if line.startswith("DOKUMENT ")), "")
        akten_header = next((line for line in akten.splitlines() if line.startswith("DOKUMENT ")), "")

        self.assertTrue(summary_header)
        self.assertTrue(akten_header)
        self.assertNotIn("Unklar", summary_header)
        self.assertNotIn("Unklar", akten_header)
        self.assertIn("Mietvertrag", summary_header)
        self.assertIn("Mietvertrag", akten_header)


class ExternalModeQuelleZeilenTest(unittest.TestCase):
    """Quelle-Zeilen mit Adressfragmenten dürfen nicht in den external-Export."""

    def test_quelle_zeile_mit_adressfragment_wird_entfernt(self):
        """LLM-Output: '[PERSON_1] 77 20359 Hamburg' in Quelle-Zeile → muss verschwinden."""
        payload = {
            "summary": (
                "– Mietbetrag: 1.200 EUR\n"
                "  Quelle S. 3: [PERSON_1] 77 20359 Hamburg\n"
                "– Vertragslaufzeit: 12 Monate"
            )
        }
        result = apply_strict_external_mask(payload)
        summary = result["summary"]
        self.assertNotIn("Quelle S.", summary)
        self.assertNotIn("20359", summary)
        self.assertNotIn("Hamburg", summary)
        # Restinhalt bleibt erhalten
        self.assertIn("1.200 EUR", summary)
        self.assertIn("12 Monate", summary)

    def test_quelle_zeile_ohne_adressfragment_wird_ebenfalls_entfernt(self):
        """Auch harmlose Quelle-Zeilen werden im external-mode entfernt."""
        payload = {"summary": "– Aussage\n  Quelle S. 1: Der Vertrag gilt ab 01.01.2026"}
        result = apply_strict_external_mask(payload)
        self.assertNotIn("Quelle S.", result["summary"])

    def test_interne_zeilen_bleiben_unveraendert(self):
        """Nicht-Quelle-Zeilen werden durch den Filter nicht verändert."""
        payload = {"summary": "– Kernaussage ohne Quelle\n– Zweite Aussage"}
        result = apply_strict_external_mask(payload)
        self.assertIn("Kernaussage ohne Quelle", result["summary"])
        self.assertIn("Zweite Aussage", result["summary"])


if __name__ == "__main__":
    unittest.main()
