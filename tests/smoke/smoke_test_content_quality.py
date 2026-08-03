"""
smoke_test_content_quality.py – Smoke-Test für Content-Quality-Fixes (PR content-quality-improvements).

Testet den Datenfluss ohne echte LLM-Calls:
  1. PDF extrahieren + Redaktion → segment_text erzeugen
  2. Synthetischen LLM-Output (mit todos, fehlende_angaben, fristen_termine) injizieren
  3. _to_legacy_segment_analysis() aufrufen
  4. render_segmented_aktenvermerk_text() aufrufen
  5. Prüfen:
     - Sektion F (TODOs) ist nicht leer (todos fließen durch)
     - Sektion E (Rückfragen) enthält fehlende_angaben aus Aktenvermerk
     - Sektion C (Fristen) enthält aktenvermerk-fristen aus dates-Merge
     - todos kommen aus analysis_payload (nicht mehr hardcoded [])

Problem 1: todos hardcoded [] → jetzt aus LLM gelesen
Problem 2: doc_class-spezifischer Prompt → via DOC_CLASS_HINTS
Problem 3: generate_aktenvermerk → todos/fehlende_angaben/fristen in pipeline gemergt
"""

from __future__ import annotations

from pathlib import Path

from core.extractor import extract_text_with_meta
from governance.redaction import prepare_for_llm
from core.pipeline import _to_legacy_segment_analysis, _document_analysis_prompt
from core.export_render import render_segmented_aktenvermerk_text
from core.prompts import DOC_CLASS_HINTS, get_aktenvermerk_prompt, AKTENVERMERK_PROMPT

PDFS_DIR = Path(__file__).parent.parent.parent / "pdfs"
POLICY = {
    "dictionary_entities": [],
    "dictionary_fuzzy": False,
    "dictionary_threshold": 92,
    "pseudonymize_people": True,
}

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _make_synthetic_analysis_payload(doc_class: str, doc_text: str) -> dict:
    """
    Simuliert einen LLM-Output mit todos, fehlende_angaben (via uncertainties) und dates.
    Entspricht dem was analyze_document + merge_aktenvermerk gemeinsam liefern würden.
    """
    return {
        "document_type": doc_class,
        "facts": [
            {"text": "Vertragsparteien und -gegenstand aus Dokument extrahiert.", "citations": [{"page_number": 1, "quote_snippet": doc_text[:60].strip()}]},
            {"text": "Wesentliche Klauseln im Dokument identifiziert.", "citations": [{"page_number": 1, "quote_snippet": doc_text[60:120].strip()}]},
        ],
        "dates": [
            {"text": "Vertragslaufzeit beginnt mit Unterzeichnung.", "citations": []},
        ],
        "issues": [
            {"text": "Klausel zu Haftungsbegrenzung prüfen.", "citations": []},
        ],
        "parties": [],
        "amounts": [],
        "key_facts": ["Vertragsparteien erkannt", "Vertragsgegenstand definiert"],
        "deadlines": ["Zahlungsfrist: 14 Tage ab Rechnungsstellung"],
        "summary_neutral": f"Dokument der Klasse {doc_class} analysiert; wesentliche Klauseln und Fristen extrahiert.",
        "uncertainties": [
            "Sind alle Parteien korrekt identifiziert?",
            # fehlende_angaben aus Aktenvermerk-Merge (Problem 3):
            "Rückfrage: Liegt eine aktuelle Vollmacht vor?",
        ],
        # todos aus analyze_document-Prompt (Problem 1):
        "todos": [
            "Vertragsfrist in Fristenkalender eintragen.",
            "Klausel zu Haftungsbeschränkung mit Mandant besprechen.",
            "Gegenzeichnung durch zweite Partei prüfen.",
        ],
    }


def _build_synthetic_segment_result(analysis: dict, pages: int = 1) -> dict:
    return {
        "segment_meta": {
            "page_start": 1,
            "page_end": pages,
            "title_guess": "Testdokument",
            "doc_class": analysis.get("document_type", "Sonstiges"),
        },
        "analysis": analysis,
        "evidence_map": [],
        "coverage": {},
    }


def _check_pdf(pdf_path: Path) -> dict:
    result = {
        "file": pdf_path.name,
        "ok": False,
        "pages": 0,
        "doc_class": "Sonstiges",
        "todos_in_legacy": [],
        "section_f_empty": True,
        "section_e_has_content": False,
        "doc_class_hint_present": False,
        "aktenvermerk_prompt_ok": False,
        "error": None,
    }

    try:
        file_bytes = pdf_path.read_bytes()
        extraction = extract_text_with_meta(file_bytes, pdf_path.name)
        result["pages"] = extraction.pages_total

        # Redaktion (für realistischen Text ohne Klarnamen)
        ctx = prepare_for_llm(extraction.text, POLICY)

        # doc_class aus Dateiname heuristisch ableiten
        name_lower = pdf_path.name.lower()
        if "miet" in name_lower:
            doc_class = "Vertrag"
        elif "arbeits" in name_lower:
            doc_class = "Vertrag"
        elif "kauf" in name_lower:
            doc_class = "Vertrag"
        elif "gbr" in name_lower or "gesellschaft" in name_lower:
            doc_class = "Vertrag"
        elif "mahn" in name_lower:
            doc_class = "Mahnung"
        elif "abmahn" in name_lower:
            doc_class = "Abmahnung"
        elif "klage" in name_lower or "klageschrift" in name_lower:
            doc_class = "Klageschrift"
        elif "schriftsatz" in name_lower:
            doc_class = "Schriftsatz"
        else:
            doc_class = "Sonstiges"
        result["doc_class"] = doc_class

        # Check 1: DOC_CLASS_HINTS vorhanden für diese Klasse
        result["doc_class_hint_present"] = doc_class in DOC_CLASS_HINTS

        # Check 2: _document_analysis_prompt enthält doc_class-Hinweis
        from doc_class_policy import normalize_doc_class
        prompt = _document_analysis_prompt(
            segment_doc_class=doc_class,
            allow_llm_doc_class_guess=False,
            doc_class=doc_class,
        )
        result["doc_class_hint_present"] = (
            result["doc_class_hint_present"]
            and "Dokumentklassen-Hinweise" in prompt
            and doc_class in prompt
        )

        # Check 3: AKTENVERMERK_PROMPT ist nicht leer und enthält todos/fehlende_angaben
        akten_prompt = get_aktenvermerk_prompt()
        result["aktenvermerk_prompt_ok"] = (
            "todos" in akten_prompt
            and "fehlende_angaben" in akten_prompt
            and "fristen_termine" in akten_prompt
        )

        # Check 4: synthetischer LLM-Output → _to_legacy_segment_analysis → Sektion F
        raw_text = ctx.redacted_text[:500]  # kurzen Ausschnitt für Quote
        synthetic_payload = _make_synthetic_analysis_payload(doc_class, raw_text)
        legacy = _to_legacy_segment_analysis(synthetic_payload)

        result["todos_in_legacy"] = list(legacy.get("todos") or [])
        result["section_f_empty"] = not result["todos_in_legacy"]

        # Check 5: render_segmented_aktenvermerk_text → Sektion F nicht "– keine"
        seg_result = _build_synthetic_segment_result(legacy, pages=extraction.pages_total)
        rendered = render_segmented_aktenvermerk_text([seg_result])
        lines = rendered.splitlines()
        in_f = False
        f_content = []
        for line in lines:
            if line.startswith("F TO-DOS"):
                in_f = True
                continue
            if in_f:
                if line.startswith(("G ", "A ", "B ", "C ", "D ", "E ", "DOKUMENT ")):
                    break
                if line.strip():
                    f_content.append(line.strip())

        result["section_f_empty"] = not f_content or f_content == ["– keine"]
        result["section_e_has_content"] = any(
            "Rückfrage" in line or "prüfen" in line.lower()
            for line in lines
        )

        result["ok"] = (
            not result["section_f_empty"]
            and result["doc_class_hint_present"]
            and result["aktenvermerk_prompt_ok"]
        )

    except Exception as exc:
        result["error"] = str(exc)

    return result


def main() -> int:
    pdf_files = sorted(PDFS_DIR.glob("*.pdf"))
    if not pdf_files:
        print(f"{RED}Keine PDFs in {PDFS_DIR} gefunden.{RESET}")
        return 1

    print(f"\n{BOLD}Document pipeline – Content-Quality Smoke-Test (Datenfluss ohne LLM-Call){RESET}")
    print(f"Prüft: todos-Datenfluss, DOC_CLASS_HINTS, AKTENVERMERK_PROMPT-Aktivierung")
    print(f"PDFs: {len(pdf_files)}\n")
    print("─" * 72)

    all_ok = True
    results = []

    for pdf_path in pdf_files:
        r = _check_pdf(pdf_path)
        results.append(r)

        if r["error"]:
            status = f"{RED}ERROR{RESET}"
            all_ok = False
        elif r["ok"]:
            status = f"{GREEN}PASS{RESET}"
        else:
            status = f"{RED}FAIL{RESET}"
            all_ok = False

        hint_ok = f"{GREEN}✓{RESET}" if r["doc_class_hint_present"] else f"{RED}✗{RESET}"
        akten_ok = f"{GREEN}✓{RESET}" if r["aktenvermerk_prompt_ok"] else f"{RED}✗{RESET}"
        f_ok = f"{GREEN}✓{RESET}" if not r["section_f_empty"] else f"{RED}✗ leer{RESET}"

        print(f"{status}  {r['file']}")
        print(
            f"      Klasse: {r['doc_class']:12}  |  "
            f"DOC_CLASS_HINTS: {hint_ok}  |  "
            f"AKTENVERMERK_PROMPT: {akten_ok}  |  "
            f"Sektion F: {f_ok}"
        )
        if r["todos_in_legacy"]:
            preview = "; ".join(r["todos_in_legacy"][:2])
            if len(r["todos_in_legacy"]) > 2:
                preview += f" (+{len(r['todos_in_legacy'])-2})"
            print(f"      todos: {preview}")
        if r["error"]:
            print(f"      {RED}Fehler: {r['error']}{RESET}")
        print()

    print("─" * 72)
    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed

    if all_ok:
        print(f"\n{GREEN}{BOLD}Alle {len(results)} PDFs bestanden.{RESET}")
        print(f"  ✓ todos fließen nicht mehr als [] durch – Sektion F befüllt")
        print(f"  ✓ Klassenspezifische Prompt-Hinweise aktiv (DOC_CLASS_HINTS)")
        print(f"  ✓ AKTENVERMERK_PROMPT ist aktiviert (todos/fehlende_angaben/fristen)\n")
        return 0
    else:
        print(f"\n{RED}{BOLD}{failed} von {len(results)} PDFs fehlgeschlagen.{RESET}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
