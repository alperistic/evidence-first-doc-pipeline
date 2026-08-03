"""scripts/generate_samples.py – Generator for synthetic sample PDFs.

This script generates ALL sample documents under samples/ from scratch.
It never reads, copies, or references any real documents. Every name,
address, IBAN, and reference number in the generated PDFs comes from an
obviously fictional namespace (Mustermann/Beispiel style, the German
equivalent of "John Doe"/"Jane Sample"), so nobody opening one of these
files could mistake it for a real client or business record.

Purpose: a public portfolio repo needs sample input that exercises the
extraction/segmentation/redaction pipeline without ever containing or
referencing real (confidential) documents.

Sample inputs are generated, never real documents.

Usage:
    python scripts/generate_samples.py
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

from fpdf import FPDF

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = REPO_ROOT / "samples"

# Fixed creation timestamp so re-running the generator produces byte-identical
# PDFs (fpdf2 embeds a /CreationDate in the PDF trailer unless we pin it).
_FIXED_CREATION_DATE = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)

# Redaction-inert banner/watermark wording.
#
# The banner and watermark are stamped as regular PDF text (not vector
# graphics/images -- see the SyntheticSamplePDF docstring for why), so
# whatever we write here also flows through PDF text extraction straight
# into the redaction stage. The original all-caps wording
# ("SYNTHETIC SAMPLE - GENERATED TEST DATA - NOT A REAL DOCUMENT") was
# empirically confirmed to get mangled there: "SAMPLE" matches the
# Title-Case two-word person heuristic (_PERSON_CANDIDATE_RE) and gets
# masked as [PERSON_n]; "DOCUMENT" is an 8-letter all-caps token whose
# characters 5-6 ("ME") happen to be a valid ISO country code, so it
# passes the BIC regex+validator and becomes [BIC_n]. Lowercase prose does
# not trigger either rule (both require capitalised tokens), so the
# wording below is lowercase. It is additionally bracket-wrapped: pypdf's
# extraction joins the header/footer/watermark onto the same text line as
# the surrounding body content (no reliable newline between them), and the
# address regexes (_ADDR_STREET_RE / _ADDR_PLZ_CITY_RE) are greedy across
# runs of word characters -- without a delimiter, a banner placed right
# after a real street/PLZ+city match gets partially absorbed into the
# preceding [ADDR_n] placeholder. A bracket is not in the regexes' allowed
# character classes, so it hard-stops the match and keeps the banner intact.
# All of this was verified empirically against governance/redaction.py,
# not assumed -- see scripts/generate_samples.py usage notes / task report.
SYNTHETIC_BANNER = (
    "[synthetisches beispieldokument -- automatisch erzeugte testdaten, "
    "kein echtes dokument]"
)
WATERMARK_TEXT = "[synthetisches beispiel]"

# Documented German test IBAN (Bundesbank-style example number), used
# consistently across all generated samples.
TEST_IBAN = "DE02120300000000202051"
TEST_EMAIL = "kontakt@example.org"
TEST_PHONE = "+49 30 000000-0"


class SyntheticSamplePDF(FPDF):
    """FPDF subclass that stamps every page with the synthetic-data banner
    and a rotated grey watermark.

    Watermark note: this fpdf2 build (2.8.7) has no public alpha/opacity API
    for text, so true low-opacity rendering is not available. Instead we
    approximate a "light" watermark using large, light-grey (RGB 210,210,210)
    rotated text drawn before the page content — visually similar to a low
    opacity stamp without needing alpha blending.

    Text vs. graphics: fpdf2 2.8.7's `text_mode` (fpdf.enums.TextMode) only
    controls the PDF /Tr (text rendering mode) operator -- fill/stroke/clip
    variants, including INVISIBLE. This is a rendering hint for PDF viewers,
    not an extraction switch: text drawn with TextMode.INVISIBLE is still
    present as real Tj/TJ text operators and is extracted by pypdf exactly
    like normal text (empirically confirmed). fpdf2 also has no public API
    to convert a string to glyph outlines/vector paths (font_type_3.py uses
    glyph paths internally for embedded Type3 fonts, not as a general
    "draw this text as a shape" primitive). So drawing the banner as
    non-extractable vector graphics is not something fpdf2 2.8.7 supports
    cleanly; doing it properly would mean rasterizing the banner as an
    image (extra Pillow dependency, heavier for a demo repo) or hand-rolling
    glyph-to-path conversion. Given that, the banner stays as regular text,
    with wording chosen (see SYNTHETIC_BANNER/WATERMARK_TEXT above) so it
    survives the redaction stage unmangled instead.
    """

    def header(self) -> None:
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(150, 0, 0)
        self.set_xy(10, 8)
        self.cell(0, 6, SYNTHETIC_BANNER, align="C")
        self.set_text_color(0, 0, 0)
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 6, SYNTHETIC_BANNER, align="C")
        self.set_text_color(0, 0, 0)

    def draw_watermark(self) -> None:
        self.set_font("Helvetica", "B", 48)
        self.set_text_color(210, 210, 210)
        page_w = self.w
        page_h = self.h
        with self.rotation(45, x=page_w / 2, y=page_h / 2):
            self.set_xy(page_w / 2 - 90, page_h / 2 - 10)
            self.cell(180, 20, WATERMARK_TEXT, align="C")
        self.set_text_color(0, 0, 0)

    def add_page(self, *args, **kwargs):  # type: ignore[override]
        super().add_page(*args, **kwargs)
        self.draw_watermark()
        self.set_xy(10, 22)


def _write_heading(pdf: FPDF, text: str, size: int = 14) -> None:
    pdf.set_font("Helvetica", "B", size)
    pdf.multi_cell(0, 8, text)
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 11)


def _write_subheading(pdf: FPDF, text: str) -> None:
    pdf.set_font("Helvetica", "B", 12)
    pdf.multi_cell(0, 7, text)
    pdf.ln(1)
    pdf.set_font("Helvetica", "", 11)


def _write_para(pdf: FPDF, text: str) -> None:
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, text)
    pdf.ln(2)


def build_mietvertrag() -> SyntheticSamplePDF:
    """Document 1: synthetic tenancy agreement (~2 pages)."""
    pdf = SyntheticSamplePDF(format="A4")
    pdf.set_creation_date(_FIXED_CREATION_DATE)
    pdf.set_creator("evidence-first-doc-pipeline sample generator")
    pdf.set_title("Mietvertrag (synthetisch)")
    pdf.set_author("Synthetic Sample Generator")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    _write_heading(pdf, "Wohnraum-Mietvertrag")
    _write_para(
        pdf,
        "Aktenzeichen: MV-2026-0001-SYN",
    )

    _write_subheading(pdf, "Zwischen den Parteien")
    _write_para(
        pdf,
        "Vermieter: Max Beispiel, Musterstraße 1, 12345 Musterstadt "
        f"(E-Mail: {TEST_EMAIL}, Telefon: {TEST_PHONE})",
    )
    _write_para(
        pdf,
        "Mieter: Erika Mustermann, Beispielweg 2, 54321 Beispielhausen",
    )
    _write_para(
        pdf,
        "- nachfolgend gemeinsam die Vertragsparteien genannt - "
        "wird folgender Mietvertrag über Wohnraum geschlossen.",
    )

    _write_subheading(pdf, "Paragraph 1 Mietgegenstand")
    _write_para(
        pdf,
        "Der Vermieter vermietet dem Mieter die Wohnung im Anwesen "
        "Musterstraße 1, 12345 Musterstadt, bestehend aus 3 Zimmern, "
        "Küche, Bad und einem Kellerraum, zu Wohnzwecken.",
    )

    _write_subheading(pdf, "Paragraph 2 Mietbeginn und Mietzeit")
    _write_para(
        pdf,
        "Das Mietverhältnis beginnt am 01.03.2026 (Mietbeginn). Es wird "
        "auf unbestimmte Zeit geschlossen. Die Kündigungsfrist beträgt "
        "drei Monate. Eine ordentliche Kündigung durch den Mieter ist "
        "erstmals zum 31.05.2026 möglich; die Kündigungserklärung muss "
        "dem Vermieter bis spätestens 28.02.2026 zugehen.",
    )

    _write_subheading(pdf, "Paragraph 3 Miete und Nebenkosten")
    _write_para(
        pdf,
        "Die Monatsmiete (Kaltmiete) beträgt EUR 850,00 und ist "
        "monatlich im Voraus, spätestens bis zum dritten Werktag eines "
        "jeden Monats, auf das nachfolgend genannte Konto des Vermieters "
        "zu zahlen.",
    )
    _write_para(
        pdf,
        "Zusätzlich zur Monatsmiete zahlt der Mieter eine monatliche "
        "Nebenkostenvorauszahlung in Höhe von EUR 180,00 für Heizung, "
        "Wasser, Müllabfuhr und sonstige umlagefähige Betriebskosten. "
        "Über die Nebenkosten wird jährlich abgerechnet.",
    )
    _write_para(
        pdf,
        f"Zahlungen erfolgen auf das Konto IBAN {TEST_IBAN}, "
        "Musterbank Beispielstadt.",
    )

    pdf.add_page()

    _write_subheading(pdf, "Paragraph 4 Kaution")
    _write_para(
        pdf,
        "Der Mieter leistet eine Mietkaution in Höhe von EUR 2.550,00 "
        "(entspricht drei Monatskaltmieten). Die Kaution ist bei "
        "Mietbeginn in einer Summe auf das og. Konto zu überweisen und "
        "wird vom Vermieter getrennt von seinem Vermögen angelegt.",
    )

    _write_subheading(pdf, "Paragraph 5 Instandhaltung")
    _write_para(
        pdf,
        "Der Mieter ist verpflichtet, die Mietsache pfleglich zu "
        "behandeln und Schäden, die über die vertragsgemäße Abnutzung "
        "hinausgehen, unverzüglich anzuzeigen. Kleinreparaturen bis EUR "
        "100,00 im Einzelfall trägt der Mieter.",
    )

    _write_subheading(pdf, "Paragraph 6 Hausordnung und Sonstiges")
    _write_para(
        pdf,
        "Die als Anlage beigefügte Hausordnung ist Bestandteil dieses "
        "Vertrages. Nebenabreden bestehen nicht. Änderungen und "
        "Ergänzungen bedürfen der Schriftform.",
    )

    _write_para(
        pdf,
        "Musterstadt, den 15.01.2026",
    )
    pdf.ln(10)
    _write_para(pdf, "Musterfirma GmbH i.V. Max Beispiel (Vermieter)")
    _write_para(pdf, "Erika Mustermann (Mieterin)")

    return pdf


def build_mahnschreiben() -> SyntheticSamplePDF:
    """Document 2: synthetic payment reminder / dunning letter (1 page)."""
    pdf = SyntheticSamplePDF(format="A4")
    pdf.set_creation_date(_FIXED_CREATION_DATE)
    pdf.set_creator("evidence-first-doc-pipeline sample generator")
    pdf.set_title("Mahnschreiben (synthetisch)")
    pdf.set_author("Synthetic Sample Generator")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    _write_para(
        pdf,
        "Beispiel & Partner GbR\n"
        "Musterstraße 1, 12345 Musterstadt\n"
        f"E-Mail: {TEST_EMAIL} | Telefon: {TEST_PHONE}",
    )
    pdf.ln(4)
    _write_para(
        pdf,
        "An:\nMax Beispiel\nBeispielweg 2\n54321 Beispielhausen",
    )
    pdf.ln(2)
    _write_para(pdf, "Musterstadt, den 20.01.2026")

    _write_heading(pdf, "Mahnung wegen offener Forderung", size=13)
    _write_para(pdf, "Aktenzeichen: MS-2026-0042-SYN")

    _write_para(
        pdf,
        "Sehr geehrter Herr Beispiel,",
    )
    _write_para(
        pdf,
        "trotz unserer Zahlungsaufforderung vom 05.01.2026 konnten wir bis "
        "heute keinen Zahlungseingang feststellen. Aus der Rechnung Nr. "
        "RG-2025-9981 besteht folgender Rückstand (offene Forderung):",
    )
    _write_para(pdf, "Hauptforderung: EUR 1.240,00")
    _write_para(pdf, "Verzugszinsen: EUR 36,50")
    _write_para(pdf, "Mahngebühr (Verzugsschaden): EUR 15,00")
    _write_para(pdf, "Gesamtbetrag: EUR 1.291,50")

    _write_para(
        pdf,
        "Wir fordern Sie hiermit letztmalig auf, den Gesamtbetrag von "
        "EUR 1.291,50 bis spätestens 05.02.2026 auf unten stehendes "
        "Konto zu überweisen. Sollte der Betrag nicht fristgerecht bei "
        "uns eingehen, behalten wir uns gerichtliche Schritte ohne "
        "weitere Ankündigung vor.",
    )
    _write_para(
        pdf,
        f"Bankverbindung: IBAN {TEST_IBAN}, Musterbank Beispielstadt, "
        "Verwendungszweck: MS-2026-0042-SYN",
    )

    _write_para(
        pdf,
        "Mit freundlichen Grüßen",
    )
    pdf.ln(8)
    _write_para(pdf, "i.A. Erika Mustermann\nBeispiel & Partner GbR")

    return pdf


def build_klageschrift() -> SyntheticSamplePDF:
    """Document 3: synthetic Klageschrift (court filing) with a Rubrum
    header (Klaeger:/Beklagter:/vertreten durch:), needed to exercise the
    rubrum-override code path (core/rubrum.py), which specifically looks
    for that Rubrum marker structure and is otherwise not triggered by the
    tenancy agreement or the dunning letter."""
    pdf = SyntheticSamplePDF(format="A4")
    pdf.set_creation_date(_FIXED_CREATION_DATE)
    pdf.set_creator("evidence-first-doc-pipeline sample generator")
    pdf.set_title("Klageschrift (synthetisch)")
    pdf.set_author("Synthetic Sample Generator")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    _write_para(
        pdf,
        "An das\nAmtsgericht Musterstadt\nMusterstraße 10\n12345 Musterstadt",
    )
    pdf.ln(2)
    _write_heading(pdf, "Klageschrift", size=14)
    _write_para(pdf, "Aktenzeichen: KS-2026-0007-SYN")

    _write_para(
        pdf,
        "Klägerin: Erika Mustermann, Musterstraße 1, 12345 Musterstadt.\n"
        "vertreten durch: Rechtsanwältin Dr. Anna Beispiel, "
        "Beispielkanzlei Beispiel & Partner GbR, Musterweg 3, "
        "54321 Beispielhausen.",
    )
    _write_para(
        pdf,
        "Beklagter: Max Beispiel, Beispielweg 2, 54321 Beispielhausen.",
    )

    _write_para(
        pdf,
        "wegen: Zahlungsklage aus Mietverhältnis\n"
        "Der Streitwert wird auf EUR 1.291,50 festgesetzt.",
    )

    _write_subheading(pdf, "I. Anträge")
    _write_para(
        pdf,
        "Die Klägerin beantragt (Klageantrag), den Beklagten zu "
        "verurteilen,\n"
        "1. an die Klägerin EUR 1.291,50 nebst Zinsen in Höhe von 5 "
        "Prozentpunkten über dem Basiszinssatz seit dem 06.02.2026 zu "
        "zahlen,\n"
        "2. dem Beklagten die Kosten des Rechtsstreits aufzuerlegen.\n"
        "Der Klageanspruch ergibt sich aus dem nachfolgend geschilderten "
        "Sachverhalt.",
    )

    pdf.add_page()

    _write_subheading(pdf, "II. Sachverhalt")
    _write_para(
        pdf,
        "Zwischen den Parteien besteht ein Mietverhältnis über die "
        "Wohnung Musterstraße 1, 12345 Musterstadt (Aktenzeichen "
        "MV-2026-0001-SYN). Der Beklagte ist mit Zahlungen aus diesem "
        "Mietverhältnis in Rückstand geraten. Trotz Mahnung vom "
        "20.01.2026 (Aktenzeichen MS-2026-0042-SYN) mit Zahlungsfrist "
        "zum 05.02.2026 erfolgte keine Zahlung.",
    )

    _write_subheading(pdf, "III. Rechtliche Würdigung")
    _write_para(
        pdf,
        "Der Anspruch der Klägerin ergibt sich aus dem Mietvertrag in "
        "Verbindung mit den gesetzlichen Vorschriften zum Zahlungsverzug. "
        "Der Beklagte befindet sich seit Ablauf der in der Mahnung "
        "gesetzten Frist in Verzug.",
    )

    _write_subheading(pdf, "IV. Beweismittel")
    _write_para(
        pdf,
        "Als Beweismittel werden benannt: Mietvertrag MV-2026-0001-SYN, "
        "Mahnschreiben MS-2026-0042-SYN, Kontoauszug der Klägerin.",
    )

    _write_para(pdf, "Musterstadt, den 10.02.2026")
    pdf.ln(6)
    _write_para(pdf, "Dr. Anna Beispiel\nRechtsanwältin")

    return pdf


def main() -> None:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    targets = [
        ("01_mietvertrag_synthetic.pdf", build_mietvertrag),
        ("02_mahnschreiben_synthetic.pdf", build_mahnschreiben),
        ("03_klageschrift_synthetic.pdf", build_klageschrift),
    ]

    for filename, builder in targets:
        pdf = builder()
        out_path = SAMPLES_DIR / filename
        pdf.output(str(out_path))
        size = out_path.stat().st_size
        print(f"wrote {out_path.relative_to(REPO_ROOT)} ({size} bytes)")


if __name__ == "__main__":
    sys.exit(main())
