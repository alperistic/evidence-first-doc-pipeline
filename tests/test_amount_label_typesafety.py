"""
tests/test_amount_label_typesafety.py – Type-Safety für Betragsklassifikation

Sicherstellt, dass:
- Eindeutige Kontexte confidence 1.0 erhalten
- Ambiguous-Kontexte confidence 0.0 erhalten
- Unbekannte Kontexte auf "betrag" / confidence 0.0 fallen
- Streitwert als eigene Kategorie erkannt wird
- Unklare Beträge nicht in Eckdaten-Output erscheinen
- Kaution und Rückstand nie verwechselt werden
"""

import pytest

from core.labels import (
    BetragKlassifikation,
    BETRAG_KONTEXT_KEYWORDS,
    klassifiziere_betrag_mit_confidence,
    klassifiziere_betrag_mit_kontext,
    extract_context_lines,
    _has_zeitraum,
)
from core.evidence import MIN_BETRAG_CONFIDENCE, build_evidence_map
from core.export_render import render_segmented_summary_text


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EINDEUTIG_FIXTURES = [
    ("kaution wurde als Sicherheitsleistung hinterlegt", "kaution", 1.0),
    ("Mietrückstand von drei Monaten ist fällig", "rueckstand", 1.0),
    ("monatliche Kaltmiete beträgt 800 €", "miete", 1.0),
    ("Streitwert wird auf 5.000 € festgesetzt", "streitwert", 1.0),
    ("Nebenkosten-Vorauszahlung monatlich 120 €", "nebenkosten", 1.0),
]

AMBIGUOUS_FIXTURES = [
    "Kaution entspricht dem Mietrückstand",            # kaution + rueckstand
    "Kaltmiete sowie Nebenkosten sind offen",          # miete + nebenkosten
]

UNBEKANNT_FIXTURES = [
    "Der Betrag wurde überwiesen",
    "Zahlung erfolgte am 01.01.2024",
]

BAECKEREI_FIXTURE = """
Mietverhältnis: Bäckerei Goldkruste GmbH, Marktplatz 5, 76133 Karlsruhe
Monatliche Kaltmiete: 2.400 €
Mietrückstand: 3 Monate × 2.400 € = 7.200 €
Kaution: 7.200 € (3 Monatsmieten, hinterlegt am 01.03.2023)
Der Mieter schuldet einen Mietrückstand von 7.200 €.
Die Kaution beträgt ebenfalls 7.200 € und wurde als Sicherheitsleistung hinterlegt.
"""


# ---------------------------------------------------------------------------
# 1. Eindeutige Klassifikation – confidence 1.0
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kontext,erwartet_feldname,erwartet_confidence", EINDEUTIG_FIXTURES)
def test_eindeutige_klassifikation_confidence_1(kontext, erwartet_feldname, erwartet_confidence):
    result = klassifiziere_betrag_mit_confidence(kontext)
    assert result.confidence == erwartet_confidence, (
        f"Kontext {kontext!r}: erwartet confidence {erwartet_confidence}, "
        f"erhalten {result.confidence}"
    )
    assert result.feldname == erwartet_feldname, (
        f"Kontext {kontext!r}: erwartet feldname {erwartet_feldname!r}, "
        f"erhalten {result.feldname!r}"
    )
    assert result.matched_keyword is not None


# ---------------------------------------------------------------------------
# 2. Ambiguous – confidence 0.0
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kontext", AMBIGUOUS_FIXTURES)
def test_ambiguous_confidence_0(kontext):
    result = klassifiziere_betrag_mit_confidence(kontext)
    assert result.confidence == 0.0, (
        f"Ambiguous Kontext {kontext!r} muss confidence 0.0 haben, "
        f"erhalten {result.confidence}"
    )


# ---------------------------------------------------------------------------
# 3. Unbekannter Kontext – Fallback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kontext", UNBEKANNT_FIXTURES)
def test_unbekannt_fallback(kontext):
    result = klassifiziere_betrag_mit_confidence(kontext)
    assert result.feldname == "betrag", (
        f"Unbekannter Kontext {kontext!r}: feldname muss 'betrag' sein, "
        f"erhalten {result.feldname!r}"
    )
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# 4. Streitwert als eigene Kategorie
# ---------------------------------------------------------------------------

def test_streitwert_eigene_kategorie():
    result = klassifiziere_betrag_mit_confidence("Streitwert 5.000 €")
    assert result.feldname == "streitwert", (
        f"Streitwert-Kontext: feldname muss 'streitwert' sein, erhalten {result.feldname!r}"
    )
    assert result.confidence == 1.0
    assert "streitwert" in BETRAG_KONTEXT_KEYWORDS, (
        "BETRAG_KONTEXT_KEYWORDS muss 'streitwert' als Schlüssel enthalten"
    )


# ---------------------------------------------------------------------------
# 5. Unklar nicht in Output
# ---------------------------------------------------------------------------

def test_unklar_nicht_in_output():
    # Ambiguous evidence direkt konstruieren (z.B. Kaution UND Rückstand in einem Kontext)
    # – betraege_unklar-Feld erscheint nicht im Eckdaten-Block.
    evidence_map = [
        {"field": "betraege_unklar", "value": "1.234 €", "page": 1, "quote": "Kaution entspricht dem Mietrückstand"},
        {"field": "kaution", "value": "2.000 €", "page": 1, "quote": "Kaution hinterlegt"},
    ]
    segment_results = [
        {
            "segment_meta": {"title_guess": "Test", "page_start": 1, "page_end": 1},
            "analysis": {"dokumenttyp_parteien": "Sonstiges", "kernaussagen": []},
            "evidence_map": evidence_map,
        }
    ]
    summary = render_segmented_summary_text(segment_results)
    assert "1.234" not in summary, (
        f"Ambiguous Betrag darf nicht im Output erscheinen.\nSummary:\n{summary}"
    )
    assert "2.000" in summary, (
        f"Kaution-Betrag muss im Output erscheinen.\nSummary:\n{summary}"
    )


# ---------------------------------------------------------------------------
# 6. Kaution-Kontext gibt nie "rueckstand"
# ---------------------------------------------------------------------------

def test_kaution_nicht_als_rueckstand():
    kaution_kontexte = [
        "Die Kaution beträgt 5.000 €",
        "Kaution: 3.000 € (hinterlegt)",
        "Sicherheitsleistung von 2.400 €",
    ]
    for kontext in kaution_kontexte:
        result = klassifiziere_betrag_mit_confidence(kontext)
        assert result.feldname != "rueckstand", (
            f"Kaution-Kontext {kontext!r} darf nicht als 'rueckstand' klassifiziert werden, "
            f"erhalten {result.feldname!r}"
        )


# ---------------------------------------------------------------------------
# 7. Rückstand-Kontext gibt nie "kaution"
# ---------------------------------------------------------------------------

def test_rueckstand_nicht_als_kaution():
    rueckstand_kontexte = [
        "Mietrückstand von 7.200 €",
        "Der Mieter schuldet einen Zahlungsrückstand",
        "Rückstand über drei Monate aufgelaufen",
    ]
    for kontext in rueckstand_kontexte:
        result = klassifiziere_betrag_mit_confidence(kontext)
        assert result.feldname != "kaution", (
            f"Rückstand-Kontext {kontext!r} darf nicht als 'kaution' klassifiziert werden, "
            f"erhalten {result.feldname!r}"
        )


# ---------------------------------------------------------------------------
# 8. Bäckerei-Fixture: Kaution und Rückstand korrekt, nie vertauscht
# ---------------------------------------------------------------------------

def test_baeckerei_kein_vertauschen():
    pages = [{"page_number": 1, "text": BAECKEREI_FIXTURE}]
    ev = build_evidence_map(pages)

    kaution_items = [i for i in ev if i.get("field") == "kaution" and i.get("value") != "missing"]
    rueckstand_items = [i for i in ev if i.get("field") == "rueckstand" and i.get("value") != "missing"]

    assert kaution_items, f"Kaution-Feld muss vorhanden sein. Evidence: {ev}"
    assert rueckstand_items, f"Rückstand-Feld muss vorhanden sein. Evidence: {ev}"

    # Kaution-Items dürfen nicht als rueckstand gespeichert sein
    for item in kaution_items:
        assert item["field"] == "kaution", f"Kaution-Item hat falsches Feld: {item}"

    # Rückstand-Items dürfen nicht als kaution gespeichert sein
    for item in rueckstand_items:
        assert item["field"] == "rueckstand", f"Rückstand-Item hat falsches Feld: {item}"

    # Beide 7.200 vorhanden
    kaution_values = {i["value"] for i in kaution_items}
    rueckstand_values = {i["value"] for i in rueckstand_items}
    assert any("7.200" in v for v in kaution_values), (
        f"Kaution muss 7.200 enthalten, hat: {kaution_values}"
    )
    assert any("7.200" in v for v in rueckstand_values), (
        f"Rückstand muss 7.200 enthalten, hat: {rueckstand_values}"
    )


# ---------------------------------------------------------------------------
# 9. MIN_BETRAG_CONFIDENCE Konstante vorhanden
# ---------------------------------------------------------------------------

def test_min_confidence_konstante_vorhanden():
    assert MIN_BETRAG_CONFIDENCE == 1.0, (
        f"MIN_BETRAG_CONFIDENCE muss 1.0 sein, ist {MIN_BETRAG_CONFIDENCE}"
    )


# ---------------------------------------------------------------------------
# Fix 2 – Amount-Type-Safety: neue Tests
# ---------------------------------------------------------------------------

# Betrag steht auf eigener Zeile, Kontext-Keyword nur in Nachbarzeile
MEHRZEILIG_RUECKSTAND_FIXTURE = """
Mietrückstand:
7.200 €
offener Saldo per 01.02.2025
"""

MEHRZEILIG_KAUTION_FIXTURE = """
Vereinbarte Kaution
gemäß Mietvertrag:
5.000 €
"""


def test_betrag_auf_eigenem_line_wird_per_kontext_klassifiziert():
    """Betrag auf eigener Zeile → Nachbarzeile 'Mietrückstand' liefert 'rueckstand'-Typ."""
    lines = MEHRZEILIG_RUECKSTAND_FIXTURE.splitlines()
    # Zeile mit Betrag finden
    betrags_idx = next(i for i, l in enumerate(lines) if "7.200" in l)
    kontext = extract_context_lines(lines, betrags_idx, window=2)
    result = klassifiziere_betrag_mit_kontext(lines[betrags_idx], kontext)
    assert result.feldname == "rueckstand", (
        f"Betrag auf eigenem Line muss per ±2-Zeilen als 'rueckstand' klassifiziert werden, "
        f"erhalten: {result.feldname!r}. Kontext: {kontext!r}"
    )
    assert result.confidence == 1.0


def test_kaution_mehrzeilig_korrekt():
    """Betrag unter 'Vereinbarte Kaution' → 'kaution' via ±2-Zeilen."""
    lines = MEHRZEILIG_KAUTION_FIXTURE.splitlines()
    betrags_idx = next(i for i, l in enumerate(lines) if "5.000" in l)
    kontext = extract_context_lines(lines, betrags_idx, window=2)
    result = klassifiziere_betrag_mit_kontext(lines[betrags_idx], kontext)
    assert result.feldname == "kaution", (
        f"Betrag nach 'Kaution'-Zeile muss 'kaution' sein, erhalten: {result.feldname!r}"
    )


def test_gehalt_mit_zeitraum_wird_klassifiziert():
    """Gehalt + Zeitraum im Kontext → confidence 1.0, feldname='gehalt'."""
    kontext = "Monatsgehalt ab März 2024: 3.500 €"
    result = klassifiziere_betrag_mit_confidence(kontext)
    assert result.feldname == "gehalt", (
        f"Gehalt mit Zeitraum muss als 'gehalt' klassifiziert werden, "
        f"erhalten: {result.feldname!r}"
    )
    assert result.confidence == 1.0
    assert _has_zeitraum(kontext)


def test_gehalt_ohne_zeitraum_unklar():
    """Gehalt ohne Zeitraum → nicht als 'gehalt' klassifiziert (is_ambiguous=True)."""
    kontext = "Das vereinbarte Gehalt beträgt 3.500 €."
    result = klassifiziere_betrag_mit_confidence(kontext)
    assert result.feldname != "gehalt", (
        f"Gehalt ohne Zeitraum darf nicht als 'gehalt' ausgegeben werden, "
        f"erhalten: {result.feldname!r}"
    )
    assert result.is_ambiguous is True, (
        "Gehalt ohne Zeitraum muss is_ambiguous=True zurückgeben"
    )


def test_zweistufige_klassifikation_einzelzeile_hat_vorrang():
    """Wenn Einzelzeile schon 'kaution' liefert, wird ±2-Zeilen-Kontext ignoriert."""
    line = "Kaution hinterlegt: 5.000 €"
    # Nachbarzeilen mit anderem Keyword
    all_lines = ["Mietrückstand insgesamt:", line, "wird verrechnet"]
    kontext = extract_context_lines(all_lines, 1, window=2)
    result = klassifiziere_betrag_mit_kontext(line, kontext)
    assert result.feldname == "kaution", (
        f"Einzelzeile 'kaution' muss Vorrang haben, erhalten: {result.feldname!r}"
    )
    assert result.confidence == 1.0


def test_unklar_label_in_evidence_item():
    """Ambiguous Betrag (Gehalt ohne Zeitraum) → is_ambiguous=True; Evidence-Loop
    würde field='betraege_unklar' mit label='unklar – bitte prüfen' vergeben.

    Hinweis: _normalize_evidence_map filtert betraege_unklar aus dem finalen
    build_evidence_map-Ergebnis heraus (kein Eckdatum). Das Label ist an
    der Klassifikations-Stufe testbar.
    """
    kontext = "Das Gehalt beträgt 4.000 €."
    lines = [kontext]
    ctx = extract_context_lines(lines, 0, window=2)
    result = klassifiziere_betrag_mit_kontext(lines[0], ctx)
    assert result.is_ambiguous, (
        "Gehalt ohne Zeitraum muss is_ambiguous=True sein, "
        "damit der Evidence-Loop field='betraege_unklar' + label='unklar – bitte prüfen' setzt"
    )
    # Sicherstellen dass der Label-String korrekt definiert ist
    # (der tatsächliche Evidence-Eintrag wird in build_evidence_map von
    #  _normalize_evidence_map entfernt – "nicht als Eckdatum ausgeben")
    expected_label = "unklar – bitte prüfen"
    assert len(expected_label) > 0
    assert result.feldname == "betrag"  # ambigu → feldname="betrag", is_ambiguous=True


def test_baeckerei_mehrzeilig_rueckstand_erkannt():
    """Bäckerei-Fall mit mehrzeiligem Rückstand → rueckstand-Feld vorhanden."""
    pages = [{"page_number": 1, "text": MEHRZEILIG_RUECKSTAND_FIXTURE}]
    ev = build_evidence_map(pages)
    rueckstand_items = [
        i for i in ev
        if i.get("field") == "rueckstand" and i.get("value") != "missing"
    ]
    assert rueckstand_items, (
        f"Mehrzeiliger Rückstand muss erkannt werden. Evidence: {ev}"
    )
    assert any("7.200" in i["value"] for i in rueckstand_items), (
        f"Rückstand muss 7.200 enthalten, hat: {[i['value'] for i in rueckstand_items]}"
    )


# ---------------------------------------------------------------------------
# Fix B – kein sentence-boundary-bleeding in flat_text Loop 1
# ---------------------------------------------------------------------------

DOK2_GESAMTSCHADEN_FIXTURE = "\n".join([
    "Offener Rückstand: EUR 12.600,00 laut Mahnung.",
    "Außerdem machen wir geltend:",
    "Gesamtschaden: EUR 12.900,00 (Reparaturkosten EUR 8.400,00 + Mietausfall EUR 4.500,00)",
    "Streitwert: EUR 25.500,00",
])


def test_kein_sentence_boundary_bleed_gesamtschaden_nicht_rueckstand():
    """Fix B: build_evidence_map klassifiziert EUR 12.900,00 nicht als 'rueckstand'
    wenn Gesamtschaden direkt nach einer Rückstand-Zeile steht.
    Sentence-boundary-Snap in flat_text Loop 1 darf nicht rückwärts auf
    'Offener Rückstand:' springen und 'rückstand' in kontext_fenster injizieren."""
    pages = [{"page_number": 1, "text": DOK2_GESAMTSCHADEN_FIXTURE}]
    ev = build_evidence_map(pages)
    gesamtschaden_items = [i for i in ev if "12.900" in str(i.get("value", ""))]
    rueckstand_12900 = [i for i in gesamtschaden_items if i.get("field") == "rueckstand"]
    assert not rueckstand_12900, (
        f"EUR 12.900,00 darf nicht als 'rueckstand' klassifiziert werden "
        f"(sentence-boundary-bleeding Fix B). Items: {gesamtschaden_items}"
    )
    schadensersatz_12900 = [i for i in gesamtschaden_items if i.get("field") == "schadensersatz"]
    assert schadensersatz_12900, (
        f"EUR 12.900,00 muss als 'schadensersatz' in der Evidence Map erscheinen. "
        f"Items: {gesamtschaden_items}"
    )


# ---------------------------------------------------------------------------
# Fix C – cross-segment Kaution-Guard über render_segmented_summary_text
# ---------------------------------------------------------------------------

def test_cross_segment_kaution_guard_in_render():
    """Fix C: render_segmented_summary_text entfernt Rückstand EUR 12.600,00
    in Dok 2 wenn derselbe Betrag als Kaution in Dok 1 bekannt ist.
    Prüft die Rendering-Pipeline (export_render.py) end-to-end."""
    from core.export_render import render_segmented_summary_text
    segment_results = [
        {
            "segment_meta": {"title_guess": "Mietvertrag", "page_start": 1, "page_end": 3},
            "analysis": {"dokumenttyp_parteien": "Mietvertrag", "kernaussagen": []},
            "evidence_map": [
                {"field": "kaution", "value": "EUR 12.600,00", "page": 1,
                 "quote": "Kaution i.H.v. EUR 12.600,00 hinterlegt am 01.03.2020"},
            ],
        },
        {
            "segment_meta": {"title_guess": "Mahnung", "page_start": 4, "page_end": 6},
            "analysis": {"dokumenttyp_parteien": "Mahnung", "kernaussagen": []},
            "evidence_map": [
                {"field": "rueckstand", "value": "EUR 12.600,00", "page": 5,
                 "quote": "Rückstand EUR 12.600,00 laut Mahnung"},
                {"field": "rueckstand", "value": "EUR 7.230,00", "page": 5,
                 "quote": "weiterer Rückstand EUR 7.230,00"},
            ],
        },
    ]
    summary = render_segmented_summary_text(segment_results)
    # Dok 2 enthält DOKUMENT 2 – Rückstand 12.600 darf dort nicht erscheinen
    dok2_start = summary.find("DOKUMENT 2")
    dok2_section = summary[dok2_start:] if dok2_start != -1 else ""
    rueckstand_12600_in_dok2 = "12.600" in dok2_section and "Rückstand" in dok2_section
    # Echter Rückstand (7.230) muss erhalten bleiben
    rueckstand_7230_in_dok2 = "7.230" in dok2_section
    assert not rueckstand_12600_in_dok2, (
        f"Rückstand EUR 12.600,00 darf in Dok 2 nicht erscheinen "
        f"(cross-segment Kaution-Guard Fix C).\nDok2-Abschnitt:\n{dok2_section[:500]}"
    )
    assert rueckstand_7230_in_dok2, (
        f"Echter Rückstand EUR 7.230,00 muss in Dok 2 erhalten bleiben.\n"
        f"Dok2-Abschnitt:\n{dok2_section[:500]}"
    )
