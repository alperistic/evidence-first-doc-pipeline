"""core/doctype_classifier.py – Rule-based document-type classification.

Extracted from core/segmenter.py (W2 god-file split).
Contains keyword dicts, priority order, the "Urteil" (judgment) gate, and
the public functions detect_doctype_by_keywords() and
classify_doc_type_with_result().
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Keyword dicts for score-based heuristic (segment_pages / _keyword_score)
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
# Rule-based document-type detection matrix.
# More specific keywords with a priority order – prevents misclassification,
# e.g. a termination letter (Kündigung) being misread as an employment
# contract (Arbeitsvertrag), or a generic letter (Schreiben) as such.
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
    # More specific types first – prevents a generic "Schreiben" (letter)
    # match from overriding a more specific "Arbeitsvertrag" match
    "urteil",
    "klageschrift",
    "schriftsatz",  # fallback for court documents without a judgment marker
    "kuendigung",
    "vergleich",
    "mahnung",
    "arbeitsvertrag",
    "mietvertrag",
    "kaufvertrag",
    "schreiben",  # most generic type last
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
# "Urteil" (judgment) marker gate.
# "Urteil" is only assigned if >= _URTEIL_MIN_HARD_MARKERS of these patterns
# are found in the text. Prevents misclassification of letters/briefs that
# happen to contain the word "Urteil" incidentally.
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

# Required marker (§ 311 ZPO, German civil procedure code): the phrase
# "Im Namen des Volkes" ("In the name of the people") appears exclusively
# in actual judgments – not in briefs that merely reference a judgment.
# Must be present in addition to the marker count.
_URTEIL_REQUIRED_MARKER: re.Pattern[str] = _URTEIL_HARD_MARKERS[0]


def _count_urteil_hard_markers(text: str) -> int:
    """Counts how many hard "Urteil" markers are present in the text."""
    return sum(1 for pat in _URTEIL_HARD_MARKERS if pat.search(text))


@dataclass
class DocTypeResult:
    """Document-type classification result with method and confidence flag.

    method:
      "keyword_rule" – rule-based detection (DOCTYPE_KEYWORDS)
      "llm"          – score-based heuristic produced a match
      "fallback"     – no match, returns "Unklar" (unclear)
    confidence:
      "high" – keyword_rule match
      "low"  – llm or fallback
    """

    doc_type: str
    method: str      # "keyword_rule" | "llm" | "fallback"
    confidence: str  # "high" | "low"


def detect_doctype_by_keywords(text: str) -> str | None:
    """Rule-based document-type detection.

    Returns the doc-type key (e.g. 'kuendigung') or None if there is no match.
    Priority order: the most specific type wins.

    "Urteil" gate: "urteil" is only assigned if (a) "Im Namen des Volkes" is
    present as the required marker (§ 311 ZPO) AND (b) >= _URTEIL_MIN_HARD_MARKERS
    hard markers are counted. Briefs that merely reference judgments rarely
    contain the required marker and are correctly classified further down
    the priority chain.
    """
    text_lower = text.lower()
    for doctype in DOCTYPE_PRIORITY:
        keywords = DOCTYPE_KEYWORDS.get(doctype, [])
        if any(kw in text_lower for kw in keywords):
            if doctype == "klageschrift":
                # "klageschrift" alone also matches TOC entries like
                # "Klageschrift (Entwurf) – Zahlungsklage" – at least one
                # additional specific keyword must confirm the content.
                other_kw = [kw for kw in keywords if kw != "klageschrift"]
                if not any(kw in text_lower for kw in other_kw):
                    continue
            if doctype == "urteil":
                if not _URTEIL_REQUIRED_MARKER.search(text):
                    continue  # required marker missing → not a judgment
                if _count_urteil_hard_markers(text) < _URTEIL_MIN_HARD_MARKERS:
                    continue  # marker gate not passed → keep checking
            return doctype
    return None


def _guess_doc_type_from_text(text: str, title_hint: str = "") -> str:
    """Score-based document-type heuristic, with rule-based detection taking precedence."""
    combined = f"{title_hint}\n{text}"
    # Rule-based detection takes precedence over the score-based heuristic
    detected_key = detect_doctype_by_keywords(combined)
    if detected_key is not None:
        return _DOCTYPE_KEY_TO_LABEL.get(detected_key, detected_key)
    # Fallback: score-based heuristic (previous behavior)
    combined_lower = combined.lower()
    scores: dict[str, int] = {}
    for doc_type, keywords in _DOC_TYPE_KEYWORDS.items():
        scores[doc_type] = sum(1 for kw in keywords if kw in combined_lower)
    best_doc, best_score = max(scores.items(), key=lambda item: item[1])
    return best_doc if best_score > 0 else "Unklar"


def classify_doc_type_with_result(text: str, title_hint: str = "") -> DocTypeResult:
    """Document-type classification with method + confidence flag.

    First attempts rule-based detection; on a match confidence='high'.
    Falls back to the score-based heuristic; confidence='low'.
    """
    combined = f"{title_hint}\n{text}" if title_hint else text
    detected_key = detect_doctype_by_keywords(combined)
    if detected_key is not None:
        label = _DOCTYPE_KEY_TO_LABEL.get(detected_key, detected_key)
        return DocTypeResult(doc_type=label, method="keyword_rule", confidence="high")
    # Fallback: score-based heuristic
    doc_type = _guess_doc_type_from_text(text, title_hint=title_hint)
    method = "llm" if doc_type != "Unklar" else "fallback"
    return DocTypeResult(doc_type=doc_type, method=method, confidence="low")
