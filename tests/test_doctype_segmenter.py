"""test_doctype_segmenter.py – Regelbasierte DocType-Erkennung.

Stellt sicher, dass Kündigung und Schreiben nicht mehr als Arbeitsvertrag
klassifiziert werden und die Priority-Reihenfolge korrekt greift.
"""

import pytest

from core.segmenter import (
    DOCTYPE_KEYWORDS,
    DOCTYPE_PRIORITY,
    DocTypeResult,
    classify_doc_type_with_result,
    detect_doctype_by_keywords,
    _count_urteil_hard_markers,
    _URTEIL_MIN_HARD_MARKERS,
    _URTEIL_REQUIRED_MARKER,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

KUENDIGUNG_FIXTURE = """
Sehr geehrte Damen und Herren,
hiermit kündige ich das bestehende Arbeitsverhältnis fristlos
zum nächstmöglichen Termin.
Mit freundlichen Grüßen
"""

ARBEITSVERTRAG_FIXTURE = """
Arbeitsvertrag zwischen Arbeitgeber GmbH und Herrn Max Mustermann.
Das Arbeitsverhältnis wird begründet ab 01.03.2024.
Die Vergütung beträgt 3.500 € monatlich.
Die Probezeit beträgt 6 Monate.
"""

SCHREIBEN_FIXTURE = """
Sehr geehrter Herr Mustermann,
wir teilen Ihnen mit, dass Ihre Anfrage eingegangen ist.
Mit freundlichen Grüßen
Kanzlei Beispiel
"""

URTEIL_FIXTURE = """
Im Namen des Volkes
Urteil
Tenor:
Die Klage wird abgewiesen.
Entscheidungsgründe:
"""

BERGMANN_FIXTURE = """
Sehr geehrte Frau Bergmann,
hiermit kündige ich das Arbeitsverhältnis ordentlich.
Bitte bestätigen Sie den Eingang dieses Kündigungsschreibens.
Mit freundlichen Grüßen
"""

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_kuendigung_nicht_als_arbeitsvertrag():
    """KUENDIGUNG_FIXTURE muss als 'kuendigung' erkannt werden, nicht als 'arbeitsvertrag'."""
    result = detect_doctype_by_keywords(KUENDIGUNG_FIXTURE)
    assert result == "kuendigung", f"Erwartet 'kuendigung', erhalten: {result!r}"
    assert result != "arbeitsvertrag"


def test_arbeitsvertrag_korrekt_erkannt():
    """ARBEITSVERTRAG_FIXTURE muss als 'arbeitsvertrag' erkannt werden."""
    result = detect_doctype_by_keywords(ARBEITSVERTRAG_FIXTURE)
    assert result == "arbeitsvertrag", f"Erwartet 'arbeitsvertrag', erhalten: {result!r}"


def test_schreiben_korrekt_erkannt():
    """SCHREIBEN_FIXTURE muss als 'schreiben' erkannt werden."""
    result = detect_doctype_by_keywords(SCHREIBEN_FIXTURE)
    assert result == "schreiben", f"Erwartet 'schreiben', erhalten: {result!r}"


def test_urteil_korrekt_erkannt():
    """URTEIL_FIXTURE muss als 'urteil' erkannt werden."""
    result = detect_doctype_by_keywords(URTEIL_FIXTURE)
    assert result == "urteil", f"Erwartet 'urteil', erhalten: {result!r}"


def test_spezifischer_typ_gewinnt_bei_overlap():
    """Text mit 'kündigung' + 'sehr geehrte' → 'kuendigung' (nicht 'schreiben')."""
    text = "Sehr geehrte Damen und Herren, hiermit kündige ich fristlos."
    result = detect_doctype_by_keywords(text)
    assert result == "kuendigung", f"Erwartet 'kuendigung', erhalten: {result!r}"
    assert result != "schreiben"


def test_kein_treffer_gibt_none():
    """Leerer Text oder unbekannter Text → None."""
    assert detect_doctype_by_keywords("") is None
    assert detect_doctype_by_keywords("Völlig unbekannter Inhalt ohne relevante Schlüsselwörter.") is None


def test_method_flag_keyword_rule():
    """Keyword-Treffer → method == 'keyword_rule', confidence == 'high'."""
    result = classify_doc_type_with_result(KUENDIGUNG_FIXTURE)
    assert isinstance(result, DocTypeResult)
    assert result.method == "keyword_rule"
    assert result.confidence == "high"
    assert result.doc_type == "Kündigung"


def test_method_flag_llm_fallback():
    """Kein Keyword-Treffer → method != 'keyword_rule'."""
    text = "Völlig neutraler Text ohne jegliche Dokument-Keywords."
    result = classify_doc_type_with_result(text)
    assert isinstance(result, DocTypeResult)
    assert result.method != "keyword_rule"
    assert result.confidence == "low"


def test_bergmann_kuendigung_erkannt():
    """Bergmann-ähnlicher Text mit Kündigungsinhalt → 'kuendigung', nicht 'arbeitsvertrag'."""
    result = detect_doctype_by_keywords(BERGMANN_FIXTURE)
    assert result == "kuendigung", f"Erwartet 'kuendigung', erhalten: {result!r}"
    assert result != "arbeitsvertrag"


def test_priority_reihenfolge():
    """'urteil' Keywords + 'schreiben' Keywords im selben Text → 'urteil' gewinnt."""
    text = (
        "Im Namen des Volkes\n"
        "Urteil\n"
        "Tenor: Die Klage wird abgewiesen.\n"
        "Sehr geehrte Damen und Herren, mit freundlichen Grüßen."
    )
    result = detect_doctype_by_keywords(text)
    assert result == "urteil", f"Erwartet 'urteil', erhalten: {result!r}"
    assert result != "schreiben"


# ---------------------------------------------------------------------------
# Fix 1 – Urteil-Marker-Gate: neue Tests
# ---------------------------------------------------------------------------

# Nur das Wort "Urteil" + 1 Marker → Gate nicht bestanden
URTEIL_OHNE_MARKER_FIXTURE = """
Urteil in dem Verfahren vor dem Amtsgericht Karlsruhe
Tenor: Die Klage wird abgewiesen.
Die Klage gegen die Beklagte ist unbegründet.
"""

# Genau 2 harte Marker → Gate bestanden
URTEIL_ZWEI_MARKER_FIXTURE = """
Im Namen des Volkes
Tatbestand:
Der Kläger begehrt Zahlung von 5.000 €.
Die Beklagte bestreitet dies.
"""

# Klageschrift-DocType-Fall (Bäckerei-ähnlich)
KLAGESCHRIFT_BAECKEREI_FIXTURE = """
An das Amtsgericht Karlsruhe
Klageschrift
Kläger: Bäckerei Goldkruste GmbH, Marktplatz 5, 76133 Karlsruhe
Beklagte: Vermieter GmbH, Hauptstraße 1, 76131 Karlsruhe
Streitwert wird auf 7.200 € festgesetzt.
Kläger beantragt: Rückzahlung der Kaution in Höhe von 7.200 €.
"""


def test_urteil_ohne_ausreichende_marker_kein_urteil():
    """Urteil-Keyword + nur 1 harter Marker → Gate nicht bestanden, kein 'urteil'."""
    result = detect_doctype_by_keywords(URTEIL_OHNE_MARKER_FIXTURE)
    assert result != "urteil", (
        f"Ohne 2 harte Marker darf 'urteil' nicht vergeben werden, erhalten: {result!r}"
    )
    # Marker-Zählung direkt prüfen
    count = _count_urteil_hard_markers(URTEIL_OHNE_MARKER_FIXTURE)
    assert count < _URTEIL_MIN_HARD_MARKERS, (
        f"Erwarte < {_URTEIL_MIN_HARD_MARKERS} Marker, gezählt: {count}"
    )


def test_urteil_genau_zwei_marker_reicht():
    """Genau 2 harte Marker (Im Namen des Volkes + Tatbestand) → 'urteil'."""
    result = detect_doctype_by_keywords(URTEIL_ZWEI_MARKER_FIXTURE)
    assert result == "urteil", (
        f"Mit genau 2 harten Markern muss 'urteil' erkannt werden, erhalten: {result!r}"
    )
    count = _count_urteil_hard_markers(URTEIL_ZWEI_MARKER_FIXTURE)
    assert count >= _URTEIL_MIN_HARD_MARKERS, (
        f"Erwarte ≥ {_URTEIL_MIN_HARD_MARKERS} Marker, gezählt: {count}"
    )


def test_klageschrift_baeckerei_korrekt_erkannt():
    """Klageschrift (Bäckerei-Mietstreit) → 'klageschrift', niemals 'urteil'."""
    result = detect_doctype_by_keywords(KLAGESCHRIFT_BAECKEREI_FIXTURE)
    assert result == "klageschrift", (
        f"Klageschrift muss als 'klageschrift' erkannt werden, erhalten: {result!r}"
    )
    assert result != "urteil"
    # Kein Urteil-Marker vorhanden
    assert _count_urteil_hard_markers(KLAGESCHRIFT_BAECKEREI_FIXTURE) < _URTEIL_MIN_HARD_MARKERS


# ---------------------------------------------------------------------------
# Bug2-Regression: Gewerbemietvertrag + Kanzleiakte
# ---------------------------------------------------------------------------

GEWERBEMIETVERTRAG_FIXTURE = """
Gewerbemietvertrag

§ 1 Mietsache
Der Vermieter vermietet an den Mieter die Gewerbeeinheit im Erdgeschoss,
Mietfläche ca. 120 m², für gewerbliche Nutzung als Büro.

§ 2 Mietzeit und Mietzins
Das Mietverhältnis beginnt am 01.04.2024. Die monatliche Miete beträgt
2.400,00 € zuzüglich Nebenkosten.

§ 3 Kaution
Der Mieter hinterlegt eine Kaution in Höhe von drei Monatsmieten.

§ 10 Streitigkeiten
Im Streitfall können die Vertragsparteien Klage erheben. Vor Klageerhebung
ist eine außergerichtliche Einigung zu versuchen. Klagen sind ausschließlich
am Gericht des Vermietersitzes einzureichen.
"""

KANZLEIAKTE_SCHRIFTSATZ_FIXTURE = """
Schriftsatz

In dem Rechtsstreit
Klägerin ./. Beklagte

Namens und in Vollmacht der Klägerin nehmen wir wie folgt Stellung:

I. Zum Tatbestand des zitierten Urteils des LG Frankfurt (Az. 1 O 123/23)

Das Landgericht hat in seiner Entscheidung vom 15.01.2024 die
Entscheidungsgründe wie folgt ausgeführt: [...]

II. Begründung

Es wird beantragt, das vorgenannte Urteil aufzuheben, da die Entscheidungsgründe
des Vorgerichts die gesetzlichen Anforderungen nicht erfüllen.
"""


def test_gewerbemietvertrag_nicht_als_klageschrift(
):
    """Gewerbemietvertrag mit 'Klage erheben'-Klausel → 'mietvertrag', nicht 'klageschrift'.

    Bug2-Regression: 'klage' war zu generisch in DOCTYPE_KEYWORDS['klageschrift']
    und hatte Priorität 2 gegenüber 'mietvertrag' auf Platz 8.
    """
    result = detect_doctype_by_keywords(GEWERBEMIETVERTRAG_FIXTURE)
    assert result == "mietvertrag", (
        f"Gewerbemietvertrag muss als 'mietvertrag' erkannt werden, erhalten: {result!r}"
    )
    assert result != "klageschrift"


def test_kanzleiakte_schriftsatz_nicht_als_urteil():
    """Schriftsatz mit Tatbestand+Entscheidungsgründe-Referenzen ohne 'Im Namen des Volkes'
    → 'schriftsatz', nicht 'urteil'.

    Bug2-Regression: Kanzleiakten mit zitierten Urteilspassagen hatten ≥ 2 harte Marker
    und passierten das Gate, obwohl kein echtes Urteil vorlag.
    """
    result = detect_doctype_by_keywords(KANZLEIAKTE_SCHRIFTSATZ_FIXTURE)
    assert result != "urteil", (
        f"Kanzleiakte darf ohne 'Im Namen des Volkes' nicht als 'urteil' klassifiziert werden, "
        f"erhalten: {result!r}"
    )
    assert result == "schriftsatz", (
        f"Kanzleiakte mit Schriftsatz-Inhalt muss als 'schriftsatz' erkannt werden, "
        f"erhalten: {result!r}"
    )
    # Pflicht-Marker darf nicht vorhanden sein
    assert not _URTEIL_REQUIRED_MARKER.search(KANZLEIAKTE_SCHRIFTSATZ_FIXTURE), (
        "'Im Namen des Volkes' darf in diesem Fixture nicht vorkommen"
    )


# ---------------------------------------------------------------------------
# Bug2-Regression: Klageschrift-TOC-Gate
# ---------------------------------------------------------------------------

TOC_KLAGESCHRIFT_FIXTURE = """
Inhaltsübersicht

1. Gewerbemietvertrag (01.09.2022) .......... S. 3
2. Klageschrift (Entwurf) – Zahlungsklage .. S. 12
3. Mahnung vom 14.02.2024 .................. S. 18
4. Kontoauszüge ............................. S. 22

Mietzins: 2.400,00 € monatlich, Mietobjekt: Gewerbeeinheit EG,
Mietbeginn: 01.09.2022, Nebenkosten gemäß Abrechnung.
"""


def test_toc_klageschrift_eintrag_nicht_als_klageschrift():
    """TOC-Listeneintrag 'Klageschrift (Entwurf)' ohne corroborating Keywords →
    darf nicht als 'klageschrift' klassifiziert werden.

    Bug2-Regression: "klageschrift" allein trifft auf TOC-Einträge. Gate erfordert
    mindestens ein weiteres spezifisches Keyword (klageanspruch / kläger beantragt /
    streitwert wird).
    """
    result = detect_doctype_by_keywords(TOC_KLAGESCHRIFT_FIXTURE)
    assert result != "klageschrift", (
        f"TOC-Listeneintrag darf nicht als 'klageschrift' klassifiziert werden, "
        f"erhalten: {result!r}"
    )


def test_klageschrift_baeckerei_gate_besteht_weiterhin():
    """Regression: KLAGESCHRIFT_BAECKEREI_FIXTURE hat 'kläger beantragt' +
    'streitwert wird' → Gate besteht, Klassifikation bleibt 'klageschrift'.
    """
    result = detect_doctype_by_keywords(KLAGESCHRIFT_BAECKEREI_FIXTURE)
    assert result == "klageschrift", (
        f"KLAGESCHRIFT_BAECKEREI_FIXTURE muss weiterhin 'klageschrift' ergeben, "
        f"erhalten: {result!r}"
    )
