"""
export_content.py - Selection logic for export content.
"""

from __future__ import annotations

from typing import Any, Literal


def build_export_content(
    result_payload: dict[str, Any],
    export_selection: str,
    mode: Literal["internal", "external"] = "internal",
) -> dict[str, Any]:
    """Selects export parts based on the UI selection.

    Note: the export_selection values ("Zusammenfassung", "Aktennotiz", "Beides")
    are a string-based data contract consumed by callers and asserted directly in
    tests - they are intentionally left untranslated (see UI layer for the
    English display labels).
    """
    if mode not in {"internal", "external"}:
        raise ValueError("mode must be one of: internal, external")

    summary = str(result_payload.get("summary", "") or "")
    aktennotiz = str(result_payload.get("aktennotiz", "") or "")
    evidence = result_payload.get("evidence")

    # Hard rule: the external (client-facing) version never includes the internal note.
    if mode == "external":
        return {"summary": summary, "evidence": evidence}

    if export_selection == "Zusammenfassung":
        return {"summary": summary, "aktennotiz": "", "evidence": evidence}
    if export_selection == "Aktennotiz":
        return {"summary": "", "aktennotiz": aktennotiz, "evidence": evidence}
    if export_selection == "Beides":
        return {"summary": summary, "aktennotiz": aktennotiz, "evidence": evidence}
    raise ValueError("export_selection must be one of: Zusammenfassung, Aktennotiz, Beides")
