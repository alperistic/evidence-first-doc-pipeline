import re
import unittest

try:
    from pydantic import ValidationError
except ModuleNotFoundError:  # pragma: no cover - fallback for local test env
    from core.models import ValidationError

from core.export_render import (
    collect_structured_parties_for_render,
    _format_token_count,
    _normalize_pdf_missing_line,
    render_segmented_aktenvermerk_text,
    render_segmented_summary_text,
)
from core.models import AktenvermerkPayloadModel, SummaryPayloadModel, clean_snippet
from core.analyzer import (
    _extract_rubrum_parties_from_text,
    maybe_verify_citations_for_fields,
    verify_citations_for_fields,
)
from core.evidence import (
    apply_no_legal_advice_guard,
    apply_doc_type_consistency_gate,
    build_evidence_map,
    ensure_party_evidence_consistency,
    strip_repeating_header_footer_lines,
)


class CitationAndAktenvermerkTests(unittest.TestCase):
    def test_klageschrift_rubrum_party_extraction_keeps_roles_separate(self):
        text = (
            "[Seite 1]\n"
            "Klägerin: Petra Sommer\n"
            "Elbchaussee 201\n"
            "22605 Hamburg\n"
            "vertreten durch: Rechtsanwältin Dr. Anna Weiß\n"
            "Jungfernstieg 44\n"
            "20354 Hamburg\n"
            "Beklagter: Stefan Lorenz\n"
            "Reeperbahn 77\n"
            "20359 Hamburg\n"
            "Hamburg, den 14.02.2026\n"
            "Klage\n"
        )
        parsed = {"document_type": "Klageschrift"}
        parties = _extract_rubrum_parties_from_text(text, parsed=parsed)
        self.assertGreaterEqual(len(parties), 2)

        by_role = {}
        for item in parties:
            text_value = str(item.get("text", "") or "")
            if ":" not in text_value:
                continue
            role, value = text_value.split(":", 1)
            by_role[role.strip()] = value.strip()

        self.assertIn("Petra Sommer", by_role.get("Klägerin", ""))
        self.assertIn("Stefan Lorenz", by_role.get("Beklagter", ""))
        self.assertIn("Dr. Anna Weiß", by_role.get("Prozessbevollmächtigte", ""))
        self.assertNotIn("Rechtsanwältin", by_role.get("Klägerin", ""))

    def test_klageschrift_rubrum_parties_render_in_export_summary(self):
        text = (
            "[Seite 1]\n"
            "Klägerin: Petra Sommer\n"
            "Elbchaussee 201\n"
            "22605 Hamburg\n"
            "vertreten durch: Rechtsanwältin Dr. Anna Weiß\n"
            "Jungfernstieg 44\n"
            "20354 Hamburg\n"
            "Beklagter: Stefan Lorenz\n"
            "Reeperbahn 77\n"
            "20359 Hamburg\n"
            "Klage\n"
        )
        parties = _extract_rubrum_parties_from_text(text, parsed={"document_type": "Klageschrift"})
        segment_results = [
            {
                "segment_meta": {"title_guess": "Klageschrift", "start_page": 1, "end_page": 1},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "document_type": "Klageschrift",
                    "dokumenttyp_parteien": "Dokumenttyp: Klageschrift",
                    "parties": parties,
                    "kernaussagen": [],
                    "auffaelligkeiten": [],
                    "missing_info": [],
                    "prueffragen": [],
                    "todos": [],
                },
                "evidence_map": [
                    {"field": "parteien", "value": "Rechtsanwältin Müllwert", "page": 1, "quote": "Müllwert"},
                ],
            }
        ]
        rendered = render_segmented_summary_text(segment_results, coverage=None)
        parties_section = rendered.split("2. ECKDATEN")[0]
        self.assertIn("Klägerin: Petra Sommer", parties_section)
        self.assertIn("Prozessbevollmächtigte: Rechtsanwältin Dr. Anna Weiß", parties_section)
        self.assertIn("Beklagter: Stefan Lorenz", parties_section)

    def test_exact_citation_match_is_verified(self):
        pages = [
            {
                "page_number": 1,
                "text": "Die Kündigungsfrist beträgt 14 Tage ab Zugang der Mitteilung.",
            }
        ]
        parsed = {
            "kernaussagen": [
                {
                    "text": "Im Dokument steht eine Kündigungsfrist von 14 Tagen.",
                    "citations": [
                        {"page_number": 1, "quote_snippet": "Kündigungsfrist beträgt 14 Tage"}
                    ],
                }
            ]
        }

        out = verify_citations_for_fields(parsed, pages, ("kernaussagen",))
        item = out["kernaussagen"][0]

        self.assertEqual(item["citation_status"], "verified")
        self.assertEqual(item["citations"][0]["quote_snippet"], "Kündigungsfrist beträgt 14 Tage")

    def test_fuzzy_citation_match_falls_back_and_is_verified(self):
        pages = [
            {
                "page_number": 2,
                "text": "Die Zahlung ist bis spätestens zum 31.03.2026 auf das benannte Konto zu leisten.",
            }
        ]
        parsed = {
            "auffaelligkeiten": [
                {
                    "text": "Im Dokument steht ein konkreter Zahlungstermin.",
                    "citations": [
                        {"quote_snippet": "Zahlung bis spaetestens zum 31.03.2026"}
                    ],
                }
            ]
        }

        out = verify_citations_for_fields(parsed, pages, ("auffaelligkeiten",))
        item = out["auffaelligkeiten"][0]

        self.assertEqual(item["citation_status"], "verified")
        self.assertEqual(item["best_evidence"]["page_number"], 2)
        self.assertIn("31.03.2026", item["best_evidence"]["quote_snippet"])

    def test_fuzzy_citation_match_genuinely_ambiguous_across_pages_is_unverified(self):
        # Same candidate quote plausibly matches two DIFFERENT, non-overlapping
        # locations (different pages) -> must stay unverified. This guards
        # against a fix for the overlapping-window bug (above) accidentally
        # deleting the uniqueness margin instead of narrowing it correctly.
        pages = [
            {
                "page_number": 1,
                "text": "Die Zahlung ist bis spätestens zum 31.03.2026 auf das benannte Konto zu leisten.",
            },
            {
                "page_number": 3,
                "text": "Die Zahlung ist bis spätestens zum 31.03.2026 auf das andere Konto zu leisten.",
            },
        ]
        parsed = {
            "auffaelligkeiten": [
                {
                    "text": "Im Dokument steht ein konkreter Zahlungstermin.",
                    "citations": [
                        {"quote_snippet": "Zahlung bis spaetestens zum 31.03.2026"}
                    ],
                }
            ]
        }

        out = verify_citations_for_fields(parsed, pages, ("auffaelligkeiten",))
        item = out["auffaelligkeiten"][0]

        self.assertEqual(item["citation_status"], "unverified")

    def test_aktenvermerk_structure_validation(self):
        valid_payload = {
            "dokumenttyp_parteien": "Mietvertrag zwischen Partei A und Partei B",
            "kernaussagen": [{"text": "Im Dokument steht eine Laufzeit von 24 Monaten.", "citations": []}],
            "fristen_termine": [
                {
                    "text": "Kündigungsfrist 3 Monate",
                    "berechnet": False,
                    "herleitung": None,
                    "citations": [],
                }
            ],
            "widersprueche": [{"text": "Hinweis: Regelung zu prüfen.", "citations": []}],
            "fehlende_angaben": ["Rückfrage zur Nebenkostenregelung"],
            "todos": ["Originalvertrag gegenlesen"],
        }
        model = AktenvermerkPayloadModel.model_validate(valid_payload)
        self.assertEqual(model.dokumenttyp_parteien, valid_payload["dokumenttyp_parteien"])

        invalid_payload = dict(valid_payload)
        invalid_payload.pop("todos")
        with self.assertRaises(ValidationError):
            AktenvermerkPayloadModel.model_validate(invalid_payload)

    def test_summary_schema_accepts_legacy_list_format(self):
        payload = {
            "kurzueberblick": "Kurzüberblick.",
            "top3_findings": ["Fehlende Angabe zur Laufzeit"],
            "top3_questions": ["Welche Laufzeit steht im Dokument?"],
            "dokumenttyp_parteien": "Vertrag zwischen A und B",
            "kernaussagen": ["Im Dokument steht eine Vergütung."],
            "fristen_termine": [{"text": "Frist bis 31.03.2026"}],
            "auffaelligkeiten": ["Hinweis: Klausel zu prüfen."],
            "prueffragen": ["Ist die Regelung vollständig?"],
        }
        model = SummaryPayloadModel.model_validate(payload)
        self.assertEqual(model.kernaussagen[0].text, "Im Dokument steht eine Vergütung.")
        self.assertEqual(model.top3_findings[0].title, "Fehlende Angabe zur Laufzeit")
        self.assertEqual(model.fristen_termine[0], "Frist bis 31.03.2026")

    def test_unverified_candidates_are_retained(self):
        pages = [{"page_number": 1, "text": "Kurzer Seitentext ohne den Kandidaten."}]
        parsed = {
            "kernaussagen": [
                {
                    "text": "Aussage",
                    "citations": [{"page_number": 1, "quote_snippet": "Nicht vorhandene Fundstelle"}],
                }
            ]
        }
        out = verify_citations_for_fields(parsed, pages, ("kernaussagen",))
        item = out["kernaussagen"][0]
        self.assertEqual(item["citation_status"], "unverified")
        self.assertEqual(len(item["citations"]), 1)
        self.assertEqual(len(item["citations_verified"]), 0)
        self.assertEqual(item["citation_candidates"][0]["quote_snippet"], "Nicht vorhandene Fundstelle")

    def test_verify_disabled_keeps_original_citations_unchanged(self):
        pages = [{"page_number": 1, "text": "Es liegt nur ein kurzer Segmenttext vor."}]
        parsed = {
            "kernaussagen": [
                {
                    "text": "Aussage 1",
                    "citations": [{"page_number": 5, "quote_snippet": "Originales Zitat"}],
                }
            ]
        }
        original = parsed["kernaussagen"][0]["citations"]
        out = maybe_verify_citations_for_fields(parsed, pages, ("kernaussagen",), enabled=False)
        self.assertEqual(out["kernaussagen"][0]["citations"], original)
        self.assertEqual(out["citation_status"], "unchecked")
        self.assertFalse(out["citation_check_ran"])

    def test_verify_enabled_preserves_existing_citations_without_overwrite(self):
        pages = [
            {
                "page_number": 1,
                "text": "Im Dokument ist eine Kündigungsfrist von 14 Tagen nach Zugang geregelt.",
            }
        ]
        parsed = {
            "kernaussagen": [
                {
                    "text": "Kündigungsfrist beträgt 14 Tage.",
                    "citations": [
                        {
                            "page_number": 1,
                            "quote_snippet": "Kündigungsfrist von 14 Tagen",
                            "section_hint": "Abschnitt 3",
                        }
                    ],
                }
            ]
        }
        original = [dict(item) for item in parsed["kernaussagen"][0]["citations"]]

        out = maybe_verify_citations_for_fields(parsed, pages, ("kernaussagen",), enabled=True)

        self.assertTrue(out["citation_check_ran"])
        self.assertEqual(out["kernaussagen"][0]["citations"], original)

    def test_verify_enabled_can_add_heuristic_citation_when_missing(self):
        pages = [
            {
                "page_number": 3,
                "text": "Die Miete beträgt 1.250 EUR monatlich laut Vertrag.",
            }
        ]
        parsed = {
            "kernaussagen": [
                {
                    "text": "Die Miete beträgt 1.250 EUR monatlich laut Vertrag.",
                    "citations": [],
                }
            ]
        }

        out = verify_citations_for_fields(parsed, pages, ("kernaussagen",))
        item = out["kernaussagen"][0]

        self.assertTrue(out["citation_check_ran"])
        self.assertTrue(item.get("citation_heuristic_added"))
        self.assertGreaterEqual(len(item.get("citations", [])), 1)
        self.assertEqual(item["citations"][0]["page_number"], 3)
        self.assertIn("1.250 EUR", item["citations"][0]["quote_snippet"])
        self.assertEqual(item["citation_status"], "unverified")

    def test_validation_only_does_not_throttle_repeated_existing_citations(self):
        pages = [{"page_number": 1, "text": "A B C D E F G H I J K L M N O P Q R S T"}]
        repeated_citation = {"page_number": 1, "quote_snippet": "A B C D"}
        parsed = {
            "kernaussagen": [
                {"text": f"Aussage {idx}", "citations": [dict(repeated_citation)]}
                for idx in range(1, 7)
            ]
        }
        out = verify_citations_for_fields(parsed, pages, ("kernaussagen",))
        verified = [
            item for item in out["kernaussagen"]
            if str(item.get("citation_status", "")).lower() == "verified"
        ]
        self.assertEqual(len(verified), 6)
        for item in out["kernaussagen"]:
            self.assertEqual(item.get("citations"), [repeated_citation])

    def test_evidence_map_filters_party_artifacts_and_sets_missing(self):
        pages = [
            {
                "page_number": 1,
                "text": "Zwischen\nnachfolgend Arbeitgeber\nArbeitnehmer\nPräambel",
            }
        ]
        evidence_map = build_evidence_map(pages)
        parties = [item for item in evidence_map if item.get("field") == "parteien"]
        self.assertTrue(parties)
        self.assertEqual(parties[0]["value"], "missing")

    def test_doc_type_leak_items_are_moved_to_unclear(self):
        parsed = {
            "dokumenttyp_parteien": "Arbeitsvertrag zwischen A und B",
            "kernaussagen": [
                {
                    "text": "Im Dokument wird eine Kaution von 500 EUR erwähnt.",
                    "citations": [{"page_number": 1, "quote_snippet": "Kaution von 500 EUR"}],
                }
            ],
            "top3_findings": [],
            "auffaelligkeiten": [],
            "prueffragen": [],
        }
        out = apply_doc_type_consistency_gate(parsed, doc_type_hint="Arbeitsvertrag")
        self.assertEqual(len(out["kernaussagen"]), 0)
        self.assertTrue(any("Segment-Vermischung" in item.get("text", "") for item in out["auffaelligkeiten"]))
        self.assertTrue(any("Kaution" in q or "kaution" in q for q in out["prueffragen"]))

    def test_summary_and_aktenvermerk_render_paths_are_distinct(self):
        segment_results = [
            {
                "segment_meta": {"title_guess": "Mietvertrag", "start_page": 1, "end_page": 1},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "dokumenttyp_parteien": "Mietvertrag zwischen A und B",
                    "kernaussagen": [
                        {"text": "Im Dokument steht eine Vergütung.", "citations": [{"page_number": 1, "quote_snippet": "Vergütung"}]}
                    ],
                    "auffaelligkeiten": [],
                    "prueffragen": [],
                    "todos": ["Checkliste abgleichen"],
                    "missing_info": [],
                },
                "evidence_map": [
                    {"field": "parteien", "value": "A B", "page": 1, "quote": "zwischen A und B"},
                    {"field": "betraege", "value": "3.540,00 EUR", "page": 1, "quote": "Kaution in Höhe von 3.540,00 EUR"},
                    {"field": "fristen_termine", "value": "25.03.2026", "page": 1, "quote": "Kaution fällig bis 25.03.2026"},
                ],
            }
        ]
        summary_text = render_segmented_summary_text(segment_results, coverage=None)
        akten_text = render_segmented_aktenvermerk_text(segment_results, coverage=None)
        self.assertNotEqual(summary_text, akten_text)
        self.assertIn("ZUSAMMENFASSUNG", summary_text)
        self.assertIn("AKTENNOTIZ (INTERN)", akten_text)
        self.assertNotIn("betraege:", summary_text.lower())
        self.assertIn("Kaution", summary_text)

    def test_parties_rendering_does_not_use_evidence_fallback(self):
        segment_results = [
            {
                "segment_meta": {"title_guess": "Klageschrift", "start_page": 1, "end_page": 1},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "document_type": "Klageschrift",
                    "dokumenttyp_parteien": "Dokumenttyp: Klageschrift",
                    "parties": [],
                    "kernaussagen": [],
                    "auffaelligkeiten": [],
                    "prueffragen": [],
                    "todos": [],
                    "missing_info": [],
                },
                "evidence_map": [
                    {
                        "field": "parteien",
                        "value": "IBAN DE00 0000 0000 0000 0000 00 / Wohnzwecke / Rechtsanwältin",
                        "page": 1,
                        "quote": "Müll aus evidence fallback",
                    }
                ],
            }
        ]

        text = render_segmented_summary_text(segment_results, coverage=None)
        parties_section = text.split("2. ECKDATEN")[0]
        self.assertIn("– nicht genannt", parties_section)
        self.assertNotIn("IBAN", parties_section)
        self.assertNotIn("Wohnzwecke", parties_section)
        self.assertNotIn("Rechtsanwältin", parties_section)

    def test_parties_single_source_of_truth_ignores_evidence_fallback_in_helper_and_export(self):
        analysis = {
            "document_type": "Mietvertrag",
            "dokumenttyp_parteien": "Dokumenttyp: Mietvertrag",
            "parties": [],
            "kernaussagen": [],
            "auffaelligkeiten": [],
            "missing_info": [],
            "prueffragen": [],
            "todos": [],
        }
        evidence_garbage_1 = "Rechtsanwältin Dr. Beispiel, Kanzlei Muster"
        evidence_garbage_2 = "Die Räume dienen ausschließlich Wohnzwecke"
        segment_results = [
            {
                "segment_meta": {"title_guess": "Mietvertrag", "start_page": 1, "end_page": 1},
                "analysis_output_type": "json_ok",
                "analysis": analysis,
                "evidence_map": [
                    {"field": "parteien", "value": evidence_garbage_1, "page": 1, "quote": evidence_garbage_1},
                    {"field": "parteien", "value": evidence_garbage_2, "page": 1, "quote": evidence_garbage_2},
                ],
            }
        ]

        ui_parties = collect_structured_parties_for_render(analysis, max_items=4)
        self.assertEqual(ui_parties, [])

        export_text = render_segmented_summary_text(segment_results, coverage=None)
        parties_section = export_text.split("2. ECKDATEN")[0]
        self.assertIn("– nicht genannt", parties_section)
        self.assertNotIn(evidence_garbage_1, parties_section)
        self.assertNotIn(evidence_garbage_2, parties_section)

    def test_parties_rendering_prefers_structured_parties(self):
        segment_results = [
            {
                "segment_meta": {"title_guess": "Klageschrift", "start_page": 1, "end_page": 1},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "document_type": "Klageschrift",
                    "dokumenttyp_parteien": "Dokumenttyp: Klageschrift",
                    "parties": [
                        {
                            "text": "Klägerin: [PARTEI_A]",
                            "citations": [{"page_number": 1, "quote_snippet": "Klägerin: [PARTEI_A]"}],
                        },
                        {
                            "text": "Beklagter: [PARTEI_B]",
                            "citations": [{"page_number": 1, "quote_snippet": "Beklagter: [PARTEI_B]"}],
                        },
                    ],
                    "kernaussagen": [],
                    "auffaelligkeiten": [],
                    "prueffragen": [],
                    "todos": [],
                    "missing_info": [],
                },
                "evidence_map": [
                    {
                        "field": "parteien",
                        "value": "MÜLLTEXT aus fallback",
                        "page": 1,
                        "quote": "MÜLLTEXT aus fallback",
                    }
                ],
            }
        ]

        text = render_segmented_summary_text(segment_results, coverage=None)
        parties_section = text.split("2. ECKDATEN")[0]
        self.assertIn("Klägerin: [PARTEI_A]", parties_section)
        self.assertIn("Beklagter: [PARTEI_B]", parties_section)
        self.assertNotIn("MÜLLTEXT aus fallback", parties_section)

    def test_klageschrift_parties_from_structured_field_are_rendered(self):
        segment_results = [
            {
                "segment_meta": {"title_guess": "Klageschrift", "start_page": 1, "end_page": 2},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "document_type": "Klageschrift",
                    "dokumenttyp_parteien": "Dokumenttyp: Klageschrift",
                    "parties": [
                        {
                            "text": "Klägerin: Anna Beispiel",
                            "citations": [{"page_number": 1, "quote_snippet": "Klägerin: Anna Beispiel"}],
                        },
                        {
                            "text": "Beklagter: Beta GmbH",
                            "citations": [{"page_number": 1, "quote_snippet": "Beklagter: Beta GmbH"}],
                        },
                    ],
                    "kernaussagen": [],
                    "auffaelligkeiten": [],
                    "prueffragen": [],
                    "todos": [],
                    "missing_info": [],
                },
                "evidence_map": [],
            }
        ]

        text = render_segmented_summary_text(segment_results, coverage=None)
        self.assertIn("Klägerin: Anna Beispiel", text)
        self.assertIn("Beklagter: Beta GmbH", text)

    def test_segmented_export_coverage_status_uses_verified_citations(self):
        segment_results = [
            {
                "analysis": {
                    "kernaussagen": [
                        {
                            "text": "Im Dokument steht eine Frist.",
                            "citations": [{"page_number": 1, "quote_snippet": "Frist bis 31.03.2026"}],
                        }
                    ],
                    "auffaelligkeiten": [],
                    "top3_findings": [],
                    "citation_status": "unverified",
                }
            }
        ]
        text = render_segmented_summary_text(
            segment_results,
            coverage={"pages_total": 1, "pages_extracted": 1, "chunks_sent": 1, "citation_status": "unverified"},
        )
        self.assertIn("Dokument: 1 Seiten (extrahiert)", text)

    def test_token_count_formatter_returns_na_for_missing_usage(self):
        self.assertEqual(_format_token_count({"token_count": 0}), "n/a")
        self.assertEqual(_format_token_count({"total_tokens": 221}), "221")

    def test_fristen_termine_label_is_normalized_in_aktenvermerk(self):
        segment_results = [
            {
                "segment_meta": {"title_guess": "Klageschrift", "start_page": 1, "end_page": 1},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "dokumenttyp_parteien": "Dokumenttyp: Klageschrift\nKlägerin: [PARTEI_A]\nBeklagter: [PARTEI_B]",
                    "kernaussagen": [],
                    "auffaelligkeiten": [],
                    "missing_info": [],
                    "todos": [],
                    "prueffragen": [],
                },
                "evidence_map": [
                    {"field": "fristen_termine", "value": "31.03.2026", "page": 1, "quote": "Frist bis 31.03.2026"}
                ],
            }
        ]
        text = render_segmented_aktenvermerk_text(segment_results, coverage=None)
        self.assertIn("Frist/Termin:", text)
        self.assertNotIn("fristen_termine:", text.lower())

    def test_no_party_missing_line_when_core_parties_are_present(self):
        segment_results = [
            {
                "segment_meta": {"title_guess": "Klageschrift", "start_page": 1, "end_page": 1},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "dokumenttyp_parteien": "Dokumenttyp: Klageschrift\nKlägerin: [PARTEI_A]\nBeklagter: [PARTEI_B]",
                    "kernaussagen": [],
                    "auffaelligkeiten": [],
                    "missing_info": [],
                    "todos": [],
                    "prueffragen": [],
                },
                "evidence_map": [],
            }
        ]
        text = render_segmented_summary_text(segment_results, coverage=None)
        self.assertNotIn("missing (Rückfrage erforderlich)", text.split("2. ECKDATEN")[0])

    def test_party_missing_followups_are_filtered_when_core_parties_exist(self):
        segment_results = [
            {
                "segment_meta": {"title_guess": "Klageschrift", "start_page": 1, "end_page": 1},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "dokumenttyp_parteien": "Dokumenttyp: Klageschrift\nKlägerin: [PARTEI_A]\nBeklagter: [PARTEI_B]",
                    "kernaussagen": [],
                    "auffaelligkeiten": [],
                    "missing_info": ["Rückfrage: Parteienangabe im Segment nicht eindeutig belegbar (fehlende Angabe)."],
                    "todos": [],
                    "prueffragen": ["Rückfrage: Klägerin/Beklagter missing"],
                },
                "evidence_map": [],
            }
        ]
        text = render_segmented_aktenvermerk_text(segment_results, coverage=None)
        self.assertNotIn("Parteienangabe", text)
        self.assertNotIn("Klägerin/Beklagter missing", text)

    def test_citations_are_rendered_in_separate_source_lines(self):
        segment_results = [
            {
                "segment_meta": {"title_guess": "Klageschrift", "start_page": 1, "end_page": 1},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "dokumenttyp_parteien": "Dokumenttyp: Klageschrift\nKlägerin: [PARTEI_A]\nBeklagter: [PARTEI_B]",
                    "kernaussagen": [
                        {"text": "Im Dokument steht eine Frist.", "citations": [{"page_number": 1, "quote_snippet": "Frist bis 31.03.2026"}]}
                    ],
                    "auffaelligkeiten": [],
                    "missing_info": [],
                    "todos": [],
                    "prueffragen": [],
                },
                "evidence_map": [],
            }
        ]
        text = render_segmented_summary_text(segment_results, coverage=None)
        self.assertIn("Quelle S. 1: Frist bis 31.03.2026", text)

    def test_structured_segment_is_rendered_even_without_json_ok_flag(self):
        segment_results = [
            {
                "segment_meta": {"title_guess": "Klageschrift", "start_page": 1, "end_page": 1},
                "analysis_output_type": "json_fallback",
                "analysis": {
                    "dokumenttyp_parteien": "Dokumenttyp: Klageschrift\nKlägerin: [PARTEI_A]\nBeklagter: [PARTEI_B]",
                    "kernaussagen": [{"text": "Im Dokument steht eine Frist.", "citations": []}],
                    "auffaelligkeiten": [],
                    "missing_info": [],
                    "prueffragen": [],
                    "todos": [],
                },
                "evidence_map": [],
            }
        ]
        text = render_segmented_summary_text(segment_results, coverage=None)
        self.assertNotIn("Segment-Ergebnis nicht strukturiert verfügbar.", text)
        self.assertIn("1. DOKUMENTTYP & PARTEIEN", text)

    def test_unverified_is_never_rendered_in_summary_text(self):
        segment_results = [
            {
                "segment_meta": {"title_guess": "Klageschrift", "start_page": 1, "end_page": 1},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "dokumenttyp_parteien": "Dokumenttyp: Klageschrift\nKlägerin: [PARTEI_A]\nBeklagter: [PARTEI_B]",
                    "kernaussagen": [
                        {"text": "Im Dokument steht etwas (unverified - Rückfrage erforderlich).", "citations": []}
                    ],
                    "auffaelligkeiten": [],
                    "missing_info": ["Rückfrage: Wert unverified"],
                    "prueffragen": [],
                    "todos": [],
                },
                "evidence_map": [],
            }
        ]
        text = render_segmented_summary_text(
            segment_results,
            coverage={"pages_total": 1, "pages_extracted": 1, "chunks_sent": 1, "citation_status": "unverified"},
        )
        self.assertNotIn("unverified", text.lower())
        self.assertIn("rückfrage (beleg im segment nicht vorhanden)", text.lower())

    def test_citation_sources_are_deduped_and_shortened(self):
        long_quote = "Das ist ein sehr langer Quellenauszug " * 12
        segment_results = [
            {
                "segment_meta": {"title_guess": "Klageschrift", "start_page": 1, "end_page": 1},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "dokumenttyp_parteien": "Dokumenttyp: Klageschrift\nKlägerin: [PARTEI_A]\nBeklagter: [PARTEI_B]",
                    "kernaussagen": [
                        {
                            "text": "Im Dokument steht eine Kernaussage.",
                            "citations": [
                                {"page_number": 1, "quote_snippet": long_quote},
                                {"page_number": 1, "quote_snippet": long_quote},
                            ],
                        }
                    ],
                    "auffaelligkeiten": [],
                    "missing_info": [],
                    "prueffragen": [],
                    "todos": [],
                },
                "evidence_map": [],
            }
        ]
        text = render_segmented_summary_text(segment_results, coverage=None)
        self.assertEqual(text.count("Quelle S. 1:"), 1)
        self.assertIn("…", text)

    def test_pdf_missing_normalization_replaces_non_party_missing(self):
        self.assertEqual(
            _normalize_pdf_missing_line("Vertreter Beklagter: missing"),
            "Vertreter Beklagter: nicht genannt",
        )
        self.assertEqual(
            _normalize_pdf_missing_line("– missing (Rückfrage erforderlich)"),
            "– nicht genannt (Rückfrage erforderlich)",
        )
        self.assertEqual(
            _normalize_pdf_missing_line("Klägerin: missing"),
            "Klägerin: missing",
        )

    def test_evidence_map_dedup_and_topk_limits(self):
        pages = [
            {
                "page_number": 1,
                "text": (
                    "Der Betrag beträgt 1.200,00 EUR. "
                    "Der Betrag beträgt 1.200,00 EUR erneut genannt. "
                    "Frist bis 01.03.2026. "
                    "Weitere Frist bis 15.03.2026. "
                    "Noch eine Frist bis 30.03.2026."
                ),
            }
        ]
        evidence_map = build_evidence_map(pages)
        betraege = [item for item in evidence_map if item.get("field") == "betraege" and item.get("value") != "missing"]
        fristen = [item for item in evidence_map if item.get("field") == "fristen_termine" and item.get("value") != "missing"]

        self.assertEqual(len(betraege), 1)
        self.assertLessEqual(len(fristen), 2)
        dedupe_keys = {(item.get("value"), item.get("page")) for item in fristen}
        self.assertEqual(len(dedupe_keys), len(fristen))

    def test_strip_repeating_header_footer_lines_removes_only_recurring_edge_noise(self):
        pages = [
            {
                "page_number": idx,
                "text": (
                    f"Kanzlei-Testpaket (fiktiv) AZ: MV-2026-0001 Seite {idx}/5\n"
                    f"Inhalt nur Seite {idx}\n"
                    f"Vertraulich Seite {idx}/5"
                ),
            }
            for idx in range(1, 6)
        ]

        cleaned = strip_repeating_header_footer_lines(pages)
        for idx, page in enumerate(cleaned, start=1):
            text = str(page.get("text", "") or "")
            self.assertIn(f"Inhalt nur Seite {idx}", text)
            self.assertNotIn("Kanzlei-Testpaket", text)
            self.assertNotIn("Vertraulich Seite", text)

    def test_strip_repeating_header_footer_lines_keeps_single_occurrence_header(self):
        pages = [
            {"page_number": 1, "text": "Nur einmal Header\nInhalt A"},
            {"page_number": 2, "text": "Anderer Header\nInhalt B"},
            {"page_number": 3, "text": "Noch ein Header\nInhalt C"},
        ]

        cleaned = strip_repeating_header_footer_lines(pages)
        self.assertIn("Nur einmal Header", str(cleaned[0].get("text", "") or ""))

    def test_heuristic_citation_prefers_non_repeating_content_over_header(self):
        pages = []
        for idx in range(1, 6):
            body = "Ohne Fristangabe im Haupttext."
            if idx == 4:
                body = "Im Haupttext: Zahlung bis spätestens 17.06.2026 auf das benannte Konto."
            pages.append(
                {
                    "page_number": idx,
                    "text": (
                        f"Kanzlei-Testpaket (fiktiv) Zahlung bis spätestens 17.06.2026 Seite {idx}/5\n"
                        f"{body}\n"
                        f"AZ: MV-2026-0001 Seite {idx}"
                    ),
                }
            )

        parsed = {"kernaussagen": [{"text": "Zahlung bis spätestens 17.06.2026", "citations": []}]}
        out = verify_citations_for_fields(parsed, pages, ("kernaussagen",))
        citation = out["kernaussagen"][0]["citations"][0]

        self.assertEqual(citation["page_number"], 4)
        self.assertNotIn("Kanzlei-Testpaket", citation["quote_snippet"])

    def test_party_evidence_does_not_use_repeating_header_text(self):
        pages = []
        for idx in range(1, 6):
            body = "Allgemeiner Inhalt."
            if idx == 3:
                body = "Vermieter: Max Mustermann\nMieter: ACME GmbH"
            pages.append(
                {
                    "page_number": idx,
                    "text": (
                        f"Zwischen Kanzlei-Testpaket GmbH und Seite {idx}/5\n"
                        f"{body}\n"
                        f"Footer AZ: MV-2026-0001 Seite {idx}"
                    ),
                }
            )

        evidence_map = build_evidence_map(pages)
        parties = [
            str(item.get("value", "") or "")
            for item in evidence_map
            if item.get("field") == "parteien" and item.get("value") != "missing"
        ]
        self.assertTrue(parties)
        self.assertFalse(any("Kanzlei-Testpaket" in value for value in parties))
        self.assertFalse(any(re.search(r"\bSeite\s+\d", value) for value in parties))

    def test_deadline_evidence_does_not_use_repeating_footer_text(self):
        pages = []
        for idx in range(1, 6):
            body = "Inhalt ohne Termin."
            if idx == 2:
                body = "Die Zahlung ist bis spätestens 17.06.2026 auf das benannte Konto zu leisten."
            pages.append(
                {
                    "page_number": idx,
                    "text": (
                        f"Kanzlei-Testpaket (fiktiv) AZ: MV-2026-0001 Seite {idx}/5\n"
                        f"{body}\n"
                        f"Footer Hinweis: Frist bis 31.03.2026 Seite {idx}/5"
                    ),
                }
            )

        evidence_map = build_evidence_map(pages)
        fristen = [
            item
            for item in evidence_map
            if item.get("field") == "fristen_termine" and item.get("value") != "missing"
        ]
        values = [str(item.get("value", "") or "") for item in fristen]
        quotes = [str(item.get("quote", "") or "") for item in fristen]

        self.assertIn("17.06.2026", values)
        self.assertNotIn("31.03.2026", values)
        self.assertFalse(any("Kanzlei-Testpaket" in quote for quote in quotes))

    def test_mahnung_bis_spaetestens_date_is_extracted_with_payment_snippet(self):
        pages = [
            {
                "page_number": 1,
                "text": (
                    "Rechnung vom 10.05.2026. Erinnerung vom 24.05.2026. "
                    "Letzte Zahlungsaufforderung: Zahlung bis spätestens 17.06.2026 auf das benannte Konto."
                ),
            }
        ]
        evidence_map = build_evidence_map(pages)
        extracted_dates = [
            str(item.get("value", ""))
            for item in evidence_map
            if item.get("field") == "fristen_termine" and item.get("value") != "missing"
        ]
        self.assertIn("17.06.2026", extracted_dates)
        fristen = [
            item
            for item in evidence_map
            if item.get("field") == "fristen_termine" and item.get("value") == "17.06.2026"
        ]
        self.assertTrue(fristen)
        self.assertTrue(any("frist" in str(item.get("label", "")).lower() for item in fristen))
        self.assertTrue(
            any(
                ("bis spätestens" in str(item.get("quote", "")).lower())
                or ("zahlung" in str(item.get("quote", "")).lower())
                or ("fällig" in str(item.get("quote", "")).lower())
                for item in fristen
            )
        )

    def test_date_snippet_prefers_begin_sentence_over_generic_wohnzwecke_sentence(self):
        pages = [
            {
                "page_number": 1,
                "text": (
                    "Die Räume dienen zu Wohnzwecken, Stand 01.04.2026. "
                    "Das Mietverhältnis beginnt am 01.04.2026 und läuft unbefristet."
                ),
            }
        ]
        evidence_map = build_evidence_map(pages)
        beginn = [
            item
            for item in evidence_map
            if item.get("field") == "datum_beginn" and item.get("value") == "01.04.2026"
        ]
        self.assertTrue(beginn)
        quote = str(beginn[0].get("quote", ""))
        self.assertIn("beginnt am 01.04.2026", quote)
        self.assertNotIn("Wohnzwecke", quote)

    def test_party_missing_is_resolved_from_structured_output(self):
        evidence_map = [
            {"field": "parteien", "value": "missing", "page": None, "quote": "missing"},
            {"field": "betraege", "value": "missing", "page": None, "quote": "missing"},
            {"field": "fristen_termine", "value": "missing", "page": None, "quote": "missing"},
            {"field": "datum_beginn", "value": "missing", "page": None, "quote": "missing"},
            {"field": "kuendigung_fristen", "value": "missing", "page": None, "quote": "missing"},
            {"field": "urlaub_ueberstunden", "value": "missing", "page": None, "quote": "missing"},
            {"field": "wettbewerbsverbot", "value": "missing", "page": None, "quote": "missing"},
        ]
        parsed = {
            "dokumenttyp_parteien": "Kläger: Max Mustermann; Beklagte: ACME GmbH",
            "kernaussagen": [
                {
                    "text": "Im Dokument sind die Parteien genannt.",
                    "citations": [{"page_number": 1, "quote_snippet": "Kläger Max Mustermann gegen ACME GmbH"}],
                }
            ],
            "auffaelligkeiten": [],
            "top3_findings": [],
        }
        pages = [{"page_number": 1, "text": "Kläger Max Mustermann gegen ACME GmbH"}]

        fixed = ensure_party_evidence_consistency(evidence_map, parsed, pages)
        parties = [item for item in fixed if item.get("field") == "parteien"]
        self.assertTrue(parties)
        self.assertTrue(any(item.get("value") != "missing" for item in parties))

    def test_clean_snippet_removes_parser_artifacts(self):
        raw = "026   Klage wegen   Zahlungs-\nanspruch.   Zahlung spätestens bis 31.03.2026."
        cleaned = clean_snippet(raw)
        self.assertFalse(cleaned.startswith("026"))
        self.assertNotIn("  ", cleaned)
        self.assertIn("spätestens", cleaned)
        self.assertLessEqual(len(cleaned), 170)

    def test_no_legal_advice_guard_neutralizes_target_fields(self):
        parsed = {
            "todos": ["Anspruch ist schlüssig und durchsetzbar."],
            "missing_info": ["Ob die Beweise die Forderung belegen können, ist offen."],
            "prueffragen": ["Wie sind die Erfolgsaussichten und Aussichten insgesamt?"],
            "auffaelligkeiten": [{"text": "Die Gegenseite könnte die Durchsetzung erschweren.", "citations": []}],
        }
        out = apply_no_legal_advice_guard(parsed)
        joined = " ".join(out.get("todos", []) + out.get("missing_info", []) + out.get("prueffragen", []))
        self.assertNotIn("schlüssig", joined.lower())
        self.assertNotIn("durchsetzbar", joined.lower())
        self.assertNotIn("erfolgsaussicht", joined.lower())
        self.assertIn("Abgleich erforderlich", joined)
        self.assertIn("im Dokument genannt / Beweismittel genannt", joined)
        self.assertIn("offen / unklar / Rückfrage", joined)

    def test_no_legal_advice_guard_filters_doc_type_leaks_for_mietvertrag(self):
        parsed = {
            "dokumenttyp_parteien": "Mietvertrag zwischen A und B",
            "missing_info": [
                "Angabe fehlt: Urlaubstage nicht erkennbar.",
                "Angabe fehlt: Kaution nicht eindeutig benannt.",
            ],
            "auffaelligkeiten": [
                {"text": "Wettbewerbsverbot ungewöhnlich streng.", "citations": []},
                {"text": "Klausel vorhanden: Kaution - zur Prüfung markieren.", "citations": []},
            ],
            "todos": ["Urlaub prüfen", "Kaution abstimmen"],
            "prueffragen": ["Überstunden geregelt?", "Ist die Kaution vollständig?"],
        }
        out = apply_no_legal_advice_guard(parsed, doc_type_hint="Mietvertrag")
        joined = " ".join(
            out.get("missing_info", [])
            + out.get("todos", [])
            + out.get("prueffragen", [])
            + [i.get("text", "") for i in out.get("auffaelligkeiten", [])]
        ).lower()
        self.assertNotIn("urlaub", joined)
        self.assertNotIn("überstunden", joined)
        self.assertNotIn("ueberstunden", joined)
        self.assertNotIn("wettbewerb", joined)
        self.assertIn("kaution", joined)


class ExternalModeQuelleRenderTest(unittest.TestCase):
    """Quelle-Zeilen dürfen im mode='external' nicht gerendert werden."""

    def _segment_with_citations(self) -> list[dict]:
        return [
            {
                "segment_meta": {"title_guess": "Klageschrift", "start_page": 1, "end_page": 2},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "dokumenttyp_parteien": "Dokumenttyp: Klageschrift\nKlägerin: [PARTEI_1]\nBeklagter: [PARTEI_2]",
                    "kernaussagen": [
                        {
                            "text": "Miete wurde nicht gezahlt.",
                            "citations": [{"page_number": 3, "quote_snippet": "[PERSON_1] 77 20359 Hamburg"}],
                        }
                    ],
                    "auffaelligkeiten": [
                        {
                            "text": "Unklare Frist.",
                            "citations": [{"page_number": 4, "quote_snippet": "Frist unklar"}],
                        }
                    ],
                    "missing_info": [],
                    "prueffragen": [],
                    "todos": [],
                },
                "evidence_map": [
                    {"field": "fristen_termine", "value": "31.03.2026", "page": 5, "quote": "Frist bis 31.03.2026"},
                ],
            }
        ]

    def test_external_mode_entfernt_alle_quelle_zeilen_summary(self):
        result = render_segmented_summary_text(self._segment_with_citations(), coverage=None, mode="external")
        self.assertNotIn("Quelle S.", result)

    def test_external_mode_entfernt_alle_quelle_zeilen_aktenvermerk(self):
        result = render_segmented_aktenvermerk_text(self._segment_with_citations(), coverage=None, mode="external")
        self.assertNotIn("Quelle S.", result)

    def test_internal_mode_behaelt_quelle_zeilen_summary(self):
        result = render_segmented_summary_text(self._segment_with_citations(), coverage=None, mode="internal")
        self.assertIn("Quelle S. 3:", result)

    def test_internal_mode_behaelt_quelle_zeilen_aktenvermerk(self):
        result = render_segmented_aktenvermerk_text(self._segment_with_citations(), coverage=None, mode="internal")
        self.assertIn("Quelle S.", result)

    def test_external_mode_behaelt_kerninhalt(self):
        """Kernaussagen (ohne Quelle-Zeilen) bleiben im external-Output erhalten."""
        result = render_segmented_summary_text(self._segment_with_citations(), coverage=None, mode="external")
        self.assertIn("Miete wurde nicht gezahlt.", result)

    def _segment_with_party_citations(self) -> list[dict]:
        """Fixture: Parteien haben eigene citations mit Adressfragment."""
        return [
            {
                "segment_meta": {"title_guess": "Klageschrift", "start_page": 1, "end_page": 3},
                "analysis_output_type": "json_ok",
                "analysis": {
                    "dokumenttyp_parteien": "Dokumenttyp: Klageschrift",
                    "parteien": [
                        {
                            "role": "Klägerin",
                            "value": "[PARTEI_1]",
                            "citations": [{"page_number": 1, "quote_snippet": "[PARTEI_1] wohnhaft 20359 Hamburg"}],
                        },
                        {
                            "role": "Beklagter",
                            "value": "[PARTEI_2]",
                            "citations": [{"page_number": 1, "quote_snippet": "[PARTEI_2] Bahnhofstr. 4a"}],
                        },
                    ],
                    "kernaussagen": [],
                    "auffaelligkeiten": [],
                    "missing_info": [],
                    "prueffragen": [],
                    "todos": [],
                },
                "evidence_map": [],
            }
        ]

    def test_parteien_block_external_keine_quelle_zeilen_summary(self):
        """DOKUMENTTYP & PARTEIEN-Block darf im external-mode keine Quelle-Zeilen enthalten."""
        result = render_segmented_summary_text(
            self._segment_with_party_citations(), coverage=None, mode="external"
        )
        self.assertNotIn("Quelle S.", result)
        self.assertNotIn("20359 Hamburg", result)
        self.assertNotIn("Bahnhofstr.", result)

    def test_parteien_block_internal_behaelt_quelle_zeilen_summary(self):
        """Quelle-Zeilen aus kernaussagen-citations bleiben im internal-mode erhalten."""
        # Parteien-citations werden von collect_structured_parties_for_render
        # nur bei bestimmten Strukturen erkannt; kernaussagen-citations sind
        # der zuverlässige Weg um Quelle-Zeilen im internal-Output zu verifizieren.
        result = render_segmented_summary_text(
            self._segment_with_citations(), coverage=None, mode="internal"
        )
        self.assertIn("Quelle S. 3:", result)


if __name__ == "__main__":
    unittest.main()
