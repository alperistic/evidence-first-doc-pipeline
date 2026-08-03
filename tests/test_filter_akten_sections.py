"""Tests für _filter_akten_sections und die davon abhängigen
_to_legacy_segment_analysis-Änderungen (widersprueche + prueffragen).

Abgedeckte Anforderungen:
  - Duplikat in fehlende_angaben → dedupliziert (Filter 1)
  - Kernaussagen-Text in fehlende_angaben → entfernt (Filter 2)
  - Echter fehlender Punkt → bleibt erhalten
  - widersprueche fließt in legacy_analysis["widersprueche"] durch
  - prueffragen = [] wenn fehlende_angaben_akten leer oder nicht gesetzt
  - prueffragen aus fehlende_angaben, nicht aus issues
"""
import pytest
from core.pipeline import _filter_akten_sections, _to_legacy_segment_analysis


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _akten(kernaussagen=None, widersprueche=None, fehlende_angaben=None, todos=None):
    """Minimales akten_payload-Dict."""
    return {
        "dokumenttyp_parteien": "Mahnung",
        "kernaussagen": kernaussagen or [],
        "widersprueche": widersprueche or [],
        "fehlende_angaben": fehlende_angaben or [],
        "todos": todos or [],
        "fristen_termine": [],
    }


def _legacy_payload(fehlende_angaben_akten=None, widersprueche_akten=None, issues=None, uncertainties=None):
    """Simuliert den analysis_payload-Zustand nach dem Aktenvermerk-Merge."""
    payload = {
        "document_type": "Mahnung",
        "facts": [],
        "dates": [],
        "parties": [],
        "amounts": [],
        "summary_neutral": "Test",
        "issues": issues,
        "uncertainties": uncertainties or [],
    }
    if fehlende_angaben_akten is not None:
        payload["fehlende_angaben_akten"] = fehlende_angaben_akten
    if widersprueche_akten is not None:
        payload["widersprueche_akten"] = widersprueche_akten
    return payload


# ---------------------------------------------------------------------------
# Tests für _filter_akten_sections
# ---------------------------------------------------------------------------

class TestFilterAktenSections:

    def test_duplikat_in_fehlende_angaben_wird_entfernt(self):
        """Filter 1: Identische Strings in fehlende_angaben → nur erster bleibt."""
        payload = _akten(
            fehlende_angaben=[
                "Wurde die Kaution verzinst?",
                "Wurde die Kaution verzinst?",
            ]
        )
        result = _filter_akten_sections(payload)
        assert result["fehlende_angaben"] == ["Wurde die Kaution verzinst?"]

    def test_aehnlicher_duplikat_fehlende_angaben_wird_entfernt(self):
        """Filter 1: >85%-ähnlicher Eintrag → dedupliziert (zweiter fliegt raus)."""
        payload = _akten(
            fehlende_angaben=[
                "Liegt eine Vollmacht vor?",
                "Liegt eine Vollmacht vor",  # kein Fragezeichen, sonst identisch
            ]
        )
        result = _filter_akten_sections(payload)
        assert len(result["fehlende_angaben"]) == 1
        assert "Vollmacht" in result["fehlende_angaben"][0]

    def test_kernaussagen_text_in_fehlende_angaben_wird_entfernt(self):
        """Filter 2: Wenn fehlende_angaben-Text zu >80% in kernaussagen → entfernen."""
        payload = _akten(
            kernaussagen=[
                {"text": "Die Kaution beträgt drei Monatsmieten.", "citations": []},
            ],
            fehlende_angaben=[
                "Die Kaution beträgt drei Monatsmieten.",  # exakte Kopie → weg
                "Liegt eine Vollmacht vor?",               # legitime Frage → bleibt
            ],
        )
        result = _filter_akten_sections(payload)
        remaining = result["fehlende_angaben"]
        assert "Vollmacht" in " ".join(remaining), f"Echter Eintrag entfernt: {remaining}"
        assert not any("Kaution" in x and "Monatsmieten" in x for x in remaining), (
            f"Kernaussagen-Kopie nicht entfernt: {remaining}"
        )

    def test_echter_fehlender_punkt_bleibt_erhalten(self):
        """Nur belegte Sachverhalte werden entfernt – legitime Fragen bleiben."""
        payload = _akten(
            kernaussagen=[
                {"text": "Vertrag wurde am 01.01.2026 unterzeichnet.", "citations": []},
            ],
            fehlende_angaben=[
                "Liegt eine notarielle Beglaubigung vor?",
                "Wurde das Dokument fristgerecht zugestellt?",
            ],
        )
        result = _filter_akten_sections(payload)
        assert len(result["fehlende_angaben"]) == 2, (
            f"Legitime Einträge wurden entfernt: {result['fehlende_angaben']}"
        )

    def test_kernaussagen_text_in_widersprueche_wird_entfernt(self):
        """Filter 2 gilt auch für widersprueche."""
        payload = _akten(
            kernaussagen=[
                {"text": "Kündigungsfrist beträgt drei Monate laut Vertrag.", "citations": []},
            ],
            widersprueche=[
                {"text": "Kündigungsfrist beträgt drei Monate laut Vertrag.", "citations": []},
                {"text": "§ 3 und § 7 nennen unterschiedliche Fristen.", "citations": []},
            ],
        )
        result = _filter_akten_sections(payload)
        texts = [w.get("text", "") for w in result["widersprueche"]]
        assert not any("Kündigungsfrist" in t and "Monate laut Vertrag" in t for t in texts), (
            f"Kernaussagen-Kopie in widersprueche nicht entfernt: {texts}"
        )
        assert any("§ 3" in t for t in texts), (
            f"Echter Widerspruch entfernt: {texts}"
        )

    def test_filter3_fehlende_angaben_identisch_mit_widersprueche_wird_entfernt(self):
        """Filter 3: textidentischer Eintrag in fehlende_angaben + widersprueche → aus fehlende_angaben."""
        payload = _akten(
            widersprueche=[
                {"text": "§ 3 und § 7 widersprechen sich zur Kündigungsfrist.", "citations": []},
            ],
            fehlende_angaben=[
                "§ 3 und § 7 widersprechen sich zur Kündigungsfrist.",  # identisch → weg
                "Wurde die Kaution zurückgezahlt?",                      # echt → bleibt
            ],
        )
        result = _filter_akten_sections(payload)
        remaining = result["fehlende_angaben"]
        assert len(remaining) == 1, f"Erwartet 1 Eintrag, got {len(remaining)}: {remaining}"
        assert "Kaution" in remaining[0]

    def test_leere_felder_bleiben_leer(self):
        """_filter_akten_sections bricht bei leeren Arrays nicht."""
        payload = _akten()
        result = _filter_akten_sections(payload)
        assert result["widersprueche"] == []
        assert result["fehlende_angaben"] == []

    def test_original_dict_wird_nicht_veraendert(self):
        """_filter_akten_sections mutiert das Original nicht."""
        original_f = ["Wurde die Kaution verzinst?", "Wurde die Kaution verzinst?"]
        payload = _akten(fehlende_angaben=original_f)
        _filter_akten_sections(payload)
        assert len(payload["fehlende_angaben"]) == 2, "Original wurde mutiert"


# ---------------------------------------------------------------------------
# Tests für _to_legacy_segment_analysis: widersprueche + prueffragen
# ---------------------------------------------------------------------------

class TestLegacyWithWidersprueche:

    def test_widersprueche_akten_fliesst_in_legacy_analysis(self):
        """widersprueche_akten wird als widersprueche in legacy_analysis aufgenommen."""
        w_item = {"text": "§ 3 und § 7 widersprechen sich.", "citations": []}
        payload = _legacy_payload(widersprueche_akten=[w_item])
        result = _to_legacy_segment_analysis(payload)
        widersprueche = result.get("widersprueche", [])
        assert len(widersprueche) == 1, f"widersprueche fehlt in legacy_analysis: {widersprueche}"
        assert widersprueche[0].get("text") == "§ 3 und § 7 widersprechen sich."

    def test_widersprueche_leer_wenn_nicht_gesetzt(self):
        """Wenn widersprueche_akten nicht in analysis_payload → widersprueche = []."""
        payload = _legacy_payload()
        result = _to_legacy_segment_analysis(payload)
        assert result.get("widersprueche", []) == []


class TestLegacyPrueffragen:

    def test_prueffragen_leer_wenn_kein_aktenvermerk(self):
        """Ohne fehlende_angaben_akten-Key ist prueffragen immer []."""
        payload = _legacy_payload(
            issues=[{"text": "Auffälligkeit X", "citations": []}],
            uncertainties=["Auffälligkeit X"],
        )
        result = _to_legacy_segment_analysis(payload)
        assert result.get("prueffragen") == [], (
            f"prueffragen sollte [] sein, got: {result.get('prueffragen')}"
        )

    def test_prueffragen_leer_wenn_fehlende_angaben_akten_leer(self):
        """fehlende_angaben_akten=[] → prueffragen = []."""
        payload = _legacy_payload(
            fehlende_angaben_akten=[],
            uncertainties=["Irgendeine Unsicherheit"],
        )
        result = _to_legacy_segment_analysis(payload)
        assert result.get("prueffragen") == []

    def test_prueffragen_aus_fehlende_angaben_nicht_aus_issues(self):
        """prueffragen kommen NUR aus fehlende_angaben_akten, nicht aus issues/uncertainties."""
        fa = ["Liegt eine Vollmacht vor?", "Wurde die Kaution verzinst?"]
        payload = _legacy_payload(
            fehlende_angaben_akten=fa,
            issues=[{"text": "Völlig andere Auffälligkeit", "citations": []}],
            uncertainties=["Noch eine andere Unsicherheit"],
        )
        result = _to_legacy_segment_analysis(payload)
        prueffragen = result.get("prueffragen", [])
        assert "Liegt eine Vollmacht vor?" in prueffragen, (
            f"fehlende_angaben-Eintrag fehlt in prueffragen: {prueffragen}"
        )
        assert "Wurde die Kaution verzinst?" in prueffragen
        assert not any("Auffälligkeit" in q for q in prueffragen), (
            f"issues-Text in prueffragen: {prueffragen}"
        )
        assert not any("Unsicherheit" in q for q in prueffragen), (
            f"uncertainties-Text in prueffragen: {prueffragen}"
        )

    def test_prueffragen_enthalten_keine_rueckfrage_prefix(self):
        """prueffragen-Einträge erhalten keinen automatischen 'Rückfrage: '-Prefix mehr."""
        fa = ["Wurde die Miete gemindert?"]
        payload = _legacy_payload(fehlende_angaben_akten=fa)
        result = _to_legacy_segment_analysis(payload)
        prueffragen = result.get("prueffragen", [])
        assert prueffragen == ["Wurde die Miete gemindert?"], (
            f"Unerwarteter Prefix oder anderer Inhalt: {prueffragen}"
        )


# ---------------------------------------------------------------------------
# Fix 1: Threshold 0.60 — semantisch ähnliche Einträge deduplizieren
# ---------------------------------------------------------------------------

class TestThreshold060Dedup:
    """Threshold von 0.85 → 0.60: semantisch gleiche, textuell ~64%-ähnliche
    Einträge werden dedupliziert; inhaltlich verschiedene Einträge bleiben beide."""

    def test_aehnliche_widersprueche_dok2_dedupliziert(self):
        """Realfall Dok 2: 'Rechtmäßigkeit der Mietminderung...' und 'Unklarheit über
        die Rechtmäßigkeit der Mietminderung...' (similarity ≈ 0.64) → nur einer bleibt."""
        payload = _akten(
            widersprueche=[
                {"text": "Rechtmäßigkeit der Mietminderung aufgrund behaupteter Mängel.", "citations": []},
                {"text": "Unklarheit über die Rechtmäßigkeit der Mietminderung durch die Mieterin.", "citations": []},
            ]
        )
        result = _filter_akten_sections(payload)
        assert len(result["widersprueche"]) == 1, (
            f"Erwarte 1 Eintrag nach Dedup, got {len(result['widersprueche'])}: {result['widersprueche']}"
        )
        assert "Mietminderung" in result["widersprueche"][0].get("text", "")

    def test_aehnliche_fehlende_angaben_dedupliziert(self):
        """Semantisch gleiche fehlende_angaben (~0.64 similarity) → nur erster bleibt."""
        payload = _akten(
            fehlende_angaben=[
                "Rechtmäßigkeit der Mietminderung aufgrund behaupteter Mängel.",
                "Unklarheit über die Rechtmäßigkeit der Mietminderung durch die Mieterin.",
            ]
        )
        result = _filter_akten_sections(payload)
        assert len(result["fehlende_angaben"]) == 1, (
            f"Erwarte 1 Eintrag nach Dedup, got {len(result['fehlende_angaben'])}: {result['fehlende_angaben']}"
        )

    def test_verschiedene_widersprueche_bleiben_beide_erhalten(self):
        """Zwei inhaltlich verschiedene Einträge (similarity < 0.60) bleiben beide."""
        payload = _akten(
            widersprueche=[
                {"text": "Kautionshöhe im Vertrag nicht eindeutig geregelt.", "citations": []},
                {"text": "Kündigungsfrist weicht vom gesetzlichen Mindestmaß ab.", "citations": []},
            ]
        )
        result = _filter_akten_sections(payload)
        assert len(result["widersprueche"]) == 2, (
            f"Verschiedene Einträge wurden fälschlich dedupliziert: {result['widersprueche']}"
        )

    def test_verschiedene_fehlende_angaben_bleiben_beide_erhalten(self):
        """Zwei inhaltlich verschiedene fehlende_angaben bleiben beide."""
        payload = _akten(
            fehlende_angaben=[
                "Liegt eine notarielle Beglaubigung des Mietvertrags vor?",
                "Wurde die Betriebskostenabrechnung fristgerecht zugestellt?",
            ]
        )
        result = _filter_akten_sections(payload)
        assert len(result["fehlende_angaben"]) == 2, (
            f"Verschiedene Einträge wurden fälschlich dedupliziert: {result['fehlende_angaben']}"
        )
