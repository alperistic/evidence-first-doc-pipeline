"""
smoke_test_restore.py – End-to-End-Smoke-Test: PDF → Redaktion → Restore → Export-Check.

Testet den kritischen Pfad des Restore-Fixes ohne echten LLM-Aufruf:
  1. PDF-Text extrahieren (pypdf)
  2. prepare_for_llm() → redacted_text + mapping (alle Platzhalter erzeugt)
  3. restore_from_llm(redacted_text, mapping)  ← LLM-Passthrough als Worst-Case
  4. _find_internal_export_placeholder_hits → muss leer sein
  5. Klarnamen aus dem Mapping müssen im restaurierten Text erscheinen

Hinweis: "LLM-Passthrough" = redacted_text unverändert als LLM-Output behandelt.
Das ist der Worst-Case: Alle Platzhalter sind noch im Text → jeder muss restauriert werden.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.extractor import extract_text_with_meta
from governance.redaction import prepare_for_llm, restore_from_llm
from core.pipeline import _find_internal_export_placeholder_hits

PDFS_DIR = Path(__file__).parent.parent.parent / "pdfs"
POLICY = {
    "dictionary_entities": [],
    "dictionary_fuzzy": False,
    "dictionary_threshold": 92,
    "pseudonymize_people": True,
}

# ANSI-Farben
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _check_pdf(pdf_path: Path) -> dict:
    result = {
        "file": pdf_path.name,
        "ok": False,
        "pages": 0,
        "chars_raw": 0,
        "placeholders_created": 0,
        "placeholder_keys": [],
        "unrestored": [],
        "missing_originals": [],
        "error": None,
    }

    try:
        file_bytes = pdf_path.read_bytes()
        extraction = extract_text_with_meta(file_bytes, pdf_path.name)
        result["pages"] = extraction.pages_total
        result["chars_raw"] = extraction.chars_total

        # Redaktion
        ctx = prepare_for_llm(extraction.text, POLICY)
        mapping = ctx.mapping  # placeholder -> original
        result["placeholders_created"] = len(mapping)
        result["placeholder_keys"] = sorted(mapping.keys())

        # LLM-Passthrough: redacted_text als "LLM-Output" behandeln
        llm_output = ctx.redacted_text

        # Restore
        restored = restore_from_llm(llm_output, mapping)

        # Check 1: keine unrestaurierten Platzhalter
        hits = _find_internal_export_placeholder_hits({"summary": restored})
        result["unrestored"] = hits

        # Check 2: alle Klarnamen aus dem Mapping erscheinen im restaurierten Text
        missing_originals = []
        for placeholder, original in mapping.items():
            if original and original not in restored:
                missing_originals.append((placeholder, original))
        result["missing_originals"] = missing_originals

        result["ok"] = not hits and not missing_originals

    except Exception as exc:
        result["error"] = str(exc)

    return result


def main() -> int:
    pdf_files = sorted(PDFS_DIR.glob("*.pdf"))
    if not pdf_files:
        print(f"{RED}Keine PDFs in {PDFS_DIR} gefunden.{RESET}")
        return 1

    print(f"\n{BOLD}Document pipeline – End-to-End Smoke-Test: PDF → Redaktion → Restore → Export-Check{RESET}")
    print(f"PDFs-Verzeichnis: {PDFS_DIR}")
    print(f"Anzahl PDFs: {len(pdf_files)}\n")
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

        print(f"{status}  {r['file']}")
        print(
            f"      Seiten: {r['pages']:>3}  |  Zeichen (roh): {r['chars_raw']:>7,}  |  "
            f"Platzhalter erzeugt: {r['placeholders_created']:>3}"
        )

        if r["placeholder_keys"]:
            keys_preview = ", ".join(r["placeholder_keys"][:8])
            if len(r["placeholder_keys"]) > 8:
                keys_preview += f" … (+{len(r['placeholder_keys']) - 8} weitere)"
            print(f"      Platzhalter: {keys_preview}")

        if r["error"]:
            print(f"      {RED}Fehler: {r['error']}{RESET}")
        if r["unrestored"]:
            print(
                f"      {RED}Unrestauriert (Export-Block!): {', '.join(r['unrestored'])}{RESET}"
            )
        if r["missing_originals"]:
            pairs = ", ".join(
                f"{ph}→{YELLOW}'{orig[:20]}'{RESET}"
                for ph, orig in r["missing_originals"][:5]
            )
            print(f"      {YELLOW}Klarnamen nicht gefunden: {pairs}{RESET}")

        print()

    print("─" * 72)
    total = len(results)
    passed = sum(1 for r in results if r["ok"])
    failed = total - passed

    if all_ok:
        print(
            f"\n{GREEN}{BOLD}Alle {total} PDFs bestanden.{RESET}  "
            f"Kein Export-Block, alle Klarnamen restauriert.\n"
        )
        return 0
    else:
        print(
            f"\n{RED}{BOLD}{failed} von {total} PDFs fehlgeschlagen.{RESET}\n"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
