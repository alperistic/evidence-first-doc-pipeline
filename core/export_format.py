"""
core/export_format.py – Format-specific export: PDF (fpdf2) and DOCX (python-docx).

Contains: to_pdf_bytes(), to_docx_bytes(), PDF constants.
Text rendering lives in core/export_render.py.
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone

try:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt, RGBColor
except ModuleNotFoundError:  # pragma: no cover - optional dependency in tests
    Document = None  # type: ignore[assignment]
    WD_ALIGN_PARAGRAPH = None  # type: ignore[assignment]
    Inches = Pt = RGBColor = None  # type: ignore[assignment]

try:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos
except ModuleNotFoundError:  # pragma: no cover - optional dependency in tests
    FPDF = None  # type: ignore[assignment]
    XPos = YPos = None  # type: ignore[assignment]

from core.export_render import (
    _format_token_count,
    _normalize_display_text,
    _normalize_pdf_missing_line,
    _parse_lines,
)

# Usable width in the export PDF: pdf.w - l_margin - r_margin
_PDF_L = 20
_PDF_R = 20
_PDF_BULLET_INDENT = 4
_PDF_SOURCE_INDENT = 8
_PDF_TOP = 20
_PDF_BOTTOM = 20

_VERSION = "v0.1 Demo"
_DISCLAIMER = (
    "AI-generated suggestion - expert review required. Not professional advice."
)
_FOOTER_NOTE = (
    "No full document text was stored. Processing follows the configured "
    "policy; expert review required. Document Analysis Pipeline " + _VERSION
)


# ---------------------------------------------------------------------------
# Shared helper functions
# ---------------------------------------------------------------------------

def _latin1(text: str) -> str:
    """
    Converts text for fpdf2 core fonts (Latin-1).
    Known Unicode special characters are replaced with ASCII equivalents.
    """
    _MAP = {
        "\u2013": "-",   # en dash
        "\u2014": "-",   # em dash
        "\u2010": "-",   # hyphen
        "\u2018": "'",   # left single quotation mark
        "\u2019": "'",   # right single quotation mark
        "\u201a": "'",   # single low-9 quotation mark
        "\u201c": '"',   # left double quotation mark
        "\u201d": '"',   # right double quotation mark
        "\u201e": '"',   # German low double quotation mark
        "\u00ab": '"',   # left guillemet
        "\u00bb": '"',   # right guillemet
        "\u2022": "*",   # bullet
        "\u2026": "...", # ellipsis
        "\u00b7": "*",   # middle dot
        "\u20ac": "EUR", # euro sign, core-font fallback
    }
    for ch, rep in _MAP.items():
        text = text.replace(ch, rep)
    return text.encode("cp1252", errors="replace").decode("cp1252")


# ---------------------------------------------------------------------------
# PDF export (fpdf2)
# ---------------------------------------------------------------------------

def to_pdf_bytes(summary: str, audit: dict, title: str = "Document Analysis Pipeline") -> bytes:
    """Renders the summary as a PDF and returns it as bytes."""
    if FPDF is None or XPos is None or YPos is None:
        raise ModuleNotFoundError("fpdf is not installed; PDF export unavailable.")

    pdf = FPDF()
    pdf.set_margins(_PDF_L, _PDF_TOP, _PDF_R)
    pdf.set_auto_page_break(auto=True, margin=_PDF_BOTTOM)
    pdf.add_page()
    usable_width = pdf.w - pdf.l_margin - pdf.r_margin

    # --- Header banner ---
    pdf.set_fill_color(28, 57, 112)
    pdf.rect(pdf.l_margin, 20, usable_width, 20, style="F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_xy(pdf.l_margin + 4, 22)
    pdf.cell(usable_width - 8, 8, _latin1(title),
             new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(pdf.l_margin + 4, 31)
    pdf.cell(usable_width - 4, 7,
             _latin1(datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_text_color(0, 0, 0)
    pdf.ln(6)

    # --- Disclaimer (top) ---
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(140, 100, 0)
    pdf.multi_cell(
        usable_width,
        5,
        _latin1(_DISCLAIMER),
        align="C",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + usable_width, pdf.get_y())
    pdf.ln(6)

    # --- Summary ---
    for line_type, text in _parse_lines(summary):
        clean = _latin1(_normalize_pdf_missing_line(text))
        if line_type == "empty":
            pdf.ln(3)
        elif line_type == "header":
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(28, 57, 112)
            pdf.multi_cell(
                usable_width,
                7,
                clean,
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            pdf.set_text_color(0, 0, 0)
            pdf.ln(1)
        elif line_type == "source":
            pdf.set_font("Helvetica", "", 9)
            pdf.set_x(pdf.l_margin + _PDF_SOURCE_INDENT)
            pdf.multi_cell(
                usable_width - _PDF_SOURCE_INDENT,
                5.5,
                clean,
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
        elif line_type == "bullet":
            pdf.set_font("Helvetica", "", 10)
            bullet_body = re.sub(r"^\s*(?:[-–•])\s*", "", clean, count=1)
            bullet_prefix = "- "
            prefix_w = pdf.get_string_width(bullet_prefix) + 1.0
            pdf.set_x(pdf.l_margin + _PDF_BULLET_INDENT)
            pdf.cell(prefix_w, 6, bullet_prefix, new_x=XPos.RIGHT, new_y=YPos.TOP)
            pdf.multi_cell(
                usable_width - _PDF_BULLET_INDENT - prefix_w,
                6,
                bullet_body,
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
        else:
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(
                usable_width,
                6,
                clean,
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )

    # --- Divider before audit block ---
    pdf.ln(6)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + usable_width, pdf.get_y())
    pdf.ln(4)

    # --- Audit block ---
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(28, 57, 112)
    pdf.cell(usable_width, 6, "Audit block",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(60, 60, 60)
    pdf.ln(2)

    audit_rows = [
        ("Timestamp", audit.get("timestamp", "-")),
        ("Document hash (SHA-256)", audit.get("doc_hash", "-")),
        ("Model", audit.get("model", "-")),
        ("Token count", _format_token_count(audit)),
        ("Document class", audit.get("doc_class", "-")),
        ("Pipeline version", _VERSION),
    ]
    for label, value in audit_rows:
        label_text = _latin1(label + ":")
        key_w = min(max(pdf.get_string_width(label_text) + 2.0, 26.0), usable_width * 0.45)
        y = pdf.get_y()
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_xy(pdf.l_margin, y)
        pdf.cell(key_w, 6, label_text,
                 new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_xy(pdf.l_margin + key_w, y)
        pdf.multi_cell(
            usable_width - key_w,
            6,
            _latin1(_normalize_display_text(str(value))),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

    # --- Footer ---
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(130, 130, 130)
    pdf.multi_cell(
        usable_width,
        5,
        _latin1(_FOOTER_NOTE),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )

    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# DOCX export (python-docx)
# ---------------------------------------------------------------------------

def to_docx_bytes(summary: str, audit: dict, title_text: str = "Document Analysis Pipeline") -> bytes:
    """Renders the summary as a DOCX and returns it as bytes."""
    if Document is None or WD_ALIGN_PARAGRAPH is None or Inches is None or Pt is None or RGBColor is None:
        raise ModuleNotFoundError("python-docx is not installed; DOCX export unavailable.")

    doc = Document()

    # Page margins
    for section in doc.sections:
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin = Inches(1.2)
        section.right_margin = Inches(1.2)

    # --- Header ---
    title = doc.add_heading(title_text, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    for run in title.runs:
        run.font.color.rgb = RGBColor(28, 57, 112)

    date_para = doc.add_paragraph(
        datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    )
    date_run = date_para.runs[0]
    date_run.font.size = Pt(9)
    date_run.font.color.rgb = RGBColor(100, 100, 100)

    # --- Disclaimer ---
    disc = doc.add_paragraph()
    disc_run = disc.add_run(_DISCLAIMER)
    disc_run.font.italic = True
    disc_run.font.size = Pt(9)
    disc_run.font.color.rgb = RGBColor(140, 100, 0)

    doc.add_paragraph()  # blank line

    # --- Summary ---
    for line_type, text in _parse_lines(summary):
        normalized = _normalize_display_text(text)
        if line_type == "empty":
            doc.add_paragraph()
        elif line_type == "header":
            h = doc.add_heading(normalized, level=2)
            for run in h.runs:
                run.font.color.rgb = RGBColor(28, 57, 112)
        elif line_type == "bullet":
            p = doc.add_paragraph(normalized, style="List Bullet")
            if p.runs:
                p.runs[0].font.size = Pt(10)
        elif line_type == "source":
            p = doc.add_paragraph(normalized)
            p.paragraph_format.left_indent = Inches(0.3)
            if p.runs:
                p.runs[0].font.size = Pt(10)
        else:
            p = doc.add_paragraph(normalized)
            if p.runs:
                p.runs[0].font.size = Pt(10)

    doc.add_paragraph()

    # --- Audit block ---
    audit_heading = doc.add_heading("Audit block", level=2)
    for run in audit_heading.runs:
        run.font.color.rgb = RGBColor(28, 57, 112)

    audit_rows = [
        ("Timestamp", audit.get("timestamp", "-")),
        ("Document hash (SHA-256)", audit.get("doc_hash", "-")),
        ("Model", audit.get("model", "-")),
        ("Token count", _format_token_count(audit)),
        ("Document class", audit.get("doc_class", "-")),
        ("Pipeline version", _VERSION),
    ]

    table = doc.add_table(rows=len(audit_rows), cols=2)
    table.style = "Table Grid"
    for i, (label, value) in enumerate(audit_rows):
        row = table.rows[i]
        row.cells[0].text = label
        row.cells[0].paragraphs[0].runs[0].font.bold = True
        row.cells[0].paragraphs[0].runs[0].font.size = Pt(9)
        row.cells[1].text = value
        row.cells[1].paragraphs[0].runs[0].font.size = Pt(9)

    doc.add_paragraph()

    # --- Footer ---
    footer = doc.add_paragraph()
    footer_run = footer.add_run(_FOOTER_NOTE)
    footer_run.font.italic = True
    footer_run.font.size = Pt(8)
    footer_run.font.color.rgb = RGBColor(130, 130, 130)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.getvalue()
