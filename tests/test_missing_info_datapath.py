"""Tests: fehlende_angaben → missing_info datapath via _to_legacy_segment_analysis.

Covers Bug B (Codex-Audit P2): fehlende_angaben that are merged into
analysis_payload["uncertainties"] by _process_segment must always reach
legacy_payload["missing_info"], regardless of whether issues is None, []
or a non-empty list.
"""
import pytest
from core.pipeline import _to_legacy_segment_analysis


def _payload(issues, fehlende_angaben):
    """Minimal analysis_payload simulating post-_process_segment state.

    _process_segment merges fehlende_angaben into uncertainties before calling
    _to_legacy_segment_analysis, so we do the same here directly.
    """
    uncertainties = [str(x) for x in fehlende_angaben] if fehlende_angaben else []
    return {
        "document_type": "Mahnung",
        "issues": issues,
        "uncertainties": uncertainties,
        "facts": [],
        "dates": [],
        "parties": [],
        "amounts": [],
        "summary_neutral": "Test",
    }


class TestMissingInfoDatapath:
    """fehlende_angaben always land in missing_info, independent of issues."""

    # ------------------------------------------------------------------ #
    # Core bug cases                                                       #
    # ------------------------------------------------------------------ #

    def test_issues_empty_list_fehlende_angaben_present(self):
        """issues=[] + fehlende_angaben=["X"] → missing_info enthält "X"."""
        result = _to_legacy_segment_analysis(_payload(
            issues=[],
            fehlende_angaben=["Wurde die Kaution verzinst?"],
        ))
        missing = result.get("missing_info") or []
        assert "Wurde die Kaution verzinst?" in missing, (
            f"fehlende_angaben nicht in missing_info bei issues=[]: {missing}"
        )

    def test_issues_with_content_and_fehlende_angaben(self):
        """issues=["Y"] + fehlende_angaben=["X"] → missing_info enthält beides."""
        result = _to_legacy_segment_analysis(_payload(
            issues=[{"text": "Auffälligkeit Y", "citations": []}],
            fehlende_angaben=["Wurde die Kaution verzinst?"],
        ))
        missing = result.get("missing_info") or []
        joined = " ".join(str(m) for m in missing)
        assert "Auffälligkeit Y" in joined, (
            f"issues-Text nicht in missing_info: {missing}"
        )
        assert "Wurde die Kaution verzinst?" in joined, (
            f"fehlende_angaben nicht in missing_info bei issues mit Inhalt: {missing}"
        )

    # ------------------------------------------------------------------ #
    # Existing path still works                                            #
    # ------------------------------------------------------------------ #

    def test_issues_none_fehlende_angaben_present(self):
        """issues=None + fehlende_angaben=["X"] → missing_info enthält "X" (existing path)."""
        result = _to_legacy_segment_analysis(_payload(
            issues=None,
            fehlende_angaben=["Liegt eine Vollmacht vor?"],
        ))
        missing = result.get("missing_info") or []
        assert "Liegt eine Vollmacht vor?" in missing, (
            f"fehlende_angaben nicht in missing_info bei issues=None: {missing}"
        )

    # ------------------------------------------------------------------ #
    # Edge cases                                                           #
    # ------------------------------------------------------------------ #

    def test_issues_empty_fehlende_angaben_empty(self):
        """issues=[] + fehlende_angaben=[] → missing_info ist leer."""
        result = _to_legacy_segment_analysis(_payload(issues=[], fehlende_angaben=[]))
        missing = result.get("missing_info") or []
        assert missing == [], f"missing_info sollte leer sein: {missing}"

    def test_no_duplicates_when_issues_and_uncertainties_overlap(self):
        """Überschneidung zwischen issues und fehlende_angaben wird dedupliziert."""
        result = _to_legacy_segment_analysis(_payload(
            issues=[{"text": "Kaution nicht verzinst", "citations": []}],
            fehlende_angaben=["Kaution nicht verzinst"],
        ))
        missing = result.get("missing_info") or []
        count = sum(1 for m in missing if str(m) == "Kaution nicht verzinst")
        assert count == 1, f"Duplikat in missing_info: {missing}"

    def test_multiple_fehlende_angaben_all_appear(self):
        """Mehrere fehlende_angaben-Einträge landen alle in missing_info."""
        items = [
            "Liegt eine Vollmacht vor?",
            "Wurde die Kaution verzinst?",
            "Wurde Verzugszins geltend gemacht?",
        ]
        result = _to_legacy_segment_analysis(_payload(issues=[], fehlende_angaben=items))
        missing = result.get("missing_info") or []
        for item in items:
            assert item in missing, f"{item!r} nicht in missing_info: {missing}"

    def test_issues_string_items_and_fehlende_angaben(self):
        """issues als String-Liste + fehlende_angaben → beides in missing_info."""
        result = _to_legacy_segment_analysis(_payload(
            issues=["Hinweis: Widerspruch in § 3 und § 7"],
            fehlende_angaben=["Wurde Verzugszins geltend gemacht?"],
        ))
        missing = result.get("missing_info") or []
        joined = " ".join(str(m) for m in missing)
        assert "Widerspruch" in joined, f"issues-String nicht in missing_info: {missing}"
        assert "Verzugszins" in joined, f"fehlende_angaben nicht in missing_info: {missing}"

    def test_issues_dict_list_fehlende_angaben_present(self):
        """issues als dict-Liste (mit citations) + fehlende_angaben → beides vorhanden."""
        result = _to_legacy_segment_analysis(_payload(
            issues=[{"text": "Unklare Kostenverteilung", "citations": [{"page_number": 1, "quote_snippet": "laut § 5"}]}],
            fehlende_angaben=["Wurde Nebenkostenabrechnung vorgelegt?"],
        ))
        missing = result.get("missing_info") or []
        joined = " ".join(str(m) for m in missing)
        assert "Unklare Kostenverteilung" in joined, f"issues-Dict nicht in missing_info: {missing}"
        assert "Nebenkostenabrechnung" in joined, f"fehlende_angaben nicht in missing_info: {missing}"

    def test_issues_none_no_fehlende_angaben(self):
        """issues=None + fehlende_angaben=[] → missing_info ist leer."""
        result = _to_legacy_segment_analysis(_payload(issues=None, fehlende_angaben=[]))
        missing = result.get("missing_info") or []
        assert missing == [], f"missing_info sollte leer sein: {missing}"

    def test_fehlende_angaben_order_preserved(self):
        """Reihenfolge der fehlende_angaben in missing_info bleibt erhalten."""
        items = [f"Frage {i}?" for i in range(5)]
        result = _to_legacy_segment_analysis(_payload(issues=[], fehlende_angaben=items))
        missing = result.get("missing_info") or []
        for item in items:
            assert item in missing, f"{item!r} nicht in missing_info: {missing}"

    def test_issues_with_many_items_and_fehlende_angaben(self):
        """Viele issues + fehlende_angaben → beide vollständig in missing_info."""
        issue_texts = [f"Auffälligkeit {i}" for i in range(4)]
        issues = [{"text": t, "citations": []} for t in issue_texts]
        result = _to_legacy_segment_analysis(_payload(
            issues=issues,
            fehlende_angaben=["Liegt eine Vollmacht vor?"],
        ))
        missing = result.get("missing_info") or []
        joined = " ".join(str(m) for m in missing)
        assert "Liegt eine Vollmacht vor?" in joined, (
            f"fehlende_angaben nicht in missing_info bei vielen issues: {missing}"
        )
        assert any("Auffälligkeit" in str(m) for m in missing), (
            f"issues fehlen in missing_info: {missing}"
        )

    def test_missing_info_is_list(self):
        """missing_info ist immer eine Liste, nie None."""
        result = _to_legacy_segment_analysis(_payload(issues=[], fehlende_angaben=[]))
        assert isinstance(result.get("missing_info"), list), (
            f"missing_info ist kein list: {result.get('missing_info')!r}"
        )

    def test_issues_empty_list_multiple_fehlende_angaben_no_issue_bleedthrough(self):
        """issues=[] erzeugt keine Phantomeinträge — nur fehlende_angaben erscheinen."""
        result = _to_legacy_segment_analysis(_payload(
            issues=[],
            fehlende_angaben=["Wurde die Miete gemindert?"],
        ))
        missing = result.get("missing_info") or []
        assert len(missing) == 1, (
            f"Erwartet genau 1 Eintrag, got {len(missing)}: {missing}"
        )
        assert missing[0] == "Wurde die Miete gemindert?", (
            f"Falscher Inhalt in missing_info: {missing}"
        )
