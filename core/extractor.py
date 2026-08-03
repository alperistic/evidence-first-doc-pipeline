"""
extractor.py – PDF/DOCX Text-Extraktion
Kein Dokument wird persistiert; gibt nur den extrahierten Text zurück.
"""

import hashlib
import io
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.config import get_config

try:
    from pypdf import PdfReader
except ModuleNotFoundError:  # pragma: no cover - optional dependency in tests
    PdfReader = None  # type: ignore[assignment]
try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - optional dependency in tests
    requests = None  # type: ignore[assignment]
try:
    from docx import Document
except ModuleNotFoundError:  # pragma: no cover - optional dependency in tests
    Document = None  # type: ignore[assignment]


@dataclass
class ExtractionResult:
    text: str
    ocr_used: bool
    extraction_method: str  # "pdf_text" | "pdf_ocr" | "docx_text"
    pages: list[dict[str, Any]]
    pages_total: int
    pages_extracted: int
    chars_total: int


def normalize_extracted_text(text: str) -> str:
    """Normalisiert extrahierten Text für stabile Weiterverarbeitung/Export."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    # Platzhalter-Zeilenbruch: [PERSO\nN_C] -> [PERSON_C]
    normalized = re.sub(r"\[(\w+)\n(\w+)\]", r"[\1\2]", normalized)
    # Silbentrennung am Zeilenende entfernen: Bewe-\nführung -> Beweführung
    normalized = re.sub(r"(\w)-\n(\w)", r"\1\2", normalized)

    paragraphs = re.split(r"\n{2,}", normalized)
    cleaned_paragraphs: list[str] = []
    for para in paragraphs:
        part = para.strip()
        if not part:
            continue
        # Einzelne Zeilenumbrüche innerhalb eines Absatzes entfernen
        part = re.sub(r"(\w)\n(\w)", r"\1 \2", part)
        part = part.replace("\n", " ")
        part = re.sub(r"[ \t]{2,}", " ", part).strip()
        cleaned_paragraphs.append(part)

    out = "\n\n".join(cleaned_paragraphs).strip()
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out


def _normalize_pages_text(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_pages: list[dict[str, Any]] = []
    for page in pages:
        normalized_page = dict(page)
        normalized_page["text"] = normalize_extracted_text(str(page.get("text", "") or ""))
        normalized_pages.append(normalized_page)
    return normalized_pages


def extract_text(file_bytes: bytes, filename: str) -> str:
    """Extrahiert Plaintext aus PDF oder DOCX (in-memory, kein Speichern)."""
    return extract_text_with_meta(file_bytes, filename).text


def extract_text_with_meta(file_bytes: bytes, filename: str) -> ExtractionResult:
    """Extrahiert Plaintext inkl. Metadaten zur verwendeten Methode."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        direct_pages = _normalize_pages_text(_extract_pdf_pages(file_bytes))
        direct_text = normalize_extracted_text("\n\n".join(p["text"] for p in direct_pages).strip())
        if _is_text_sufficient(direct_text):
            pages_total = len(direct_pages)
            pages_extracted = sum(1 for p in direct_pages if (p.get("text") or "").strip())
            return ExtractionResult(
                text=direct_text,
                ocr_used=False,
                extraction_method="pdf_text",
                pages=direct_pages,
                pages_total=pages_total,
                pages_extracted=pages_extracted,
                chars_total=len(direct_text),
            )

        # OCR-Fallback nur für gescannte/kaum extrahierbare PDFs
        ocr_pages = _normalize_pages_text(_extract_pdf_via_azure_read_pages(file_bytes))
        ocr_text = normalize_extracted_text("\n\n".join(p["text"] for p in ocr_pages).strip())
        if ocr_text:
            pages_total = len(ocr_pages) or len(direct_pages)
            pages_extracted = sum(1 for p in ocr_pages if (p.get("text") or "").strip())
            return ExtractionResult(
                text=ocr_text,
                ocr_used=True,
                extraction_method="pdf_ocr",
                pages=ocr_pages,
                pages_total=pages_total,
                pages_extracted=pages_extracted,
                chars_total=len(ocr_text),
            )
        pages_total = len(direct_pages)
        pages_extracted = sum(1 for p in direct_pages if (p.get("text") or "").strip())
        return ExtractionResult(
            text=direct_text,
            ocr_used=False,
            extraction_method="pdf_text",
            pages=direct_pages,
            pages_total=pages_total,
            pages_extracted=pages_extracted,
            chars_total=len(direct_text),
        )
    elif suffix in (".docx", ".doc"):
        docx_text = normalize_extracted_text(_extract_docx(file_bytes))
        pages = [{"page": 1, "page_number": 1, "text": docx_text}]
        return ExtractionResult(
            text=docx_text,
            ocr_used=False,
            extraction_method="docx_text",
            pages=pages,
            pages_total=1,
            pages_extracted=1 if docx_text.strip() else 0,
            chars_total=len(docx_text),
        )
    else:
        raise ValueError(f"Nicht unterstütztes Dateiformat: {suffix}")


def _extract_pdf(file_bytes: bytes) -> str:
    return normalize_extracted_text(
        "\n\n".join(p["text"] for p in _normalize_pages_text(_extract_pdf_pages(file_bytes))).strip()
    )


def _extract_pdf_pages(file_bytes: bytes) -> list[dict[str, Any]]:
    if PdfReader is None:
        raise ModuleNotFoundError("pypdf ist nicht installiert; PDF-Extraktion nicht verfügbar.")
    reader = PdfReader(io.BytesIO(file_bytes))
    return [
        {
            "page": idx + 1,
            "page_number": idx + 1,
            "text": (page.extract_text() or "").strip(),
        }
        for idx, page in enumerate(reader.pages)
    ]


def _extract_docx(file_bytes: bytes) -> str:
    if Document is None:
        raise ModuleNotFoundError("python-docx ist nicht installiert; DOCX-Extraktion nicht verfügbar.")
    doc = Document(io.BytesIO(file_bytes))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs).strip()


def _is_text_sufficient(text: str) -> bool:
    """Heuristik: hat die PDF genug extrahierbaren Text, damit OCR nicht nötig ist?"""
    min_chars = get_config().ocr_min_pdf_text_chars
    alnum_chars = sum(ch.isalnum() for ch in text)
    return alnum_chars >= min_chars


def _extract_pdf_via_azure_read(file_bytes: bytes) -> str:
    return normalize_extracted_text(
        "\n\n".join(
            p["text"] for p in _normalize_pages_text(_extract_pdf_via_azure_read_pages(file_bytes))
        ).strip()
    )


def _extract_pdf_via_azure_read_pages(file_bytes: bytes) -> list[dict[str, Any]]:
    """
    OCR-Fallback via Azure AI Document Intelligence (prebuilt-read).
    Kein Dokument wird lokal gespeichert; Upload erfolgt direkt aus dem Speicher.
    """
    cfg = get_config()
    endpoint = cfg.azure_doc_intel_endpoint.rstrip("/")
    api_key = cfg.azure_doc_intel_key
    api_version = cfg.azure_doc_intel_api_version

    if not endpoint or not api_key:
        return []
    if requests is None:
        raise ModuleNotFoundError("requests ist nicht installiert; OCR-Fallback nicht verfügbar.")

    analyze_url = (
        f"{endpoint}/documentintelligence/documentModels/prebuilt-read:analyze"
        f"?api-version={api_version}"
    )
    headers = {
        "Ocp-Apim-Subscription-Key": api_key,
        "Content-Type": "application/pdf",
    }

    try:
        submit = requests.post(analyze_url, headers=headers, data=file_bytes, timeout=30)
        submit.raise_for_status()
        operation_url = submit.headers.get("Operation-Location")
        if not operation_url:
            return []

        max_polls = cfg.ocr_max_polls
        poll_sleep_sec = cfg.ocr_poll_sleep_sec

        for _ in range(max_polls):
            poll = requests.get(
                operation_url,
                headers={"Ocp-Apim-Subscription-Key": api_key},
                timeout=30,
            )
            poll.raise_for_status()
            data = poll.json()
            status = (data.get("status") or "").lower()

            if status == "succeeded":
                analyze_result = data.get("analyzeResult", {})
                pages: list[dict[str, Any]] = []
                for page in analyze_result.get("pages", []):
                    lines: list[str] = []
                    for line in page.get("lines", []):
                        line_text = (line.get("content") or "").strip()
                        if line_text:
                            lines.append(line_text)
                    pages.append(
                        {
                            "page": int(page.get("pageNumber", len(pages) + 1)),
                            "page_number": int(page.get("pageNumber", len(pages) + 1)),
                            "text": "\n".join(lines).strip(),
                        }
                    )
                return pages

            if status in {"failed", "cancelled"}:
                return []

            time.sleep(poll_sleep_sec)

        return []
    except Exception:
        return []


def compute_sha256(file_bytes: bytes) -> str:
    """SHA256-Hash der Rohdaten – einzige persistierte Referenz auf das Dokument."""
    return hashlib.sha256(file_bytes).hexdigest()
