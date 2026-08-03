"""
test_redaction_fail_closed.py

Post-Redaction Fail-Closed Tests:
- validate_mandantenexport() wirft RedactionLeakError bei PII
- redact(..., validate=True) wirft bei PII-Leak
- redact(..., validate=False) wirft nie
- leak_count stimmt mit Treffer-Anzahl überein
- Error-Message enthält keine Klartext-PII
"""

import pytest
from unittest.mock import MagicMock, patch

from governance.redaction import (
    RedactionLeakError,
    redact,
    validate_mandantenexport,
)

LEAK_FIXTURES = [
    ("adresse", "Wohnhaft in der Hauptstraße 23, 80331 München."),
    ("iban", "Konto: DE89 3704 0044 0532 0130 00"),
    ("email", "Kontakt: max.mustermann@kanzlei.de"),
    ("telefon", "Erreichbar unter 0171 123 45 67"),
    ("plz_stadt", "Wohnort: 10115 Berlin"),
]

SAFE_FIXTURES = [
    "Das Gericht hat entschieden.",
    "Die Klage ist zulässig.",
    "Der Vertrag wurde geschlossen.",
    "Streitwert beträgt [BETRAG_1].",
    "Partei [PARTEI_1] macht geltend.",
]


def test_validator_wirft_bei_adresse():
    with pytest.raises(RedactionLeakError):
        validate_mandantenexport("Wohnhaft in der Hauptstraße 23, 80331 München.")


def test_validator_wirft_bei_iban():
    with pytest.raises(RedactionLeakError):
        validate_mandantenexport("Konto: DE89 3704 0044 0532 0130 00")


def test_validator_wirft_bei_email():
    with pytest.raises(RedactionLeakError):
        validate_mandantenexport("Kontakt: max.mustermann@kanzlei.de")


def test_validator_wirft_bei_telefon():
    with pytest.raises(RedactionLeakError):
        validate_mandantenexport("Erreichbar unter 0171 123 45 67")


def test_validator_wirft_bei_plz():
    with pytest.raises(RedactionLeakError):
        validate_mandantenexport("Wohnort: 10115 Berlin")


@pytest.mark.parametrize("safe_text", SAFE_FIXTURES)
def test_validator_still_bei_sauberem_text(safe_text):
    # Kein Fehler erwartet
    validate_mandantenexport(safe_text)


def test_redact_validate_true_wirft_bei_leak():
    """redact() mit validate=True wirft RedactionLeakError bei unredacteten PII.
    
    Simuliert einen Leak durch direkten validate_mandantenexport-Aufruf.
    """
    from governance.redaction import validate_mandantenexport
    with pytest.raises(RedactionLeakError):
        validate_mandantenexport("Telefon: +49 721 100 200")


def test_redact_validate_false_wirft_nicht():
    """redact() mit validate=False (Default) wirft nie, egal was im Text steht."""
    raw = "Erreichbar unter max@example.com, DE89 3704 0044 0532 0130 00"
    # Kein Fehler erwartet
    result = redact(raw, validate=False)
    assert isinstance(result, str)


def test_leak_count_korrekt():
    """leak_count im Error entspricht der Gesamtzahl der Pattern-Treffer."""
    # Zwei getrennte PII-Vorkommen: eine E-Mail + eine PLZ+Stadt
    text = "Mail: a@b.de und Adresse: 80331 München"
    with pytest.raises(RedactionLeakError) as exc_info:
        validate_mandantenexport(text)
    assert exc_info.value.leak_count >= 2


def test_keine_pii_in_error_message():
    """Die Error-Message darf keine Klartext-PII enthalten."""
    pii_text = "Konto: DE89 3704 0044 0532 0130 00"
    with pytest.raises(RedactionLeakError) as exc_info:
        validate_mandantenexport(pii_text)
    msg = str(exc_info.value)
    # Die IBAN selbst darf nicht in der Fehlermeldung stehen
    assert "DE89" not in msg
    assert "3704" not in msg
    # Nur leak_count und generische Beschreibung
    assert "PII-Treffer" in msg
    assert "Export gesperrt" in msg


def test_export_allowed_when_redact_required_false():
    """build_exports() darf validate_mandantenexport() nicht aufrufen wenn
    redact_required=False, auch wenn PII im Export-Text enthalten ist.

    Hintergrund: Wenn kein Redact-Durchlauf stattfand, ist echte PII im
    Export-Text erwartet und kein Fehler. Der Guard ist nur sinnvoll wenn
    zuvor pseudonymisiert wurde und Platzhalter nicht restauriert wurden.
    """
    from services.export_service import build_exports

    _empty_seg = {
        "segment_meta": {"page_start": 1, "page_end": 2, "doc_type_guess": "Test"},
        "analysis": {},
        "analysis_raw": {},
        "analysis_output_type": "json_ok",
        "evidence_map": [],
        "coverage": {"pages_total": 2, "chunks_sent": 1},
    }

    with (
        patch("services.export_service._build_redacted_export_payload", return_value=[]),
        patch("services.export_service.apply_output_mode", return_value=[]),
        patch("services.export_service.render_segmented_summary_text", return_value="Hauptstraße 12, 80331 München"),
        patch("services.export_service.render_segmented_aktenvermerk_text", return_value=""),
        patch("services.export_service.build_export_content", return_value={"summary": "Hauptstraße 12, 80331 München", "aktennotiz": ""}),
        patch("services.export_service.to_pdf_bytes", return_value=b"pdf"),
        patch("services.export_service.to_docx_bytes", return_value=b"docx"),
    ):
        # redact_required=True → muss RedactionLeakError werfen (PII im Text)
        with pytest.raises(RedactionLeakError):
            build_exports(
                [_empty_seg],
                {},
                {"pages_total": 2, "pages_extracted": 2, "chunks_sent": 1},
                {},
                "Zusammenfassung",
                redact_required=True,
            )

        # redact_required=False → kein Fehler, obwohl dieselbe PII im Text
        result = build_exports(
            [_empty_seg],
            {},
            {"pages_total": 2, "pages_extracted": 2, "chunks_sent": 1},
            {},
            "Zusammenfassung",
            redact_required=False,
        )
        assert result.internal_pdf_bytes == b"pdf"
        assert result.external_pdf_bytes == b"pdf"


def test_export_guard_not_armed_when_redaction_toggle_off_end_to_end(monkeypatch):
    """Regression test for: 'Export blocked: redaction incomplete (3 matches)'
    firing even though the sidebar toggle "Input anonymization (redaction)"
    was OFF and no pseudonymization pass ever ran.

    Root cause: app.py passed build_exports(redact_required=...) using only
    runtime_config.redact_required, a static policy default (True unless
    policy.yaml overrides it) that is completely independent of the
    "Input anonymization (redaction)" sidebar toggle (enable_redaction).
    When enable_redaction is False, the model receives and returns the
    original text, so real PII in the export is expected, not a leak - the
    fail-closed guard (validate_mandantenexport) must not run.

    Fix: gate the guard on `enable_redaction and runtime_config.redact_required`
    (see app.py's render_results(..., redact_required=...) call site).

    This test drives the real pipeline end-to-end (extraction -> segmentation
    -> demo-stub analysis -> build_exports) against the real synthetic sample,
    exactly like the reported bug, and asserts the fixed expression does not
    raise while the naive (buggy) expression does.
    """
    monkeypatch.setenv("DOCPIPE_DEMO_MODE", "1")
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)

    from pathlib import Path

    from core.config import reset_config
    from core.extractor import compute_sha256, extract_text_with_meta
    from core.pipeline import _build_coverage_metrics, _parse_dictionary_entities, _prepare_pages_for_llm
    from core.utils import _page_no
    from governance.policy import load_policy
    from services.export_service import build_exports
    from workflows.doc_summary import DocSummaryWorkflow

    reset_config()

    sample = Path(__file__).parent.parent / "samples" / "01_mietvertrag_synthetic.pdf"
    file_bytes = sample.read_bytes()

    loaded_policy = load_policy()
    workflow = DocSummaryWorkflow()

    # Sidebar defaults an operator sees on first load: redaction toggle OFF.
    enable_redaction = False

    runtime_config = workflow.build_runtime_config(
        loaded_policy=loaded_policy,
        profile_override=None,
        auto_classify=True,
        dictionary_entities=_parse_dictionary_entities(""),
        dictionary_fuzzy=False,
        dictionary_threshold=92,
    )
    # Sanity check on the bug's precondition: the static policy default is
    # True regardless of the (independent) UI toggle.
    assert runtime_config.redact_required is True

    doc_hash = compute_sha256(file_bytes)
    extraction = extract_text_with_meta(file_bytes, sample.name)
    pages = extraction.pages

    reverse_mapping: list = []
    context_mapping: dict = {}
    redacted_pages = _prepare_pages_for_llm(pages, reverse_mapping, apply_redaction=enable_redaction)
    segments = workflow.build_segments(redacted_pages, runtime_config=runtime_config)
    source_pages_by_no = {_page_no(p): p for p in pages if _page_no(p)}

    analysis = workflow.run_analysis(
        segments,
        doc_hash=doc_hash,
        runtime_config=runtime_config,
        reverse_mapping=reverse_mapping,
        context_mapping=context_mapping,
        source_pages_by_no=source_pages_by_no,
        redaction_applied=False,
        pseudonymization_applied=False,
        classify_used_chars=0,
        extract_ms=0,
        segment_ms=0,
    )
    segment_results = analysis["result_bundle"].get("segment_results", [])
    audit_entry = analysis["audit_entry"]

    overall_chunks = sum(int(seg.get("coverage", {}).get("chunks_sent", 0)) for seg in segment_results)
    coverage = _build_coverage_metrics(
        pages=pages,
        chunks_sent=overall_chunks,
        citation_status="unverified",
        citation_metrics={},
        citation_check_ran=False,
        citation_verify_mode=runtime_config.citation_verify_mode,
    )

    # Buggy expression (pre-fix app.py): guard fires even though redaction
    # was never enabled -> false positive "Export blocked".
    with pytest.raises(RedactionLeakError):
        build_exports(
            segment_results,
            context_mapping,
            coverage,
            audit_entry,
            "Beides",
            redact_required=runtime_config.redact_required,
        )

    # Fixed expression: guard is not armed when the redaction toggle is off.
    result = build_exports(
        segment_results,
        context_mapping,
        coverage,
        audit_entry,
        "Beides",
        redact_required=enable_redaction and runtime_config.redact_required,
    )
    assert result.external_pdf_bytes
    assert result.internal_pdf_bytes

    reset_config()
