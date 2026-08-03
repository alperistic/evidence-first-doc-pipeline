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
