"""
test_demo_stub.py – Unit Tests für core/demo_stub.py

Getestet:
  - Determinismus       (gleicher Input -> identischer Output, zweifach)
  - Schema-Validität    (Output validiert gegen jedes relevante Pydantic-Model)
  - Zitat-Echtheit      (jedes quote_snippet ist ein echtes Substring des Inputs)
  - _invoke_json_task-Integration im Demo-Modus (core/llm_client.py)
"""
from __future__ import annotations

from core.demo_stub import (
    _clip_quote,
    _detect_doc_type,
    _sentences_per_page,
    _split_pages,
    build_demo_result,
)
from core.models import (
    AktenvermerkPayloadModel,
    ChunkPayloadModel,
    DocumentAnalysisPayloadModel,
    SegmentSummaryPayloadModel,
    SummaryPayloadModel,
    VerifyMappingPayloadModel,
)

# ---------------------------------------------------------------------------
# Testdaten – realistischer, [Seite N]-getaggter Analyse-Text
# ---------------------------------------------------------------------------

_SAMPLE_TEXT = (
    "[Seite 1]\n"
    "Mietvertrag zwischen Vermieter Max Mustermann und Mieterin Erika Musterfrau. "
    "Die Kaltmiete beträgt EUR 850,00 monatlich, zzgl. Nebenkosten. "
    "Die Kaution in Höhe von EUR 2.550,00 ist bei Vertragsbeginn zu hinterlegen. "
    "Der Mietbeginn ist der 01.09.2026.\n"
    "[Seite 2]\n"
    "Die Kündigungsfrist beträgt drei Monate zum Monatsende. "
    "Zahlungen sind bis zum dritten Werktag jedes Monats zu leisten. "
    "Bei Zahlungsverzug fallen Verzugszinsen an."
)

_SAMPLE_TEXT_NO_MARKERS = (
    "Dies ist ein einfacher Textausschnitt ohne Seitenmarker. "
    "Er dient dazu den Fallback-Pfad von _split_pages zu testen. "
    "Ein weiterer Satz mit genug Wörtern für die Extraktion."
)


def _all_quote_snippets(payload: object) -> list[str]:
    """Rekursiv alle quote_snippet-Werte aus einem verschachtelten dict/list einsammeln."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "quote_snippet" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_all_quote_snippets(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_all_quote_snippets(item))
    return found


_SCHEMAS = [
    DocumentAnalysisPayloadModel,
    AktenvermerkPayloadModel,
    SummaryPayloadModel,
    SegmentSummaryPayloadModel,
    ChunkPayloadModel,
]


# ---------------------------------------------------------------------------
# Determinismus
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_gleicher_input_liefert_identischen_output(self):
        for schema in _SCHEMAS:
            first = build_demo_result(
                text=_SAMPLE_TEXT, system_prompt="irrelevant", model_schema=schema
            )
            second = build_demo_result(
                text=_SAMPLE_TEXT, system_prompt="irrelevant", model_schema=schema
            )
            assert first == second, f"nicht deterministisch für {schema.__name__}"

    def test_unterschiedlicher_system_prompt_aendert_nichts(self):
        # system_prompt wird bewusst ignoriert (Doc-Type kommt aus dem Text).
        a = build_demo_result(
            text=_SAMPLE_TEXT,
            system_prompt="Prompt A",
            model_schema=DocumentAnalysisPayloadModel,
        )
        b = build_demo_result(
            text=_SAMPLE_TEXT,
            system_prompt="Ein ganz anderer Prompt B",
            model_schema=DocumentAnalysisPayloadModel,
        )
        assert a == b

    def test_split_pages_und_sentences_deterministisch(self):
        pages_1 = _split_pages(_SAMPLE_TEXT)
        pages_2 = _split_pages(_SAMPLE_TEXT)
        assert pages_1 == pages_2
        assert _sentences_per_page(pages_1) == _sentences_per_page(pages_2)


# ---------------------------------------------------------------------------
# Schema-Validität
# ---------------------------------------------------------------------------

class TestSchemaValiditaet:
    def test_document_analysis_payload_validiert(self):
        payload = build_demo_result(
            text=_SAMPLE_TEXT,
            system_prompt="",
            model_schema=DocumentAnalysisPayloadModel,
        )
        model = DocumentAnalysisPayloadModel.model_validate(payload)
        assert model.document_type == "Mietvertrag"

    def test_aktenvermerk_payload_validiert(self):
        payload = build_demo_result(
            text=_SAMPLE_TEXT, system_prompt="", model_schema=AktenvermerkPayloadModel
        )
        model = AktenvermerkPayloadModel.model_validate(payload)
        assert "Mietvertrag" in model.dokumenttyp_parteien

    def test_summary_payload_validiert(self):
        payload = build_demo_result(
            text=_SAMPLE_TEXT, system_prompt="", model_schema=SummaryPayloadModel
        )
        SummaryPayloadModel.model_validate(payload)

    def test_segment_summary_payload_validiert(self):
        payload = build_demo_result(
            text=_SAMPLE_TEXT, system_prompt="", model_schema=SegmentSummaryPayloadModel
        )
        SegmentSummaryPayloadModel.model_validate(payload)

    def test_chunk_payload_validiert(self):
        payload = build_demo_result(
            text=_SAMPLE_TEXT, system_prompt="", model_schema=ChunkPayloadModel
        )
        ChunkPayloadModel.model_validate(payload)

    def test_verify_mapping_payload_validiert(self):
        import json

        verify_input = json.dumps(
            {
                "claims": {
                    "kernaussagen": ["Behauptung eins", "Behauptung zwei"],
                    "auffaelligkeiten": [],
                    "top3_findings": [],
                },
                "segment_pages": [1, 2],
                "segment_text": _SAMPLE_TEXT,
            }
        )
        payload = build_demo_result(
            text=verify_input, system_prompt="", model_schema=VerifyMappingPayloadModel
        )
        model = VerifyMappingPayloadModel.model_validate(payload)
        assert len(model.kernaussagen) == 2

    def test_unbekanntes_schema_liefert_leeres_dict(self):
        class _UnknownSchema:
            __name__ = "SomeUnknownModel"

        result = build_demo_result(
            text=_SAMPLE_TEXT, system_prompt="", model_schema=_UnknownSchema  # type: ignore[arg-type]
        )
        assert result == {}

    def test_text_ohne_seiten_marker_faellt_nicht_auseinander(self):
        for schema in _SCHEMAS:
            payload = build_demo_result(
                text=_SAMPLE_TEXT_NO_MARKERS, system_prompt="", model_schema=schema
            )
            schema.model_validate(payload)


# ---------------------------------------------------------------------------
# Zitat-Echtheit – die kritischste Eigenschaft der Demo-Stub
# ---------------------------------------------------------------------------

class TestZitatEchtheit:
    def test_alle_quote_snippets_sind_substrings_des_inputs(self):
        for schema in _SCHEMAS:
            payload = build_demo_result(
                text=_SAMPLE_TEXT, system_prompt="", model_schema=schema
            )
            quotes = _all_quote_snippets(payload)
            assert quotes, f"{schema.__name__} hat keine Zitate produziert"
            for quote in quotes:
                assert quote in _SAMPLE_TEXT, (
                    f"Zitat ist KEIN echtes Substring des Inputs "
                    f"({schema.__name__}): {quote!r}"
                )

    def test_evidence_quotes_liegen_auf_richtiger_seite(self):
        payload = build_demo_result(
            text=_SAMPLE_TEXT,
            system_prompt="",
            model_schema=DocumentAnalysisPayloadModel,
        )
        pages = dict(_split_pages(_SAMPLE_TEXT))
        for item in payload["evidence"]:
            page_no = item["page_number"]
            quote = item["quote_snippet"]
            assert page_no in pages
            assert quote in pages[page_no], (
                f"Zitat {quote!r} nicht auf der behaupteten Seite {page_no} gefunden"
            )

    def test_verify_mapping_citations_sind_substrings(self):
        import json

        verify_input = json.dumps(
            {
                "claims": {
                    "kernaussagen": ["Behauptung eins", "Behauptung zwei"],
                    "auffaelligkeiten": ["Behauptung drei"],
                    "top3_findings": [],
                },
                "segment_pages": [1, 2],
                "segment_text": _SAMPLE_TEXT,
            }
        )
        payload = build_demo_result(
            text=verify_input, system_prompt="", model_schema=VerifyMappingPayloadModel
        )
        quotes = _all_quote_snippets(payload)
        assert quotes
        for quote in quotes:
            assert quote in _SAMPLE_TEXT

    def test_clip_quote_ist_praefix_kein_erfundener_text(self):
        long_sentence = " ".join(f"Wort{i}" for i in range(40))
        clipped = _clip_quote(long_sentence)
        assert clipped in long_sentence
        assert len(clipped) <= 170


# ---------------------------------------------------------------------------
# Doc-type detection (Heuristik, nicht auf konkrete Samples gehardcoded)
# ---------------------------------------------------------------------------

class TestDocTypeDetection:
    def test_mietvertrag_erkannt(self):
        assert _detect_doc_type(_SAMPLE_TEXT) == "Mietvertrag"

    def test_klageschrift_erkannt(self):
        text = "[Seite 1]\nDie Klägerin erhebt Klage vor dem Landgericht gegen den Beklagten."
        assert _detect_doc_type(text) == "Klageschrift"

    def test_mahnung_erkannt(self):
        text = "[Seite 1]\nZahlungserinnerung: Der Rückstand ist umgehend zu begleichen, sonst Mahngebühr."
        assert _detect_doc_type(text) == "Mahnung"

    def test_unbekannter_text_faellt_auf_sonstiges_zurueck(self):
        assert _detect_doc_type("Ein völlig neutraler Text ohne Schlüsselwörter.") == "Sonstiges"


# ---------------------------------------------------------------------------
# Integration über core/llm_client.py::_invoke_json_task im Demo-Modus
# ---------------------------------------------------------------------------

class TestInvokeJsonTaskDemoModeIntegration:
    def test_demo_mode_liefert_json_ok_ohne_azure_env(self, monkeypatch):
        monkeypatch.setenv("DOCPIPE_DEMO_MODE", "1")
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)

        from core.llm_client import _invoke_json_task

        result = _invoke_json_task(
            text=_SAMPLE_TEXT,
            system_prompt="irrelevant im Demo-Modus",
            model_schema=DocumentAnalysisPayloadModel,
            max_output_tokens=1000,
        )
        assert result.output_type == "json_ok"
        assert result.model == "demo-stub"
        assert result.parsed["document_type"] == "Mietvertrag"

    def test_demo_mode_deterministisch_ueber_zwei_aufrufe(self, monkeypatch):
        monkeypatch.setenv("DOCPIPE_DEMO_MODE", "1")
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)

        from core.llm_client import _invoke_json_task

        first = _invoke_json_task(
            text=_SAMPLE_TEXT,
            system_prompt="p",
            model_schema=AktenvermerkPayloadModel,
            max_output_tokens=1000,
        )
        second = _invoke_json_task(
            text=_SAMPLE_TEXT,
            system_prompt="p",
            model_schema=AktenvermerkPayloadModel,
            max_output_tokens=1000,
        )
        assert first.parsed == second.parsed
        assert first.token_count == second.token_count

    def test_demo_mode_off_wirft_environment_error_ohne_azure_env(self, monkeypatch):
        monkeypatch.delenv("DOCPIPE_DEMO_MODE", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)

        from core.llm_client import _invoke_json_task

        try:
            _invoke_json_task(
                text=_SAMPLE_TEXT,
                system_prompt="p",
                model_schema=DocumentAnalysisPayloadModel,
                max_output_tokens=1000,
            )
            raised = False
        except EnvironmentError as exc:
            raised = True
            assert "DOCPIPE_DEMO_MODE=1" in str(exc)
        assert raised, "erwartet: EnvironmentError wenn Demo-Modus aus und Azure-Env fehlt"

    def test_demo_mode_0_verhaelt_sich_wie_aus(self, monkeypatch):
        monkeypatch.setenv("DOCPIPE_DEMO_MODE", "0")
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)

        from core.llm_client import _invoke_json_task

        try:
            _invoke_json_task(
                text=_SAMPLE_TEXT,
                system_prompt="p",
                model_schema=DocumentAnalysisPayloadModel,
                max_output_tokens=1000,
            )
            raised = False
        except EnvironmentError:
            raised = True
        assert raised, "DOCPIPE_DEMO_MODE=0 muss sich wie deaktiviert verhalten"
