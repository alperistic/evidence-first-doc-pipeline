"""core/doctype_classifier.py – Regelbasierte DocType-Klassifikation.

Extrahiert aus core/segmenter.py (W2 God-File-Split).
Enthält Keyword-Dicts, Prioritätsreihenfolge, Urteil-Gate und
die öffentlichen Funktionen detect_doctype_by_keywords() und
classify_doc_type_with_result().
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Keyword-Dicts für score-basierte Heuristik (segment_pages / _keyword_score)
# ---------------------------------------------------------------------------

_DOC_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Klageschrift": ("klageschrift", "klage", "antrag", "beklagte", "gericht"),
    "Mahnung": ("mahnung", "forderungsschreiben", "zahlungsfrist", "forderung", "zahlungsaufforderung"),
    "Abmahnung": ("abmahnung", "unterlassung", "vertragsstrafe"),
    "Mietvertrag": ("mietvertrag", "wohnraummietvertrag", "mietsache", "miete", "nebenkosten", "kaution"),
    "Arbeitsvertrag": ("arbeitsvertrag", "arbeitnehmer", "arbeitgeber", "probezeit", "gehalt"),
    "Kaufvertrag": ("kaufvertrag", "kaufpreis", "fahrzeug", "übergabe"),
    "Ratenzahlungsvereinbarung": (
        "ratenzahlungsvereinbarung",
        "ratenzahlung",
        "zahlungsplan",
        "ratenhöhe",
    ),
    "Vergleich": ("vergleich", "vergleichsvereinbarung", "vergleichsbetrag"),
    "Abnahmeprotokoll": ("abnahmeprotokoll", "abnahme", "mängelliste"),
    "Preisblatt": ("preisblatt", "preisübersicht", "preisverzeichnis"),
    "SLA": ("sla", "service level agreement", "service-level"),
    "Auftragsverarbeitung": ("auftragsverarbeitung", "verarbeitung personenbezogener daten", "dpa", "avv"),
    "Anlage": ("anlage", "anlagen", "anlage a", "anlage 1", "anlagenpaket"),
    "Schriftsatz": ("schriftsatz", "begründung", "stellungnahme"),
    "Vertrag": ("vertrag", "vertragsparteien", "zwischen", "rahmenvertrag", "it-dienstleistungen"),
    "Testament": ("testament", "letzter wille", "erblasser", "vermächtnis", "letztwillige verfügung"),
    "OWi-Einspruch": ("bußgeldbescheid", "ordnungswidrigkeit", "einspruch", "owi"),
    "Pflichtteilsberechnung": ("pflichtteil", "pflichtteilsanspruch", "erbschaft", "nachlassverzeichnis"),
    "Scheidungsfolgenvereinbarung": ("scheidungsfolgenvereinbarung", "scheidung", "ehevertrag"),
}

# ---------------------------------------------------------------------------
# Regelbasierte DocType-Erkennungs-Matrix
# Spezifischere Keywords mit Prioritätsreihenfolge – verhindert Fehlklassifikation
# z.B. Kündigung → Arbeitsvertrag oder Schreiben → Arbeitsvertrag
# ---------------------------------------------------------------------------

DOCTYPE_KEYWORDS: dict[str, list[str]] = {
    "kuendigung": [
    "kündigungsschreiben",
    "hiermit kündige ich",
    "kündige hiermit",
    "fristlos kündige",
    "kündigung zum",
    ],
    "schreiben": [
        "sehr geehrte", "sehr geehrter", "mit freundlichen grüßen",
        "mfg", "hochachtungsvoll", "betreff:",
    ],
    "arbeitsvertrag": [
        "arbeitsvertrag", "arbeitsverhältnis wird begründet",
        "eingestellt als", "arbeitszeit beträgt",
        "vergütung beträgt", "probezeit",
    ],
    "mietvertrag": [
        "mietvertrag", "mietsache", "mietobjekt",
        "mietzins", "mietzeit", "nebenkosten",
    ],
    "kaufvertrag": [
        "kaufvertrag", "kaufpreis", "übergabe des kaufgegenstands",
        "eigentum geht über",
    ],
    "urteil": [
        "im namen des volkes", "urteil", "tenor:", "tatbestand:",
        "entscheidungsgründe:", "rechtsmittelbelehrung",
    ],
    "klageschrift": [
        "klageschrift", "klageanspruch",
        "kläger beantragt", "streitwert wird",
    ],
    "schriftsatz": [
        "schriftsatz", "begründung", "klageerwiderung",
        "widerklage", "berufung", "revision",
    ],
    "mahnung": [
        "mahnung", "mahnschreiben", "forderungsschreiben",
        "zahlungsaufforderung", "letzte mahnung",
        "ohne weitere ankündigung gerichtliche schritte",
    ],
    "vergleich": [
        "ratenzahlungsvereinbarung", "vergleich",
        "vergleichsvereinbarung", "ratenplan",
        "die schuldnerin erkennt",
    ],
}

DOCTYPE_PRIORITY: list[str] = [
    # Spezifischere Typen zuerst – verhindert dass "Schreiben"
    # einen "Arbeitsvertrag" überschreibt
    "urteil",
    "klageschrift",
    "schriftsatz",  # Fallback für Gerichtsdokumente ohne Urteil-Marker
    "kuendigung",
    "vergleich",
    "mahnung",
    "arbeitsvertrag",
    "mietvertrag",
    "kaufvertrag",
    "schreiben",  # generischster Typ zuletzt
]

_DOCTYPE_KEY_TO_LABEL: dict[str, str] = {
    "kuendigung": "Kündigung",
    "schreiben": "Schreiben",
    "arbeitsvertrag": "Arbeitsvertrag",
    "mietvertrag": "Mietvertrag",
    "kaufvertrag": "Kaufvertrag",
    "urteil": "Urteil",
    "klageschrift": "Klageschrift",
    "schriftsatz": "Schriftsatz",
    "mahnung": "Mahnung",
    "vergleich": "Vergleich",
}

# ---------------------------------------------------------------------------
# Urteil-Marker-Gate
# "Urteil" wird nur vergeben wenn ≥ _URTEIL_MIN_HARD_MARKERS dieser Muster
# im Text gefunden werden. Verhindert Fehlklassifikation bei Briefen/
# Schriftsätzen, die zufällig das Wort "Urteil" enthalten.
# ---------------------------------------------------------------------------
_URTEIL_HARD_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bim\s+namen\s+des\s+volkes\b", re.IGNORECASE),
    re.compile(r"\btenor\b", re.IGNORECASE),
    re.compile(r"\btatbestand\b", re.IGNORECASE),
    re.compile(r"\bentscheidungsgr[üu]nde\b", re.IGNORECASE),
    re.compile(r"\bkostenentscheidung\b", re.IGNORECASE),
    re.compile(r"\bvorl[äa]ufig\s+vollstreckbar\b", re.IGNORECASE),
)
_URTEIL_MIN_HARD_MARKERS = 2

# Pflicht-Marker (§ 311 ZPO): "Im Namen des Volkes" steht ausschließlich
# in echten Urteilen – nicht in Schriftsätzen, die auf Urteile verweisen.
# Muss zusätzlich zum Marker-Count vorhanden sein.
_URTEIL_REQUIRED_MARKER: re.Pattern[str] = _URTEIL_HARD_MARKERS[0]


def _count_urteil_hard_markers(text: str) -> int:
    """Zählt wie viele harte Urteil-Marker im Text vorhanden sind."""
    return sum(1 for pat in _URTEIL_HARD_MARKERS if pat.search(text))


@dataclass
class DocTypeResult:
    """DocType-Klassifikationsergebnis mit Methoden- und Confidence-Flag.

    method:
      "keyword_rule" – regelbasierte Erkennung (DOCTYPE_KEYWORDS)
      "llm"          – score-basierte Heuristik hat Treffer geliefert
      "fallback"     – kein Treffer, Rückgabe "Unklar"
    confidence:
      "high" – keyword_rule-Treffer
      "low"  – llm oder fallback
    """

    doc_type: str
    method: str      # "keyword_rule" | "llm" | "fallback"
    confidence: str  # "high" | "low"


def detect_doctype_by_keywords(text: str) -> str | None:
    """Regelbasierte DocType-Erkennung.

    Gibt den DocType-Key zurück (z.B. 'kuendigung') oder None wenn kein Treffer.
    Priority-Reihenfolge: spezifischster Typ gewinnt.

    Urteil-Gate: "urteil" wird nur vergeben wenn (a) "Im Namen des Volkes"
    als Pflicht-Marker vorhanden ist (§ 311 ZPO) UND (b) ≥ _URTEIL_MIN_HARD_MARKERS
    harte Marker gezählt werden. Schriftsätze, die auf Urteile verweisen,
    enthalten selten den Pflicht-Marker und werden korrekt weiter klassifiziert.
    """
    text_lower = text.lower()
    for doctype in DOCTYPE_PRIORITY:
        keywords = DOCTYPE_KEYWORDS.get(doctype, [])
        if any(kw in text_lower for kw in keywords):
            if doctype == "klageschrift":
                # "klageschrift" allein trifft auf TOC-Einträge wie
                # "Klageschrift (Entwurf) – Zahlungsklage" – mindestens ein
                # weiteres spezifisches Keyword muss den Inhalt bestätigen.
                other_kw = [kw for kw in keywords if kw != "klageschrift"]
                if not any(kw in text_lower for kw in other_kw):
                    continue
            if doctype == "urteil":
                if not _URTEIL_REQUIRED_MARKER.search(text):
                    continue  # Pflicht-Marker fehlt → kein Urteil
                if _count_urteil_hard_markers(text) < _URTEIL_MIN_HARD_MARKERS:
                    continue  # Marker-Gate nicht bestanden → weiter prüfen
            return doctype
    return None


def _guess_doc_type_from_text(text: str, title_hint: str = "") -> str:
    """Score-basierte DocType-Heuristik mit Regelvorrang."""
    combined = f"{title_hint}\n{text}"
    # Regelbasierte Erkennung hat Vorrang vor score-basierter Heuristik
    detected_key = detect_doctype_by_keywords(combined)
    if detected_key is not None:
        return _DOCTYPE_KEY_TO_LABEL.get(detected_key, detected_key)
    # Fallback: score-basierte Heuristik (bisheriges Verhalten)
    combined_lower = combined.lower()
    scores: dict[str, int] = {}
    for doc_type, keywords in _DOC_TYPE_KEYWORDS.items():
        scores[doc_type] = sum(1 for kw in keywords if kw in combined_lower)
    best_doc, best_score = max(scores.items(), key=lambda item: item[1])
    return best_doc if best_score > 0 else "Unklar"


def classify_doc_type_with_result(text: str, title_hint: str = "") -> DocTypeResult:
    """DocType-Klassifikation mit method + confidence Flag.

    Versucht zuerst regelbasierte Erkennung; bei Treffern confidence='high'.
    Fällt auf score-basierte Heuristik zurück; confidence='low'.
    """
    combined = f"{title_hint}\n{text}" if title_hint else text
    detected_key = detect_doctype_by_keywords(combined)
    if detected_key is not None:
        label = _DOCTYPE_KEY_TO_LABEL.get(detected_key, detected_key)
        return DocTypeResult(doc_type=label, method="keyword_rule", confidence="high")
    # Fallback: score-basierte Heuristik
    doc_type = _guess_doc_type_from_text(text, title_hint=title_hint)
    method = "llm" if doc_type != "Unklar" else "fallback"
    return DocTypeResult(doc_type=doc_type, method=method, confidence="low")
