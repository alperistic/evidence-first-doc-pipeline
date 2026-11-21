import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
import re
from unittest.mock import patch

import pytest

from core.layout_analyzer import _keyword_score
from core.segmenter import build_segment_chunks, is_attachment_page, segment_by_document, segment_pages


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "segmenter"
# Seeds 0, 1, 3, 4 lieferten vor dem Fix 2 Segmente, 2 und 5 das richtige 1 Segment.
_HASH_SEEDS = (0, 1, 2, 3, 4, 5, 10, 12)
# Liest Seiten als JSON von stdin, segmentiert, gibt die Seitenbereiche als JSON aus.
_SEGMENT_RANGES_SCRIPT = (
    "import json, sys\n"
    "from core.segmenter import segment_pages\n"
    "segments = segment_pages(json.load(sys.stdin))\n"
    "print(json.dumps([[int(s['page_start']), int(s['page_end'])] for s in segments]))\n"
)
_TAGGED_PAGE_RE = re.compile(r"\[\[PAGE:(\d+)\]\]")


def _pages(texts: list[str]) -> list[dict]:
    return [
        {
            "page_number": idx,
            "page": idx,
            "text": text,
        }
        for idx, text in enumerate(texts, start=1)
    ]


def _fixture_text(name: str) -> str:
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8")


def _fixture_pages(name: str) -> list[dict]:
    text = _fixture_text(name)
    matches = list(_TAGGED_PAGE_RE.finditer(text))
    pages: list[dict] = []
    for idx, match in enumerate(matches):
        page_no = int(match.group(1))
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        pages.append(
            {
                "page_number": page_no,
                "page": page_no,
                "text": text[start:end].strip(),
            }
        )
    return pages


def _rahmenvertrag_anlage_bankverbindung_pages() -> list[dict]:
    """41-seitiger IT-Rahmenvertrag, Seite 20 = 'ANLAGE A' (ohne Doppelpunkt) mit
    Vertragsparteien + Bankverbindung. Auf Seite 20 stehen die Doctype-Keywords von
    'Anlage' und 'Vertrag' punktgleich - der Fall, dessen Ergebnis früher vom
    PYTHONHASHSEED abhing."""
    pages: list[dict] = []
    # Page 1: Deckblatt mit Vertragsparteien → setzt prev_strong_hits = {Vertragsparteien, zwischen}
    pages.append({
        "page_number": 1,
        "page": 1,
        "text": (
            "IT-Rahmenvertrag über IT-Dienstleistungen\n"
            "Vertragsparteien: [PARTEI_A] und [PARTEI_B]\n"
            "Zwischen den Vertragsparteien wird folgendes vereinbart."
        ),
    })
    # Pages 2–19: Fließtext mit nur 'zwischen' → prev_strong_hits driftet zu {zwischen}
    for i in range(2, 20):
        pages.append({
            "page_number": i,
            "page": i,
            "text": (
                f"§ {i} Regelungen\n"
                f"Zwischen Firma A und Firma B gilt:\n"
                f"Lorem ipsum dolor sit amet, Seite {i}."
            ),
        })
    # Page 20: Anlage ohne Doppelpunkt (nicht is_attachment) + Vertragsparteien (novel nach Seiten
    # 2-19 die nur 'zwischen' hatten) + Bankverbindung (is_contract_continuation).
    # Vor Fix D: strong_novel=True → Boundary auf Seite 20 → 2 Segmente; Fallback feuerte nicht.
    # Nach Fix D ohne continuation_applied-Flag: Boundary unterdrückt → starts={1} →
    # Fallback feuert mit 40 strong_pages → 14 Segmente.
    pages.append({
        "page_number": 20,
        "page": 20,
        "text": (
            "ANLAGE A\n"
            "Zahlungskonditionen und Vertragsparteien\n"
            "Vertragsparteien: [PARTEI_A] und [PARTEI_B]\n"
            "Bankverbindung: IBAN DE12 3456 7890 1234 5678 90\n"
            "Zahlungsziel: 30 Tage netto nach Rechnungseingang."
        ),
    })
    # Pages 21–41: weitere Vertragsseiten mit 'zwischen'
    for i in range(21, 42):
        pages.append({
            "page_number": i,
            "page": i,
            "text": (
                f"§ {i} Weitere Regelungen\n"
                f"Zwischen Firma A und Firma B gilt:\n"
                f"Weitere Bestimmungen des IT-Rahmenvertrags Seite {i}."
            ),
        })
    return pages


def _segment_debug_snapshot(segments: list[dict]) -> str:
    boundaries = [int(seg.get("page_start", seg.get("start_page", 0)) or 0) for seg in segments]
    ranges = [
        f"{int(seg.get('page_start', seg.get('start_page', 0)) or 0)}-"
        f"{int(seg.get('page_end', seg.get('end_page', 0)) or 0)}"
        for seg in segments
    ]
    reasons = [
        ",".join(str(reason) for reason in list(seg.get("boundary_reasons", []) or [])[:3])
        for seg in segments
    ]
    return f"boundaries={boundaries}; ranges={ranges}; reasons={reasons}"


class SegmenterTests(unittest.TestCase):
    def test_build_segment_chunks_sends_single_chunk_when_under_limit(self):
        pages = _pages(
            [
                "Dokument A Seite 1 mit etwas Inhalt.",
                "Dokument A Seite 2 mit weiterem Inhalt.",
                "Dokument A Seite 3 mit Abschluss.",
            ]
        )

        chunks = build_segment_chunks(pages, max_chars_per_chunk=12000)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].get("page_start"), 1)
        self.assertEqual(chunks[0].get("page_end"), 3)
        text = str(chunks[0].get("text", ""))
        self.assertIn("[Seite 1]", text)
        self.assertIn("[Seite 2]", text)
        self.assertIn("[Seite 3]", text)

    def test_build_segment_chunks_only_splits_when_over_limit(self):
        pages = _pages(
            [
                ("A " * 7000).strip(),
                ("B " * 7000).strip(),
                ("C " * 7000).strip(),
            ]
        )

        chunks = build_segment_chunks(pages, max_chars_per_chunk=8000)

        self.assertGreater(len(chunks), 1)

    def test_segment_by_document_splits_and_cleans_marker_text(self):
        full_text = (
            "[[PAGE:1]]\n"
            "=== DOKUMENT START (FIKTIV) ===\n"
            "Dokument-ID: PKG3-01 | Typ: Klageschrift | Seite 1/2 | Gesamt 1/4 | Az: 3 O 18/26\n"
            "Kanzlei Muster GmbH\n"
            "Klageantrag gegen die Beklagte.\n"
            "Seitenfuß-Marker: intern\n\n"
            "[[PAGE:2]]\n"
            "Kanzlei Muster GmbH\n"
            "Ausführliche Begründung zur Forderung.\n"
            "Seitenfuß-Marker: intern\n\n"
            "[[PAGE:3]]\n"
            "=== DOKUMENT START (FIKTIV) ===\n"
            "Dokument-ID: PKG3-02 | Typ: Mahnung | Seite 1/2 | Gesamt 3/4 | Az: 3 O 19/26\n"
            "Kanzlei Muster GmbH\n"
            "Forderungsaufstellung.\n"
            "Seitenfuß-Marker: intern\n\n"
            "[[PAGE:4]]\n"
            "Kanzlei Muster GmbH\n"
            "Zahlungsfrist bis zum 15.03.2026.\n"
            "Seitenfuß-Marker: intern\n"
        )

        segments = segment_by_document(full_text)

        self.assertEqual(len(segments), 2)
        self.assertIn("Dokument-ID: PKG3-01", segments[0])
        self.assertIn("Typ: Klageschrift", segments[0])
        self.assertIn("Dokument-ID: PKG3-02", segments[1])
        self.assertIn("Typ: Mahnung", segments[1])
        self.assertNotIn("Seitenfuß-Marker:", segments[0])
        self.assertNotIn("Seitenfuß-Marker:", segments[1])
        self.assertNotIn("Kanzlei Muster GmbH", segments[0])
        self.assertNotIn("Kanzlei Muster GmbH", segments[1])

    def test_marker_based_start_is_detected(self):
        pages = _pages(
            [
                "Allgemeine Einleitung\nzwischen den Parteien\n§ 1 Gegenstand",
                "Fortsetzung Vertragstext\nVergütung und Laufzeit",
                "DOKUMENT START\nDokument-ID: A-2026-001\nMietvertrag\nBetreff: Neuabschluss",
                "Weitere Angaben zum zweiten Dokument",
            ]
        )

        segments = segment_pages(pages)

        self.assertEqual(len(segments), 2, msg=_segment_debug_snapshot(segments))
        self.assertEqual(segments[0].get("page_start"), 1)
        self.assertEqual(segments[1].get("page_start"), 3)

    def test_structure_change_heading_betreff_is_detected(self):
        pages = _pages(
            [
                "fortlaufender text ohne heading\nzwischen den parteien",
                "weiterer absatz mit details und laufzeit",
                "Betreff: Mahnung aus Lieferung\nMAHNUNG\nAz. 12 C 34/26\nBitte zahlen Sie bis 20.03.2026",
                "Forderungsaufstellung und Fristsetzung",
            ]
        )

        segments = segment_pages(pages)

        self.assertGreaterEqual(
            len(segments),
            2,
            msg=f"Boundary für Betreff/Mahnung wurde nicht erkannt: {_segment_debug_snapshot(segments)}",
        )

    def test_fallback_splits_every_n_pages_when_no_boundaries(self):
        pages = _pages([f"neutrale seite {idx} ohne marker" for idx in range(1, 11)])

        segments = segment_pages(pages, max_pages_per_segment=4)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].get("page_start"), 1)
        self.assertEqual(segments[0].get("page_end"), 10)

    def test_pages_are_assigned_exactly_once_and_cover_full_range(self):
        pages = _pages(
            [
                "Einleitung und Kontext",
                "Fortsetzung ohne neue Struktur",
                "DOKUMENT START\nDokument-ID: B-1\nArbeitsvertrag",
                "Regelungen Gehalt und Probezeit",
                "Anlage A\nZusatzvereinbarung",
                "DOKUMENT START\nDokument-ID: C-1\nKlageschrift",
                "Anträge und Begründung",
                "Beweismittel",
                "Schlussanträge",
            ]
        )

        segments = segment_pages(pages)

        assigned = [p.get("page_number") for seg in segments for p in seg.get("pages", [])]
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(set(assigned), set(range(1, 10)))

    def test_regression_no_mixing_between_artificial_marked_documents(self):
        pages = _pages(
            [
                "DOKUMENT START\nDokument-ID: Z-1\nUNIQUE_A Vertragsteil Seite 1",
                "UNIQUE_A Vertragsteil Seite 2",
                "UNIQUE_A Vertragsteil Seite 3",
                "DOKUMENT START\nDokument-ID: Z-2\nUNIQUE_B Klageschrift Seite 1",
                "UNIQUE_B Klageschrift Seite 2",
                "UNIQUE_B Klageschrift Seite 3",
            ]
        )

        segments = segment_pages(pages)

        self.assertGreaterEqual(len(segments), 2)
        for seg in segments:
            text = "\n".join(str(p.get("text", "")) for p in seg.get("pages", []))
            self.assertFalse("UNIQUE_A" in text and "UNIQUE_B" in text)

    def test_document_marker_segmentation_does_not_merge_due_to_cap(self):
        pages = _pages(
            [
                (
                    f"DOKUMENT START\nDokument-ID: BULK-{idx:03d}\n"
                    f"Betreff: Sammelakte {idx}\nAz. {idx} C 12/26\nAnlage A\nEinleitung"
                    if idx % 2 == 1
                    else f"Fortsetzung Sammelakte {idx - 1}\nAbschnitt {idx}\nDetails"
                )
                for idx in range(1, 81)
            ]
        )
        max_cap = 25
        env = {
            "DOCPIPE_SEGMENT_MAX_SEGMENTS": str(max_cap),
            "DOCPIPE_SEGMENT_BOUNDARY_COOLDOWN_PAGES": "0",
            "DOCPIPE_SEGMENT_ADAPTIVE_THRESHOLD": "1",
        }
        with patch.dict("os.environ", env, clear=False):
            segments = segment_pages(pages)

        self.assertEqual(len(segments), 40)
        meta = segments[0].get("segmentation_meta", {})
        self.assertEqual(meta.get("mode"), "document_marker")
        self.assertEqual(int(meta.get("initial_segments", 0)), 40)
        self.assertFalse(bool(meta.get("merged_due_to_cap")))

    def test_universal_short_pdf_two_to_four_pages_stays_single_segment(self):
        pages = _pages(
            [
                "Einleitung Mandat\nKurzer Überblick",
                "Sachverhalt\nFortlaufender Text ohne Dokumentstart",
                "Bewertung und Hinweise\nkeine harte Trennmarke",
                "Abschluss und Fristnotizen",
            ]
        )
        with patch.dict("os.environ", {"DOCPIPE_SEGMENT_ADAPTIVE_THRESHOLD": "1"}, clear=False):
            segments = segment_pages(pages)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].get("page_start"), 1)
        self.assertEqual(segments[0].get("page_end"), 4)

    def test_document_marker_segmentation_keeps_single_page_docs(self):
        pages = _pages(
            [
                (
                    f"DOKUMENT START\nDokument-ID: MINI-{idx:03d}\n"
                    f"Betreff: Mini-Dokument {idx}\nAz. {idx} C 99/26\nAnlage A"
                )
                for idx in range(1, 13)
            ]
        )
        env = {
            "DOCPIPE_SEGMENT_MAX_SEGMENTS": "99",
            "DOCPIPE_SEGMENT_BOUNDARY_COOLDOWN_PAGES": "0",
            "DOCPIPE_SEGMENT_ADAPTIVE_THRESHOLD": "0",
        }
        with patch.dict("os.environ", env, clear=False):
            segments = segment_pages(pages)

        lengths = [len(seg.get("pages", [])) for seg in segments]
        self.assertEqual(len(segments), 12)
        self.assertTrue(lengths)
        self.assertTrue(all(length == 1 for length in lengths))

    def test_universal_coverage_complete_after_merge_without_overlap(self):
        pages = _pages(
            [
                (
                    f"DOKUMENT START\nDokument-ID: FULL-{idx:03d}\nBetreff: Volltest {idx}\nAz. {idx} C 7/26"
                    if idx % 3 == 0
                    else f"Lauftext Seite {idx}\nWeitere Inhalte"
                )
                for idx in range(1, 61)
            ]
        )
        env = {
            "DOCPIPE_SEGMENT_MAX_SEGMENTS": "15",
            "DOCPIPE_SEGMENT_BOUNDARY_COOLDOWN_PAGES": "1",
            "DOCPIPE_SEGMENT_ADAPTIVE_THRESHOLD": "1",
        }
        with patch.dict("os.environ", env, clear=False):
            segments = segment_pages(pages)

        assigned = [p.get("page_number") for seg in segments for p in seg.get("pages", [])]
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(set(assigned), set(range(1, 61)))

    def test_segment_by_document_bundle_fixture_creates_one_segment_per_document(self):
        segments = segment_by_document(_fixture_text("kanzlei_bundle_marker.txt"))

        self.assertEqual(len(segments), 3)
        self.assertIn("Az: 11 C 100/26", segments[0])
        self.assertIn("Az: 11 C 101/26", segments[1])
        self.assertIn("Az: 11 C 102/26", segments[2])

    def test_segment_by_document_doc_start_fixture_creates_one_segment_per_document(self):
        segments = segment_by_document(_fixture_text("kanzlei_doc_start_marker.txt"))

        self.assertEqual(len(segments), 3)
        self.assertIn("Dokument-ID: KANZ-A-001", segments[0])
        self.assertIn("Dokument-ID: KANZ-A-002", segments[1])
        self.assertIn("Dokument-ID: KANZ-A-003", segments[2])

    def test_segment_by_document_doc_id_fixture_fallback_creates_one_segment_per_document(self):
        segments = segment_by_document(_fixture_text("kanzlei_doc_id_marker.txt"))

        self.assertEqual(len(segments), 3)
        self.assertIn("Dokument-ID: KANZ-B-001", segments[0])
        self.assertIn("Dokument-ID: KANZ-B-002", segments[1])
        self.assertIn("Dokument-ID: KANZ-B-003", segments[2])

    def test_segment_debug_logging_is_env_gated(self):
        text = _fixture_text("kanzlei_bundle_marker.txt")

        with patch("builtins.print") as print_mock:
            with patch.dict("os.environ", {"DOCPIPE_SEGMENT_DEBUG": "0"}, clear=False):
                segment_by_document(text)
        debug_calls_disabled = [
            call for call in print_mock.call_args_list if call.args and "[SEGMENT_DEBUG]" in str(call.args[0])
        ]
        self.assertEqual(debug_calls_disabled, [])

        with patch("builtins.print") as print_mock:
            with patch.dict("os.environ", {"DOCPIPE_SEGMENT_DEBUG": "1"}, clear=False):
                segment_by_document(text)
        debug_calls_enabled = [
            call for call in print_mock.call_args_list if call.args and "[SEGMENT_DEBUG]" in str(call.args[0])
        ]
        self.assertGreaterEqual(len(debug_calls_enabled), 1)

    def test_mixed_pdf_segment_count_in_range(self):
        pages = _fixture_pages("mixed_oversegmentation_24_pages.txt")
        segments = segment_pages(pages)
        debug = _segment_debug_snapshot(segments)
        self.assertTrue(
            4 <= len(segments) <= 8,
            msg=f"Mixed-PDF Segmentanzahl außerhalb Zielkorridor (4..8): count={len(segments)}; {debug}",
        )

        previews = []
        for seg in segments:
            first_page = list(seg.get("pages", []) or [{}])[0]
            text = str(first_page.get("text", "") or "")[:800].lower()
            previews.append(text)
        preview_blob = "\n".join(previews)

        keyword_groups = [
            ("mietvertrag",),
            ("arbeitsvertrag",),
            ("mahnung",),
            ("vergleich", "ratenzahlungsvereinbarung"),
            ("anlage", "anlagenpaket"),
        ]
        covered = sum(1 for group in keyword_groups if any(keyword in preview_blob for keyword in group))
        self.assertGreaterEqual(
            covered,
            3,
            msg=f"Zu wenige DocType-Hinweise in Segment-Starts erkannt: covered={covered}; previews={previews}; {debug}",
        )

    def test_single_doc_not_oversegmented(self):
        # Einzelvertrag ohne separates Anlagen-Deckblatt soll als ein Segment bleiben.
        pages = _pages(
            [
                "Rahmenvertrag IT-Services\nAktenzeichen: RV-2026-333\nzwischen Auftraggeber und Auftragnehmer",
                "Leistungsumfang, Vergütung, Laufzeit und Kündigung.",
                "Datenschutzklauseln, Vertraulichkeit, Haftung.",
                "Service-Level, Reaktionszeiten und Eskalationspfad.",
                "Schlussbestimmungen und Unterschriften.",
            ]
        )

        segments = segment_pages(pages)
        self.assertEqual(
            len(segments),
            1,
            msg=f"Einzeldokument wurde unerwartet gesplittet: {_segment_debug_snapshot(segments)}",
        )

    def test_existing_kanzlei_testpaket_regression(self):
        pages = _pages(
            [
                "Mietvertrag\nAktenzeichen: KANZ-V2-001\nVertragsparteien: A und B",
                "Regelungen Mietzins und Nebenkosten.",
                "Arbeitsvertrag\nDokumenten-ID: KANZ-V2-002\nBetreff: Arbeitsvertrag",
                "Arbeitszeit und Vergütung.",
                "Mahnung\nBetreff: Offene Forderung\nAbsender: Kanzlei\nEmpfänger: Mandant",
                "Forderungsaufstellung und Fristsetzung.",
                "Ratenzahlungsvereinbarung\nVereinbarung wird geschlossen.",
                "Ratenplan und Fälligkeit.",
                "Anlagen\nAnlage 1: Preisblatt\nAnlage A: SLA",
                "Auftragsverarbeitung / DPA und Anlagenhinweise.",
            ]
        )

        segments = segment_pages(pages)
        count = len(segments)
        self.assertTrue(
            4 <= count <= 6,
            msg=f"Kanzlei-Testpaket-Regression außerhalb Erwartung (~5): count={count}; {_segment_debug_snapshot(segments)}",
        )


    def test_attachment_page_is_not_a_boundary(self):
        """Rahmenvertrag mit Anlagen: Anlagen-Seiten erzeugen keine neuen Segmente."""
        pages = _pages(
            [
                "Rahmenvertrag IT-Dienstleistungen\nAktenzeichen: RV-2026-500\nzwischen Auftraggeber und Auftragnehmer",
                "Leistungsumfang und Vergütung gemäß § 3.",
                "Laufzeit, Kündigung und Haftung.",
                "Anlage A: Service Level Agreement\nReaktionszeiten und Eskalationspfad.",
                "Anlage 1: Auftragsverarbeitung (AVV)\nVerarbeitung personenbezogener Daten.",
            ]
        )
        segments = segment_pages(pages)
        self.assertEqual(
            len(segments),
            1,
            msg=f"Rahmenvertrag mit Anlagen wurde gesplittet: {_segment_debug_snapshot(segments)}",
        )

    def test_is_attachment_page_detects_anlage_patterns(self):
        self.assertTrue(is_attachment_page("Anlage A: Service Level Agreement"))
        self.assertTrue(is_attachment_page("Anlage 1: Preisblatt\nDetails folgen."))
        self.assertFalse(is_attachment_page("Anlagen sind beigefügt."))
        self.assertFalse(is_attachment_page("Rahmenvertrag über IT-Dienstleistungen"))


class PR9RahmenvertragSegmentierungTests(unittest.TestCase):
    """Fix D: Rahmenvertrag mit Zahlungsdetails-Seite als 1 Segment."""

    def test_rahmenvertrag_with_zahlungsdetails_stays_single_segment(self):
        """Rahmenvertrag: Zahlungsdetails/Rechnungsempfänger-Seite darf keine neue Grenze erzeugen."""
        pages: list[dict] = []
        # Page 1: Deckblatt Rahmenvertrag mit starken Markern
        pages.append({
            "page_number": 1,
            "page": 1,
            "text": (
                "IT-Rahmenvertrag über Dienstleistungen\n"
                "Vertragsparteien: [PARTEI_A] und [PARTEI_B]\n"
                "Zwischen den Vertragsparteien wird folgendes vereinbart.\n"
                "§ 1 Leistungsbeschreibung"
            ),
        })
        # Pages 2-8: Lauftext ohne neue Dokumentmarker
        for i in range(2, 9):
            pages.append({
                "page_number": i,
                "page": i,
                "text": (
                    f"Laufende Regelungen des IT-Rahmenvertrags Seite {i}\n"
                    f"§ {i} Haftung und Gewährleistung gemäß den vertraglichen Bestimmungen."
                ),
            })
        # Page 9: Zahlungsdetails – soll NICHT als neues Dokument erkannt werden
        pages.append({
            "page_number": 9,
            "page": 9,
            "text": (
                "Zahlungsdetails und Rechnungsempfänger\n"
                "Bankverbindung: IBAN DE12 3456 7890 1234 5678 90\n"
                "Zahlungsziel: 30 Tage netto nach Rechnungseingang.\n"
                "Dieser Abschnitt ist fester Bestandteil des Rahmenvertrags."
            ),
        })
        # Pages 10-12: Weitere Vertragsseiten
        for i in range(10, 13):
            pages.append({
                "page_number": i,
                "page": i,
                "text": f"Weitere Vertragsregelungen Seite {i}\nSonstige Bestimmungen des IT-Rahmenvertrags.",
            })

        env = {
            "DOCPIPE_SEGMENT_ADAPTIVE_THRESHOLD": "1",
            "DOCPIPE_SEGMENT_BOUNDARY_COOLDOWN_PAGES": "2",
        }
        with patch.dict("os.environ", env, clear=False):
            segments = segment_pages(pages)

        # Page 9 (Zahlungsdetails) darf keine neue Grenze erzeugen
        boundary_pages = [
            seg.get("page_start") for seg in segments if seg.get("page_start", 1) > 1
        ]
        self.assertNotIn(
            9,
            boundary_pages,
            msg=f"Zahlungsdetails-Seite darf keine Grenze erzeugen: {_segment_debug_snapshot(segments)}",
        )

    def test_rahmenvertrag_novel_vertragsparteien_plus_bankverbindung_stays_single(self):
        """Regression Fix D: Anlage-Seite mit 'Vertragsparteien' (novel) + Bankverbindung darf
        keinen Fallback-Split auslösen.  Vorher (PR#8): 2 Segmente; nach Fix D ohne diesen
        Test: 14 Segmente; Ziel: 1 Segment."""
        pages = _rahmenvertrag_anlage_bankverbindung_pages()

        segments = segment_pages(pages)

        self.assertEqual(
            len(segments),
            1,
            msg=(
                f"41-seitiger IT-Rahmenvertrag mit Anlage-Seite (Vertragsparteien+Bankverbindung) "
                f"muss als 1 Segment erkannt werden, nicht als {len(segments)}: "
                f"{_segment_debug_snapshot(segments)}"
            ),
        )

    def test_rahmenvertrag_anlage_single_segment_for_every_hash_seed(self):
        """Regression: das Ergebnis darf nicht von PYTHONHASHSEED abhängen.

        Früher hing der Doctype-Hinweis von Seite 20 (Anlage und Vertrag punktgleich)
        von der Iterationsreihenfolge eines str-Sets in _keyword_score ab; je nach Seed
        kam 1 oder 2 Segmente heraus. Der Hash-Seed ist pro Interpreter fest, deshalb
        läuft jeder Seed in einem eigenen Unterprozess."""
        pages_json = json.dumps(_rahmenvertrag_anlage_bankverbindung_pages())
        results: dict[int, list[list[int]]] = {}
        for seed in _HASH_SEEDS:
            env = {**os.environ, "PYTHONHASHSEED": str(seed)}
            proc = subprocess.run(
                [sys.executable, "-c", _SEGMENT_RANGES_SCRIPT],
                input=pages_json,
                capture_output=True,
                text=True,
                cwd=_PROJECT_ROOT,
                env=env,
                timeout=60,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=f"seed={seed}: {proc.stderr[-2000:]}")
            results[seed] = json.loads(proc.stdout.strip().splitlines()[-1])

        expected = {seed: [[1, 41]] for seed in _HASH_SEEDS}
        self.assertEqual(results, expected)

    def test_keyword_score_breaks_ties_in_declaration_order(self):
        """Gleichstand Anlage/Vertrag → der in _DOC_TYPE_KEYWORDS zuerst deklarierte
        Typ gewinnt (dieselbe Regel wie _guess_doc_type_from_text), unabhängig von der
        Reihenfolge, in der die Treffer anfallen."""
        top_text = "ANLAGE A\nZahlungskonditionen und Vertragsparteien"
        _, doc_type, _ = _keyword_score(top_text, top_text)
        self.assertEqual(doc_type, "Anlage")

    def test_two_separate_contracts_with_different_parties_stay_two_segments(self):
        """Regression: Zwei echte separate Verträge mit unterschiedlichen Parteien → 2 Segmente."""
        pages = _pages([
            # Vertrag 1: Mietvertrag
            "DOKUMENT START\nDokument-ID: V-001\nMietvertrag\nVermieter: [PARTEI_A]\nMieter: [PARTEI_B]",
            "§ 1 Mietgegenstand\nDie Wohnung wird zu Wohnzwecken vermietet.",
            "§ 2 Mietzins\nMonatliche Miete: EUR 1.200,00",
            # Vertrag 2: Arbeitsvertrag
            "DOKUMENT START\nDokument-ID: V-002\nArbeitsvertrag\nArbeitgeber: [PARTEI_C]\nArbeitnehmer: [PARTEI_D]",
            "§ 1 Beginn des Arbeitsverhältnisses\nProbezeit: 6 Monate",
            "§ 2 Vergütung\nGehalt: EUR 4.500,00 monatlich",
        ])

        env = {
            "DOCPIPE_SEGMENT_ADAPTIVE_THRESHOLD": "0",
            "DOCPIPE_SEGMENT_BOUNDARY_COOLDOWN_PAGES": "0",
        }
        with patch.dict("os.environ", env, clear=False):
            segments = segment_pages(pages)

        self.assertGreaterEqual(
            len(segments),
            2,
            msg=f"Zwei separate Verträge müssen als 2 Segmente erkannt werden: "
            f"{_segment_debug_snapshot(segments)}",
        )


    def test_stresstest_multi_doc_18_pages_min_4_segments(self):
        """18-Seiten-PDF mit Testament, OWi-Einspruch, Scheidungsfolgenvereinbarung → mind. 4 Segmente."""
        pages = _pages([
            # Dok 1: Mietvertrag S.1-3
            "Mietvertrag\nAktenzeichen: MV-2026-001\nzwischen Vermieter und Mieter",
            "Regelungen Mietzins und Nebenkosten.",
            "Schönheitsreparaturen und Kündigung.",
            # Dok 2: Scheidungsfolgenvereinbarung S.4-8
            "Scheidungsfolgenvereinbarung\nzwischen Ehegatten A und B\nScheidung und Unterhalt",
            "Vermögensauseinandersetzung und Zugewinnausgleich.",
            "Sorgerecht und Umgang.",
            "Unterhalt und Altersvorsorge.",
            "Schlussbestimmungen.",
            # Dok 3: OWi-Einspruch S.9
            "Einspruch gegen Bußgeldbescheid\nOrdnungswidrigkeit gemäß § 41 StVO\nBußgeldbescheid vom 01.03.2026",
            # Dok 4: Testament S.10
            "Mein Testament\nIch, Erblasser Max Mustermann, verfüge letztwillig:\nMein Erbe soll meiner Tochter zufallen.",
            # Dok 5: Pflichtteilsberechnung S.11-18
            "Pflichtteilsberechnung\nNachlassverzeichnis per 01.01.2026",
            "Aktiva: Immobilien und Bankguthaben.",
            "Passiva: Verbindlichkeiten.",
            "Pflichtteilsanspruch der Kinder.",
            "Berechnungsgrundlagen.",
            "Bewertungsansätze.",
            "Ergebnis der Pflichtteilsberechnung.",
            "Unterschrift Notar.",
        ])
        segments = segment_pages(pages)
        self.assertGreaterEqual(
            len(segments),
            4,
            msg=f"Stresstest 18 Seiten: erwartet ≥4 Segmente, bekommen {len(segments)}; "
            f"{_segment_debug_snapshot(segments)}",
        )

    def test_single_page_doc_with_strong_keyword_not_merged(self):
        """Testament als 1-Seiten-Segment zwischen längeren Docs darf nicht gemergt werden."""
        pages = _pages([
            "Mietvertrag\nAktenzeichen: MV-001\nzwischen Vermieter und Mieter",
            "Nebenkosten und Kaution.",
            "Mein Testament\nIch verfüge letztwillig: Mein Erbe soll meiner Tochter zufallen.",
            "Pflichtteilsberechnung\nNachlassverzeichnis per 01.01.2026",
            "Pflichtteilsanspruch berechnet.",
        ])
        segments = segment_pages(pages)
        self.assertGreaterEqual(
            len(segments),
            2,
            msg=f"1-Seiten-Testament wurde fälschlicherweise gemergt: {_segment_debug_snapshot(segments)}",
        )
        first_page_texts = [
            str(list(seg.get("pages", [{}]))[0].get("text", "")).lower()
            for seg in segments
        ]
        self.assertTrue(
            any("testament" in p for p in first_page_texts),
            msg=f"Testament nicht als eigener Segment-Start erkannt: {first_page_texts}",
        )


if __name__ == "__main__":
    unittest.main()
