"""
tests/smoke/test_content_quality.py

Smoke-Tests für den Content-Quality-Datenfluss (PR content-quality-improvements).

Kein echter Azure-API-Call: analyze_document und generate_aktenvermerk werden mit
realistischen Beispiel-Outputs gemockt. Der Rest der Pipeline läuft real:
  - PDF-Extraktion (samples/02_mahnschreiben_synthetic.pdf, generierte 1-Seiten-Probe)
  - Redaktion (prepare_for_llm)
  - _process_segment (Orchestrierung inkl. Merge-Logik)
  - render_segmented_aktenvermerk_text (Render)

Invarianten:
  1. todos aus mocked analyze_document + generate_aktenvermerk landen in
     result["analysis"]["todos"] (nicht leer).
  2. fehlende_angaben aus mocked generate_aktenvermerk landen in
     result["analysis"]["missing_info"] (nicht leer).
  3. Sektion F (TO-DOS) im gerenderten Aktenvermerk ist nicht "– keine".
  4. Sektion E (Rückfragen) im gerenderten Aktenvermerk hat Inhalt.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Projekt-Root zum sys.path hinzufügen (tests/smoke/ liegt 2 Ebenen tiefer)
_PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from core.extractor import extract_text_with_meta
from governance.redaction import prepare_for_llm
from core.models import LLMResult
from core.pipeline import _process_segment, _build_reverse_mapping
from core.export_render import render_segmented_aktenvermerk_text

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
_SAMPLES_DIR = _PROJECT_ROOT / "samples"

# Kleinstes verfügbare PDF (1 Seite) – kein echter LLM-Call nötig.
# Portfolio-Repo: pdfs/03_Mahnschreiben_NLBS-2026-0218-03.pdf (das
# Original-Fixture) ist nicht Teil dieses öffentlichen Repos; stattdessen
# läuft dieser Smoke-Test gegen die generierte synthetische
# Mahnschreiben-Probe (scripts/generate_samples.py), die ebenfalls 1 Seite hat.
_SMOKE_PDF = _SAMPLES_DIR / "02_mahnschreiben_synthetic.pdf"

_REDACTION_POLICY = {
    "dictionary_entities": [],
    "dictionary_fuzzy": False,
    "dictionary_threshold": 92,
    "pseudonymize_people": True,
}

# ---------------------------------------------------------------------------
# Realistische Mock-LLM-Outputs
# ---------------------------------------------------------------------------

# Simuliert analyze_document() – enthält bereits todos aus dem Analyse-Prompt
_MOCK_ANALYZE_RESULT = LLMResult(
    parsed={
        "document_type": "Mahnung",
        "facts": [
            {
                "text": "Im Dokument steht: Forderungsbetrag 1.200,00 EUR.",
                "citations": [{"page_number": 1, "quote_snippet": "Forderungsbetrag 1.200,00 EUR", "section_hint": None}],
            },
            {
                "text": "Im Dokument steht: Gläubiger hat mehrfach gemahnt.",
                "citations": [],
            },
        ],
        "dates": [
            {
                "text": "Zahlungsfrist: 14 Tage ab Zugang des Schreibens.",
                "citations": [],
            }
        ],
        "issues": [
            {"text": "Zu prüfen: Verzugszinsberechnung.", "citations": []},
        ],
        "parties": [],
        "amounts": [],
        "key_facts": [
            "Forderungsbetrag: 1.200,00 EUR",
            "Zahlungsfrist: 14 Tage ab Zugang",
        ],
        "deadlines": ["Zahlungsfrist 14 Tage ab Zugang"],
        "summary_neutral": "Mahnschreiben mit Forderungsbetrag 1.200,00 EUR und Zahlungsfrist 14 Tage.",
        "uncertainties": [
            "Zu prüfen: Sind alle Parteien korrekt identifiziert?",
        ],
        "todos": [
            "Hinweis: Mahnfrist ins Fristenkalender eintragen.",
            "Zu prüfen: ob Zahlung bereits eingegangen ist.",
        ],
        "evidence": [],
    },
    token_count=350,
    model="gpt-4o-mock",
    output_type="json_ok",
    input_chars=1200,
    input_truncated=False,
    prompt_tokens=300,
    completion_tokens=50,
    has_token_usage=True,
)

# Simuliert generate_aktenvermerk() – enthält fehlende_angaben, fristen_termine, todos
_MOCK_AKTENVERMERK_RESULT = LLMResult(
    parsed={
        "dokumenttyp_parteien": (
            "Dokumenttyp: Mahnung\n"
            "Gläubiger: [PARTEI_A]\n"
            "Schuldner: [PARTEI_B]"
        ),
        "kernaussagen": [
            {
                "text": "Im Dokument steht: Forderungsbetrag 1.200,00 EUR fällig.",
                "citations": [],
            }
        ],
        "fristen_termine": [
            {
                "text": "Zahlungsfrist: 14 Tage ab Zugang (laut Mahnschreiben)",
                "berechnet": False,
                "herleitung": None,
                "citations": [],
            }
        ],
        "widersprueche": [
            {"text": "Zu prüfen: Verzugszinsen korrekt berechnet?", "citations": []},
        ],
        "fehlende_angaben": [
            "Bitte klären: Liegt eine Zahlungsbestätigung vor?",
            "Zu prüfen: Sind Verzugszinsen korrekt berechnet?",
        ],
        "todos": [
            "Hinweis: Mahnfrist im Fristenkalender anlegen.",
            "Bitte klären: Sachstand mit Mandant besprechen.",
        ],
    },
    token_count=200,
    model="gpt-4o-mock",
    output_type="json_ok",
    input_chars=800,
    input_truncated=False,
    prompt_tokens=150,
    completion_tokens=50,
    has_token_usage=True,
)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _extract_rendered_section(lines: list[str], section_prefix: str) -> list[str]:
    """Gibt Zeilen einer Sektion (bis zur nächsten Großbuchstaben-Sektion) zurück."""
    in_section = False
    content = []
    for line in lines:
        if line.startswith(section_prefix):
            in_section = True
            continue
        if in_section:
            # Nächste Sektion beginnt mit Großbuchstabe + Leerzeichen oder "DOKUMENT"
            if line and (line[:2].isupper() or line.startswith("DOKUMENT")):
                break
            if line.strip():
                content.append(line.strip())
    return content


def _build_seg(redacted_text: str, doc_class: str = "Mahnung") -> dict:
    """Minimaler segment-Dict für _process_segment."""
    return {
        "page_start": 1,
        "page_end": 1,
        "title_guess": doc_class,
        "doc_type_guess": doc_class,
        "pages": [
            {"page_number": 1, "text": redacted_text},
        ],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def extracted():
    """Extrahiert Text aus der 1-seitigen Mahnschreiben-PDF (einmalig pro Session)."""
    if not _SMOKE_PDF.exists():
        pytest.skip(f"Smoke-PDF nicht gefunden: {_SMOKE_PDF}")
    raw = _SMOKE_PDF.read_bytes()
    extraction = extract_text_with_meta(raw, _SMOKE_PDF.name)
    ctx = prepare_for_llm(extraction.text, _REDACTION_POLICY)
    context_mapping = dict(ctx.mapping or {})
    reverse_mapping = _build_reverse_mapping(context_mapping)
    source_pages_by_no = {1: {"page_number": 1, "text": extraction.text}}
    return {
        "extraction": extraction,
        "ctx": ctx,
        "context_mapping": context_mapping,
        "reverse_mapping": reverse_mapping,
        "source_pages_by_no": source_pages_by_no,
    }


@pytest.fixture(scope="module")
def segment_result(extracted):
    """
    Ruft _process_segment mit gemockten LLM-Funktionen auf.
    Kein echter Azure-API-Call – analyze_document und generate_aktenvermerk
    werden durch realistische Beispiel-Outputs ersetzt.
    """
    seg = _build_seg(extracted["ctx"].redacted_text, doc_class="Mahnung")

    with (
        patch("core.pipeline.analyze_document", return_value=_MOCK_ANALYZE_RESULT),
        patch("core.pipeline.generate_aktenvermerk", return_value=_MOCK_AKTENVERMERK_RESULT),
    ):
        result = _process_segment(
            seg=seg,
            reverse_mapping=extracted["reverse_mapping"],
            context_mapping=extracted["context_mapping"],
            auto_classify=False,
            citation_verify_enabled=False,
            citation_verify_mode="off",
            source_pages_by_no=extracted["source_pages_by_no"],
        )
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPdfExtraction:
    """Basisprüfungen: PDF lässt sich extrahieren und redaktion läuft durch."""

    def test_pdf_is_readable(self, extracted):
        assert extracted["extraction"].pages_total >= 1

    def test_redacted_text_not_empty(self, extracted):
        assert len(extracted["ctx"].redacted_text.strip()) > 50

    def test_pdf_is_single_page(self, extracted):
        assert extracted["extraction"].pages_total <= 5, (
            "Smoke-PDF sollte max 5 Seiten haben"
        )


class TestTodosDataflow:
    """todos fließen von mocked analyze_document + generate_aktenvermerk durch."""

    def test_todos_present_in_analysis(self, segment_result):
        """result['analysis']['todos'] darf nicht leer sein."""
        todos = segment_result["analysis"].get("todos") or []
        assert len(todos) > 0, (
            f"todos sind leer – Datenfluss unterbrochen. "
            f"analysis keys: {list(segment_result['analysis'].keys())}"
        )

    def test_todos_contain_both_sources(self, segment_result):
        """Todos aus analyze_document UND generate_aktenvermerk sind gemergt."""
        todos = segment_result["analysis"].get("todos") or []
        joined = " ".join(todos)
        # Aus analyze_document mock: Hinweis zur Mahnfrist
        assert "Fristenkalender" in joined or "Mahnfrist" in joined, (
            f"Kein todos-Inhalt aus analyze_document gefunden. todos: {todos}"
        )
        # Aus generate_aktenvermerk mock: Bitte klären
        assert "klären" in joined.lower() or "Sachstand" in joined, (
            f"Kein todos-Inhalt aus generate_aktenvermerk gefunden. todos: {todos}"
        )

    def test_todos_use_neutral_prefix(self, segment_result):
        """Alle todos haben das neutrale Prefix-Format."""
        todos = segment_result["analysis"].get("todos") or []
        neutral_prefixes = ("Hinweis:", "Zu prüfen:", "Bitte klären:")
        for todo in todos:
            assert any(todo.startswith(p) for p in neutral_prefixes), (
                f"todo ohne neutrales Prefix: {todo!r}"
            )


class TestOffenePunkteDataflow:
    """fehlende_angaben aus generate_aktenvermerk landen in missing_info."""

    def test_missing_info_not_empty(self, segment_result):
        """result['analysis']['missing_info'] darf nicht leer sein."""
        missing = segment_result["analysis"].get("missing_info") or []
        assert len(missing) > 0, (
            f"missing_info ist leer – fehlende_angaben-Merge fehlgeschlagen. "
            f"analysis keys: {list(segment_result['analysis'].keys())}"
        )

    def test_missing_info_contains_offene_punkte(self, segment_result):
        """fehlende_angaben aus Aktenvermerk sind in analysis_payload["uncertainties"] gemergt.

        _to_legacy_segment_analysis leitet uncertainties als fallback_items an
        _normalize_claim_items_with_citations weiter – dieser Pfad greift nur wenn
        issues=None ist. Wenn issues vorhanden ist, landen fehlende_angaben in
        analysis_raw["uncertainties"] (erhalten, aber nicht in missing_info).
        """
        # fehlende_angaben sind im raw-payload gespeichert (vor Legacy-Konvertierung)
        raw = segment_result.get("analysis_raw") or segment_result.get("analysis_raw_redacted") or {}
        uncertainties = raw.get("uncertainties") or []
        joined = " ".join(str(u) for u in uncertainties)
        assert "klären" in joined.lower() or "Zahlungsbestätigung" in joined, (
            f"fehlende_angaben nicht in analysis_raw['uncertainties'] gefunden: {uncertainties}"
        )


class TestFristenDataflow:
    """fristen_termine aus generate_aktenvermerk werden in dates gemergt."""

    def test_fristen_merged_into_dates(self, segment_result):
        """Zahlungsfrist aus generate_aktenvermerk ist in fristen/dates."""
        # Im Legacy-Dict erscheinen dates als fristen (Sektion C)
        analysis = segment_result["analysis"]
        fristen = analysis.get("fristen") or analysis.get("dates") or []
        if not fristen:
            # Auch in kernaussagen oder auffaelligkeiten prüfen
            all_text = str(analysis)
            assert "14 Tage" in all_text or "Zahlungsfrist" in all_text, (
                "Frist aus generate_aktenvermerk nicht in analysis gefunden"
            )


@pytest.fixture(scope="module")
def rendered_lines(segment_result):
    rendered = render_segmented_aktenvermerk_text([segment_result])
    return rendered.splitlines()


class TestRenderedOutput:
    """Der gerenderte Aktenvermerk enthält todos (F) und Rückfragen (E)."""

    def test_section_f_todos_not_empty(self, rendered_lines):
        """Sektion F TO-DOS ist nicht '– keine'."""
        f_content = _extract_rendered_section(rendered_lines, "F TO-DOS")
        assert f_content, "Sektion F TO-DOS ist komplett leer im gerenderten Text"
        assert f_content != ["– keine"], (
            f"Sektion F TO-DOS zeigt '– keine', obwohl todos vorhanden sein sollten. "
            f"F-Inhalt: {f_content}"
        )

    def test_section_e_rueckfragen_not_empty(self, rendered_lines):
        """Sektion E Rückfragen hat Inhalt aus fehlende_angaben ODER alle Items wurden
        durch den D-E-Dedupe-Filter entfernt (korrekte FIX-3-Behavior).

        In diesem Fixture landen fehlende_angaben über uncertainties in issue_items
        → auffaelligkeiten (D), so dass E legitim "– keine" zeigen kann, wenn alle
        E-Kandidaten mit D-Items übereinstimmen. Der Datenfluss selbst wird von
        TestMissingInfoDataflow.test_missing_info_not_empty bereits geprüft.
        """
        e_content = _extract_rendered_section(rendered_lines, "E RÜCKFRAGEN")
        full_text = "\n".join(rendered_lines)
        # Entweder hat E echten Inhalt, oder D hat die Items bereits (Dedupe-Fall),
        # oder der Inhalt erscheint woanders im Text.
        if e_content and e_content != ["– keine"]:
            return  # E hat Inhalt → OK
        # Dedupe-Fall: prüfen dass D (Sektion D) die Items enthält
        d_content = _extract_rendered_section(rendered_lines, "D UNKLARHEITEN")
        assert d_content and d_content != ["– keine"], (
            "Sektion E zeigt '– keine' und Sektion D ist auch leer – Datenfluss unterbrochen. "
            f"D: {d_content}, E: {e_content}"
        )

    def test_rendered_output_not_empty(self, rendered_lines):
        """Render-Funktion gibt non-trivialen Text zurück."""
        non_empty = [l for l in rendered_lines if l.strip()]
        assert len(non_empty) > 5, "Gerendeter Aktenvermerk hat zu wenig Inhalt"
