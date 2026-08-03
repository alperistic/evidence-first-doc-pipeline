"""
export_content.py – Selektionslogik für Exportinhalte.
"""

from __future__ import annotations

from typing import Any, Literal


def build_export_content(
    result_payload: dict[str, Any],
    export_selection: str,
    mode: Literal["internal", "external"] = "internal",
) -> dict[str, Any]:
    """Wählt Export-Teile basierend auf der UI-Auswahl aus."""
    if mode not in {"internal", "external"}:
        raise ValueError("mode must be one of: internal, external")

    summary = str(result_payload.get("summary", "") or "")
    aktennotiz = str(result_payload.get("aktennotiz", "") or "")
    evidence = result_payload.get("evidence")

    # Harte Regel: Mandantenfassung enthält nie interne Aktennotiz.
    if mode == "external":
        return {"summary": summary, "evidence": evidence}

    if export_selection == "Zusammenfassung":
        return {"summary": summary, "aktennotiz": "", "evidence": evidence}
    if export_selection == "Aktennotiz":
        return {"summary": "", "aktennotiz": aktennotiz, "evidence": evidence}
    if export_selection == "Beides":
        return {"summary": summary, "aktennotiz": aktennotiz, "evidence": evidence}
    raise ValueError("export_selection must be one of: Zusammenfassung, Aktennotiz, Beides")
