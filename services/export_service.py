"""services/export_service.py – Export-Generierung (PDF/DOCX).

Enthält keine Streamlit-Abhängigkeiten. Gibt reine Daten zurück.
Wirft RedactionLeakError wenn Mandantenexport undicht ist.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.export_format import to_docx_bytes, to_pdf_bytes
from core.logging_config import get_logger

_log = get_logger(__name__)
from core.export_render import (
    apply_output_mode,
    build_export_content,
    render_segmented_aktenvermerk_text,
    render_segmented_summary_text,
)
from core.pipeline import (
    _build_redacted_export_payload,
    _find_internal_export_placeholder_hits,
)
from governance.redaction import validate_mandantenexport  # raises RedactionLeakError


@dataclass
class ExportBundle:
    """Enthält alle generierten Export-Daten (PDF/DOCX, intern/extern)."""

    internal_pdf_bytes: bytes
    external_pdf_bytes: bytes
    internal_docx_bytes: bytes
    external_docx_bytes: bytes
    internal_placeholder_hits: list[str] = field(default_factory=list)


def build_exports(
    segment_results: list[dict],
    context_mapping: dict[str, str],
    coverage: dict,
    audit_entry: dict,
    export_selection: str,
    *,
    redact_required: bool = True,
) -> ExportBundle:
    """Erzeugt alle Export-Formate (PDF/DOCX, intern/extern).

    Args:
        redact_required: Wenn False, wurde kein Redact-Durchlauf ausgeführt.
            In diesem Fall wird validate_mandantenexport() übersprungen, da
            echte PII im Text erwartet wird und kein Leak vorliegt.

    Raises:
        RedactionLeakError: Wenn redact_required=True und der Mandantenexport
            noch PII enthält.
    """
    redacted_export_segments = _build_redacted_export_payload(segment_results)
    internal_segments = apply_output_mode(
        redacted_export_segments,
        context_mapping,
        mode="internal",
        restore_evidence=True,
    )
    external_segments = apply_output_mode(
        redacted_export_segments,
        context_mapping,
        mode="external",
        restore_evidence=False,
        strict_external_mask=True,
    )

    internal_summary_text = render_segmented_summary_text(internal_segments, coverage=coverage)
    internal_akten_text = render_segmented_aktenvermerk_text(internal_segments, coverage=coverage)
    external_summary_text = render_segmented_summary_text(external_segments, coverage=coverage, mode="external")
    external_akten_text = render_segmented_aktenvermerk_text(external_segments, coverage=coverage, mode="external")

    internal_export_content = build_export_content(
        {
            "summary": internal_summary_text,
            "aktennotiz": internal_akten_text,
            "evidence": internal_segments,
        },
        export_selection,
        mode="internal",
    )
    external_export_content = build_export_content(
        {
            "summary": external_summary_text,
            "aktennotiz": external_akten_text,
            "evidence": external_segments,
        },
        export_selection,
        mode="external",
    )

    internal_placeholder_hits = _find_internal_export_placeholder_hits(internal_export_content)
    internal_export_text = "\n\n".join(
        part
        for part in [
            str(internal_export_content.get("summary", "") or ""),
            str(internal_export_content.get("aktennotiz", "") or ""),
        ]
        if part.strip()
    )
    external_export_text = "\n\n".join(
        part
        for part in [
            str(external_export_content.get("summary", "") or ""),
            str(external_export_content.get("aktennotiz", "") or ""),
        ]
        if part.strip()
    )

    # Mandantenexport-Validierung – nur wenn Redact-Durchlauf aktiv war.
    # Bei redact_required=False lief keine Pseudonymisierung → echte PII im
    # Export ist erwartet, kein Leak im Sinne des Guards.
    if redact_required:
        validate_mandantenexport(external_export_text)

    _log.info(
        "export_build_start",
        stage="export_service",
        export_selection=export_selection,
        internal_placeholder_hits=len(internal_placeholder_hits),
        segment_count=len(segment_results),
    )

    internal_pdf_bytes = to_pdf_bytes(
        internal_export_text,
        audit_entry,
        title="LATE - Kanzleiakte (intern, Klartext)",
    )
    external_pdf_bytes = to_pdf_bytes(
        external_export_text,
        audit_entry,
        title="LATE - Mandantenfassung (redigiert)",
    )
    internal_docx_bytes = to_docx_bytes(
        internal_export_text,
        audit_entry,
        title_text="LATE – Kanzleiakte (intern, Klartext)",
    )
    external_docx_bytes = to_docx_bytes(
        external_export_text,
        audit_entry,
        title_text="LATE – Mandantenfassung (redigiert)",
    )

    _log.info(
        "export_build_done",
        stage="export_service",
        internal_pdf_bytes=len(internal_pdf_bytes),
        external_pdf_bytes=len(external_pdf_bytes),
    )
    return ExportBundle(
        internal_pdf_bytes=internal_pdf_bytes,
        external_pdf_bytes=external_pdf_bytes,
        internal_docx_bytes=internal_docx_bytes,
        external_docx_bytes=external_docx_bytes,
        internal_placeholder_hits=list(internal_placeholder_hits),
    )
