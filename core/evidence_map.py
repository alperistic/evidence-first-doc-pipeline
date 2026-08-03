"""
core/evidence_map.py – Evidence-Map-Konstruktion & Normalisierung
Ausgelagert aus core/evidence.py (W2 – Evidence-Split).
"""

import re
from collections import Counter, defaultdict
from datetime import date as _date
from math import ceil
from typing import Any

from core.config import get_config
from core.models import (
    _normalize_ws,
    _LEADING_ARTIFACT_RE,
    clean_snippet,
)
from core.snippet import (
    _normalize_for_match,
    _select_best_sentence_snippet,
    _clip_quote_200,
    _window_quote,
    _is_meta_block_context,
    _contains_trigger_phrase,
    _normalize_date_to_iso,
    _has_termination_label_trigger,
    _snippet_quality_score,
    _deadline_label_from_context,
    _DEADLINE_TRIGGER_PHRASES,
    _FIELD_SENTENCE_KEYWORDS,
    _GERMAN_MONTH_DATE_RE,
)
from core.labels import (
    klassifiziere_betrag_mit_confidence,
    klassifiziere_betrag_mit_kontext,
    extract_context_lines,
)
from core.party_extractor import (
    _looks_like_person,
    _looks_like_org,
    _is_artifact_value,
    _is_rhetoric_value,
    _extract_party_candidates,
    _dedupe_str_keep_order,
)

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

MIN_BETRAG_CONFIDENCE = 1.0  # Beträge nur bei eindeutiger Klassifikation ausgeben

_REPEATING_EDGE_PAGE_RE = re.compile(
    r"\b(seite|page)\s*\d+(?:\s*(?:/|von|of)\s*\d+)?\b",
    flags=re.IGNORECASE,
)
_REPEATING_EDGE_RATIO_RE = re.compile(r"\b\d+\s*/\s*\d+\b")
_REPEATING_EDGE_DATE_RE = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b")
_REPEATING_EDGE_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")

_REQUIRED_EVIDENCE_FIELDS = (
    "parteien",
    "betraege",
    "fristen_termine",
    "datum_beginn",
    "kuendigung_fristen",
    "urlaub_ueberstunden",
    "wettbewerbsverbot",
    "kaution",
    "miete",
    "nebenkosten",
    "rueckstand",
    "schadensersatz",
    "aktenzeichen",
    "gericht",
    "streitwert",
    "beweismittel",
)
_EVIDENCE_TOPK_BY_FIELD: dict[str, int] = {
    "parteien": 2,
    "betraege": 2,
    "fristen_termine": 2,
    "datum_beginn": 1,
    "kuendigung_fristen": 2,
    "urlaub_ueberstunden": 1,
    "wettbewerbsverbot": 1,
    "kaution": 1,
    "miete": 1,
    "nebenkosten": 1,
    "rueckstand": 2,
    "schadensersatz": 1,
    "aktenzeichen": 1,
    "gericht": 1,
    "streitwert": 1,
    "beweismittel": 5,
}

_DEADLINE_DURATION_RE = re.compile(
    # PR12: inflektierte Formen (Monaten, Tagen, Jahren, Wochen) ebenfalls erfassen
    r"\b\d+\s*(?:monate?n?|wochen?|tage?n?|jahre?n?)\b",
    flags=re.IGNORECASE,
)
# Aktenzeichen-Marker: "Az.", "AZ:", "Aktenzeichen:", "Geschäftszeichen:", "Unser Zeichen:"
# gefolgt vom eigentlichen Aktenzeichen-Wert (alphanumerisch, bis 80 Zeichen)
_AZ_LINE_RE = re.compile(
    r"\b(?:az\.?:?|aktenzeichen\s*:?|gesch[äa]ftszeichen\s*:?|unser\s+zeichen\s*:?)\s*"
    r"([^\n,;(]{3,80})",
    re.IGNORECASE,
)


# FIX 3: Deutsche Stopwörter die KEINE AZ-Bestandteile sind, aber ≤3 Buchstaben
# mit Großbuchstaben haben (z.B. "Der", "Die", "Das", "Ihr", "Bis").
_AZ_STOP_WORDS = frozenset({
    "der", "die", "das", "den", "dem", "des",
    "ein", "eine", "einer", "eines", "einem", "einen",
    "ihr", "bis", "vom", "zum", "zur", "von", "und",
    "mit", "für", "auf", "aus", "bei", "nach", "über",
    "mein", "sein", "kein", "nur", "auch", "noch", "oder",
})


def _clean_az_value(raw: str) -> str:
    """FIX 2: Truncate AZ to just the case-reference tokens, strip trailing freetext.

    Valid AZ components: tokens with digits/slashes/dots/dashes, or 1-3 ALL-UPPERCASE
    abbreviations (like "F", "VI", "OLG"). Stop at the first token that looks like a
    regular German word (3+ chars with lowercase letters).
    FIX 3: Also stop at German stop words (Der, Die, Das, Ihr, Bis, etc.) that
    coincidentally have uppercase letters but are not AZ components.
    """
    if not raw:
        return raw
    tokens = raw.split()
    az_tokens: list[str] = []
    for tok in tokens:
        # Strip trailing punctuation for classification only
        tok_clean = tok.rstrip(".,;:!?")
        # FIX 3: Skip common German words that are not AZ components
        if tok_clean.lower() in _AZ_STOP_WORDS:
            break
        is_az_component = (
            bool(re.search(r"[\d/.\-]", tok_clean))          # contains digit/slash/dot/dash
            or (len(tok_clean) <= 3 and any(c.isupper() for c in tok_clean))  # 1-3 char abbreviation with uppercase
        )
        if is_az_component:
            az_tokens.append(tok)
        else:
            break  # first non-AZ token → stop
    if not az_tokens:
        # Fallback: hard truncate at 40 chars
        return raw[:40].rstrip()
    result = " ".join(az_tokens).rstrip(".,;:!?").strip()
    if len(result) > 40:
        result = result[:40].rstrip()
    return result
# Gericht-Erkennung: Gerichtstyp (explizit case-insensitive im Pattern) + Ortsname
# mit Großbuchstaben-Pflicht. Kein re.IGNORECASE, damit "olg wurde" nicht matcht.
_GERICHT_RE = re.compile(
    r"\b((?:[Aa]mtsgericht|[Ll]andgericht|[Oo]berlandesgericht|OLG|[Bb]undesgerichtshof|BGH"
    r"|[Aa]rbeitsgericht|[Ss]ozialgericht|[Ff]inanzgericht|[Vv]erwaltungsgericht)"
    r"(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+(?:\s+(?:am|an\s+der?)\s+[A-ZÄÖÜ][a-zäöüß]+)?)?)"
)
# PR12 P1: Wortgrenze-bewusstes Kündigung-Pattern — matcht NICHT 'vorankündigung'
_KUENDIGUNG_WORD_RE = re.compile(
    r"\b(?:kündigung|kuendigung)(?:sfrist)?\b",
    re.IGNORECASE,
)
# PR13 P4: Jahreszahl-Erkennung — "2026 EUR" aus Datums-Token ausschließen
_YEAR_AMOUNT_RE = re.compile(r"^(?:19|20)\d{2}$")
# FIX 4: Starke Frist-Trigger — Daten ohne diese Keywords sind keine Fristen
_STRONG_FRIST_TRIGGERS = (
    "frist", "fristablauf", "spätestens", "spaetestens",
    "fällig bis", "faellig bis", "bis spätestens", "bis spaetestens",
    "zahlungsfrist", "zahlungsziel", "zu zahlen bis", "zu zahlen",
    "zahlung bis", "zahlung am", "zahlung",
    "letzte zahlungsaufforderung", "termin", "fällig am", "faellig am",
    "ablauf", "lief ab", "läuft ab", "verstrichen",
)
# FIX 2/6: Negativ-Kontext — diese Keywords signalisieren Geburtsdaten / Eintrittsdaten /
# Gründungsdaten. Solche Daten sind KEINE Fristen, auch wenn "termin" o.ä. im Fenster vorkommt.
_NON_FRIST_CONTEXT_KEYWORDS = (
    "geboren", "geb.", "geburtsdatum", "geburtstag",
    "* ",  # genealogisches Asterisk-Zeichen vor Datum
    "beschäftigt seit", "beschaeftigt seit", "eingestellt am",
    "eintritt", "beschäftigungsbeginn", "beschaeftigungsbeginn",
    "tätig seit", "taetig seit", "arbeitsverhältnis seit", "arbeitsverhaeltnis seit",
    "angestellt seit", "arbeitsbeginn", "seit dem",  # FIX 3: zusätzliche Beschäftigungsbeginn-Keywords
    "in kraft seit",  # FIX 3: Vertragsbeginn-Keyword
    "gegründet", "gegruendet", "gründung", "gruendung",
    "eingetragen", "eintragung", "registriert",
    # FIX 1/2 new: Arbeitsvertrags- und Beschäftigungsbeginn-Signaturen
    "arbeitsvertrag vom", "beginn des arbeitsverhältnisses", "beginn des arbeitsverhaeltnisses",
    "eingetreten am", "trat ein am",
)
# FIX 2: "Vertrag vom" filtert nur für Daten vor 2024; neuere Verträge könnten relevante Fristen sein.
_VERTRAG_VOM_RE = re.compile(r"\bvertrag\s+vom\b", re.IGNORECASE)
_PAST_DATE_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


_FULL_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.((?:19|20)\d{2})\b")


def _is_past_date_no_strong_frist_context(date_value: str, context: str) -> bool:
    """FIX 4 / FIX 2 / FIX 6: Returns True if the date should be filtered (not a Frist).

    Filtering rules (checked in order):
    1. Non-Frist context keywords (birth, employment, founding) → always filter.
    2. Dates before 1960 → always filter.
    3. Past date without a strong Frist trigger keyword → filter.
    Historical expired Fristen ("Frist lief ab am ...") are preserved because they
    contain strong keywords like "frist" or "lief ab".
    """
    # Try to parse full date (dd.mm.yyyy) for accurate past/future check
    full_match = _FULL_DATE_RE.search(date_value)
    if full_match:
        try:
            day, month, year = int(full_match.group(1)), int(full_match.group(2)), int(full_match.group(3))
            is_past = _date(year, month, day) < _date.today()
        except ValueError:
            is_past = False
    else:
        year_match = _PAST_DATE_YEAR_RE.search(date_value)
        if not year_match:
            return False
        year = int(year_match.group(1))
        is_past = year < _date.today().year

    if not is_past:
        return False  # Future/today dates are always kept
    ctx = context.lower()
    # Extract year_val early (needed for year-based checks below)
    if full_match:
        year_val = int(full_match.group(3))
    else:
        year_match2 = _PAST_DATE_YEAR_RE.search(date_value)
        year_val = int(year_match2.group(1)) if year_match2 else 0
    # FIX 2/6: Negative context check — birth/employment/founding dates are NEVER Fristen,
    # regardless of whether "termin" or other soft triggers appear in the same window.
    for neg_kw in _NON_FRIST_CONTEXT_KEYWORDS:
        if neg_kw in ctx:
            return True  # Always filter — not a Frist
    # Dates before 1960 are never deadlines (birth years, founding years, etc.)
    if year_val > 0 and year_val < 1960:
        return True  # Always filter pre-1960 dates regardless of context
    for trigger in _STRONG_FRIST_TRIGGERS:
        if trigger in ctx:
            return False  # Has strong context → keep as Frist
    # FIX 2: "Vertrag vom" — filter old contracts (pre-2024); recent ones (2024+) may be relevant.
    # This check runs after strong triggers so "Frist aus dem Vertrag vom..." is kept.
    if _VERTRAG_VOM_RE.search(ctx) and year_val > 0:
        if year_val < 2024:
            return True   # old contract date without explicit Frist context → filter
        return False      # recent contract date → keep
    return True  # Past date without strong Frist context → filter
# U10: Gesamtzahllast-Erkennung
# Pattern 1 (linker Teil): "betraegt/beträgt" direkt vor dem Betrag
_ZAHLLAST_LEFT_RE = re.compile(r"betr[äa]g[t]\s*$", re.IGNORECASE)
# Pattern 1 (rechter Teil): "fällig jeweils" qualifiziert als periodische Gesamtzahlung
_ZAHLLAST_RIGHT_RE = re.compile(r"\bf[äa]llig\s+jeweils\b", re.IGNORECASE)

_FORBIDDEN_REPLACEMENTS: list[tuple[str, str]] = [
    (r"\brechtlich problematisch\b", "Hinweis (zu prüfen)"),
    (r"\brechtlich korrekt\b", "zu prüfen"),
    (r"\brechtlich zulässig\b", "zu prüfen"),
    (r"\brechtlich unzulässig\b", "zu prüfen"),
    (r"\bgesetzlich\s+erlaubt\b", "zu prüfen"),
    (r"\bgesetzlich\s+verboten\b", "zu prüfen"),
    (r"\bzulässig\b", "zu prüfen"),
    (r"\bunzulässig\b", "zu prüfen"),
    (r"\bwirksam\b", "zu prüfen"),
    (r"\bunwirksam\b", "zu prüfen"),
    (r"\brechtswidrig\b", "zu prüfen"),
    (r"\brechtm[aä][sß]{1,2}ig\b", "zu prüfen"),
    (r"\banspruch\b", "Angabe"),
    (r"\bverjährt\b", "Fristlage zu prüfen"),
    (r"\bmuss\b", "zu prüfen"),
    (r"\bdarf\s+nicht\b", "zu prüfen"),
    (r"\bnur\s+wenn\b", "unter Bedingung (zu prüfen)"),
    (r"\bist\s+verpflichtet\b", "wird im Dokument erwähnt (zu prüfen)"),
    (r"\berfolgsaussicht(?:en)?\b", "offen / unklar / Rückfrage"),
    (r"\baussichten?\b", "offen / unklar / Rückfrage"),
    (r"\bschl[üu]ssig\b", "zu prüfen / Abgleich erforderlich"),
    (r"\bausreichend\b", "zu prüfen / Abgleich erforderlich"),
    (r"\berschweren\b", "offen / unklar / Rückfrage"),
    (r"\bbelegen\s+k[oö]nnen\b", "im Dokument genannt / Beweismittel genannt"),
    (r"\bdurchsetzbar\b", "offen / unklar / Rückfrage"),
    (r"\brechtsfolge(?:n)?\b", "Hinweis"),
]

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def _normalize_repeating_edge_line(line: str) -> str:
    normalized = _normalize_ws(str(line or "")).lower()
    if not normalized:
        return ""
    normalized = _REPEATING_EDGE_PAGE_RE.sub(
        lambda m: re.sub(r"\d+", "<#>", m.group(0).lower()),
        normalized,
    )
    normalized = _REPEATING_EDGE_RATIO_RE.sub("<#>/<#>", normalized)
    normalized = _REPEATING_EDGE_DATE_RE.sub("<date>", normalized)
    normalized = _REPEATING_EDGE_TIME_RE.sub("<time>", normalized)
    return _normalize_ws(normalized)


def strip_repeating_header_footer_lines(
    pages: list[dict[str, Any]],
    *,
    edge_lines: int = 4,
    min_pages: int = 3,
    min_ratio: float = 0.5,
) -> list[dict[str, Any]]:
    copied_pages = [dict(page) for page in list(pages or [])]
    if len(copied_pages) <= 1:
        return copied_pages

    edge_count = max(1, int(edge_lines))
    page_lines: list[list[str]] = []
    edge_line_indexes_per_page: list[set[int]] = []
    edge_norms_per_page: list[set[str]] = []

    for page in copied_pages:
        lines = str(page.get("text", "") or "").splitlines()
        page_lines.append(lines)

        indexed_non_empty = [(idx, line.strip()) for idx, line in enumerate(lines) if line.strip()]
        if len(indexed_non_empty) <= edge_count * 2:
            top_candidates = indexed_non_empty[:1]
            bottom_candidates = indexed_non_empty[-1:]
        else:
            top_candidates = indexed_non_empty[:edge_count]
            bottom_candidates = indexed_non_empty[-edge_count:]
        edge_candidates = top_candidates + bottom_candidates
        edge_line_indexes = {idx for idx, _ in edge_candidates}
        edge_line_indexes_per_page.append(edge_line_indexes)

        edge_norms: set[str] = set()
        for _, candidate in edge_candidates:
            norm = _normalize_repeating_edge_line(candidate)
            if norm:
                edge_norms.add(norm)
        edge_norms_per_page.append(edge_norms)

    threshold = max(int(min_pages), int(ceil(len(copied_pages) * float(min_ratio))))
    recurring_norms = {
        norm
        for norm, count in Counter(
            norm
            for norms in edge_norms_per_page
            for norm in norms
        ).items()
        if count >= threshold
    }
    if not recurring_norms:
        return copied_pages

    cleaned_pages: list[dict[str, Any]] = []
    for idx, page in enumerate(copied_pages):
        lines = page_lines[idx]
        edge_indexes = edge_line_indexes_per_page[idx]
        kept_lines: list[str] = []
        for line_idx, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            if (
                stripped
                and line_idx in edge_indexes
                and _normalize_repeating_edge_line(stripped) in recurring_norms
            ):
                continue
            kept_lines.append(raw_line)
        cleaned_page = dict(page)
        cleaned_page["text"] = "\n".join(kept_lines).strip()
        cleaned_pages.append(cleaned_page)
    return cleaned_pages


def _searchable_pages_without_repeating_edges(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cfg = get_config()
    edge_lines = cfg.repeat_edge_lines
    min_pages = cfg.repeat_edge_min_pages
    min_ratio = cfg.repeat_edge_min_ratio
    return strip_repeating_header_footer_lines(
        pages,
        edge_lines=edge_lines,
        min_pages=min_pages,
        min_ratio=min_ratio,
    )


def _is_year_like_amount(value: str) -> bool:
    """True wenn der Betrags-Wert einer Jahreszahl (1900–2099) ohne Dezimalstellen entspricht."""
    stripped = re.sub(r"\s*(?:eur(?:o)?|€)\s*", "", value, flags=re.IGNORECASE).strip()
    return bool(_YEAR_AMOUNT_RE.match(stripped))


def _neutralize_legal_tone(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, str):
        if key in {"quote_snippet", "quote"}:
            return clean_snippet(value, max_chars=180)
        normalized = value
        for pattern, repl in _FORBIDDEN_REPLACEMENTS:
            normalized = re.sub(pattern, repl, normalized, flags=re.IGNORECASE)
        return _normalize_ws(normalized)
    if isinstance(value, list):
        return [_neutralize_legal_tone(v) for v in value]
    if isinstance(value, dict):
        return {k: _neutralize_legal_tone(v, key=k) for k, v in value.items()}
    return value


def _get_page_no(page: dict[str, Any]) -> int:
    return int(page.get("page_number") or page.get("page") or 0)


def _dedupe_keep_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        norm = _normalize_for_match(item)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(item.strip())
    return out


def _normalize_evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    field = str(item.get("field", "")).strip().lower()
    value = _normalize_ws(str(item.get("value", "") or ""))[:120]
    quote = clean_snippet(str(item.get("quote", "") or ""), max_chars=180)
    if field == "kuendigung_fristen" and not _has_termination_label_trigger(f"{value} {quote}"):
        field = "fristen_termine"
    normalized["field"] = field
    normalized["value"] = value
    page = item.get("page")
    normalized["page"] = int(page) if page is not None else None
    normalized["quote"] = quote
    label = _normalize_ws(str(item.get("label", "") or ""))
    if label:
        normalized["label"] = label[:40]
    date_iso = _normalize_date_to_iso(normalized.get("value", ""))
    if date_iso:
        normalized["date_iso"] = date_iso
    return normalized


def _dedupe_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_key: dict[tuple[str, int, str], dict[str, Any]] = {}
    for item in items:
        normalized = _normalize_evidence_item(item)
        field = str(normalized.get("field", "")).strip().lower()
        value = str(normalized.get("value", "")).strip()
        if not field or not value or value == "missing":
            continue
        page = int(normalized.get("page") or 0)
        key = (field, page, _normalize_for_match(value))
        existing = best_by_key.get(key)
        if existing is None or _snippet_quality_score(normalized) > _snippet_quality_score(existing):
            best_by_key[key] = normalized
    return list(best_by_key.values())


def _select_top_evidence(items: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    max_items = _EVIDENCE_TOPK_BY_FIELD.get(field, 2)
    ordered = sorted(
        (_normalize_evidence_item(item) for item in items),
        key=lambda x: (_snippet_quality_score(x), -(x.get("page") or 0)),
        reverse=True,
    )
    return ordered[:max_items]


def _normalize_evidence_map(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = _dedupe_evidence(items)
    by_field: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in deduped:
        by_field[str(item.get("field", ""))].append(item)

    normalized: list[dict[str, Any]] = []
    for field in _REQUIRED_EVIDENCE_FIELDS:
        selected = _select_top_evidence(by_field.get(field, []), field)
        if selected:
            normalized.extend(selected)
            continue
        normalized.append({"field": field, "value": "missing", "page": None, "quote": "missing"})
    return normalized


def build_evidence_map(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Erzeugt pro Segment eine Evidence Map:
    {"field", "value", "page", "quote"}.
    """
    evidence: list[dict[str, Any]] = []
    searchable_pages = _searchable_pages_without_repeating_edges(pages)
    if not searchable_pages:
        return evidence

    amount_re = re.compile(
        r"(?:€|eur(?:o)?)\s*\d{1,3}(?:[.]\d{3})*(?:,\d{2})?"  # EUR/€-Prefix (z.B. EUR 1.090)
        r"|\d{1,3}(?:[.]\d{3})+(?:,\d{2})?\s*(?:€|eur(?:o)?)"  # Tausenderpunkt-Betrag (z.B. 1.090 €)
        r"|\d{1,4}(?:,\d{2})?\s*(?:€|eur(?:o)?)",               # Kurzbetrag bis 4-stellig (z.B. 240 €)
        re.IGNORECASE,
    )
    date_re = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b")

    for page in searchable_pages:
        page_no = _get_page_no(page)
        page_text = str(page.get("text", "") or "")
        if not page_text.strip():
            continue

        flat_text = _normalize_ws(page_text)
        if flat_text:
            # Rolle + Name aus tokenisierten PDFs extrahieren (z.B. "Vermieter Thomas Bergmann").
            for role in ("Vermieter", "Vermieterin", "Mieter", "Mieterin", "Arbeitgeber", "Arbeitnehmer", "Käufer", "Verkäufer"):
                person_pattern = re.compile(
                    rf"{role}\s*:?\s*([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+)"
                )
                for match in person_pattern.finditer(flat_text):
                    party = _normalize_ws(match.group(1))
                    if not (_looks_like_person(party) or _looks_like_org(party)):
                        continue
                    if _is_artifact_value(party):
                        continue
                    quote = _window_quote(flat_text, match.start(), match.end(), left=40, right=80)
                    evidence.append(
                        {
                            "field": "parteien",
                            "value": party[:120],
                            "page": page_no,
                            "quote": _clip_quote_200(quote),
                        }
                    )

            for amount_match in amount_re.finditer(flat_text):
                amount_value = _normalize_ws(amount_match.group(0))
                if _is_year_like_amount(amount_value):  # PR13 P4: Jahrestoken ausschließen
                    continue
                quote = _window_quote(flat_text, amount_match.start(), amount_match.end(), left=50, right=70)
                # Betragsmapping: Zweistufig — (1) clean_snippet verhindert Keyword-Bleeding
                # aus Vorgängersatz, (2) Fallback auf Raw-Fenster wenn kein Keyword gefunden
                # (fängt Keywords wie "Grundmiete" auf, die clean_snippet abschneidet, U10-Fix).
                _fs, _fe = amount_match.start(), amount_match.end()
                _raw_window = flat_text[max(0, _fs - 50) : min(len(flat_text), _fe + 50)]
                kontext_fenster = _clip_quote_200(_raw_window)
                klassifikation = klassifiziere_betrag_mit_confidence(kontext_fenster)
                if klassifikation.confidence < MIN_BETRAG_CONFIDENCE and not klassifikation.is_ambiguous:
                    # Kein Treffer mit Clip → Raw-Fenster nochmal versuchen.
                    # Nur eindeutige Treffer übernehmen; Ambiguität im Raw-Fenster
                    # ignorieren (wäre Bleeding aus Nachbarsatz).
                    raw_klass = klassifiziere_betrag_mit_confidence(_normalize_ws(_raw_window))
                    if raw_klass.confidence >= MIN_BETRAG_CONFIDENCE:
                        klassifikation = raw_klass
                if klassifikation.confidence >= MIN_BETRAG_CONFIDENCE:
                    field_name = klassifikation.feldname
                elif klassifikation.is_ambiguous:
                    field_name = "betraege_unklar"  # Mehrdeutig → nicht rendern
                else:
                    field_name = "betraege"  # Kein Keyword → generischer Betrag
                # U10: Gesamtzahllast-Override — "zahllast" muss im linken Kontext
                # stehen (verhindert False-Positives wie Sachverständigenkosten).
                # Pattern 1: "zahllast … betraegt [AMOUNT] fällig jeweils am"
                # Pattern 2: "Gesamte monatliche Zahllast: [AMOUNT]" (direktes Label)
                # "betraege" wird einbezogen: wenn keine Miete-Keyword-Treffer im
                # 50-Zeichen-Fenster → field="betraege" generic, aber P2 stimmt → Quote
                # muss mit left=150 gesichert werden damit 3×clean_snippet nicht
                # "Zahllast:" herauswirft und der Quality-Score konkurrenzfähig bleibt.
                _is_zahllast = False
                if field_name in ("nebenkosten", "betraege_unklar", "miete", "betraege"):
                    _left_100 = flat_text[max(0, _fs - 100):_fs].lower()
                    _left_20 = flat_text[max(0, _fs - 20):_fs]
                    _right_50 = flat_text[_fe:min(len(flat_text), _fe + 50)]
                    _p1 = (
                        "zahllast" in _left_100
                        and _ZAHLLAST_LEFT_RE.search(_left_20)
                        and _ZAHLLAST_RIGHT_RE.search(_right_50)
                    )
                    _p2 = bool(re.search(r"zahllast\s*[:\-]?\s*$", _left_20, re.IGNORECASE))
                    if _p1 or _p2:
                        field_name = "betraege"
                        quote = _window_quote(flat_text, _fs, _fe, left=150, right=70)
                        _is_zahllast = True
                _ev_item: dict[str, Any] = {
                    "field": field_name,
                    "value": amount_value,
                    "page": page_no,
                    "quote": _clip_quote_200(quote),
                }
                if _is_zahllast:
                    _ev_item["is_zahllast"] = True
                evidence.append(_ev_item)

            for date_match in date_re.finditer(flat_text):
                date_value = _normalize_ws(date_match.group(0))
                date_window = _window_quote(flat_text, date_match.start(), date_match.end(), left=120, right=120)
                date_sentence = _select_best_sentence_snippet(
                    flat_text,
                    date_value,
                    field_hint="fristen_termine",
                    fallback=date_window,
                )
                context = f"{date_window} {date_sentence}"
                if _is_meta_block_context(date_window):
                    continue
                if _contains_trigger_phrase(context, _DEADLINE_TRIGGER_PHRASES):
                    # FIX 4: Filter past dates without strong Frist context
                    if not _is_past_date_no_strong_frist_context(date_value, context):
                        evidence.append(
                            {
                                "field": "fristen_termine",
                                "value": date_value,
                                "page": page_no,
                                "quote": _clip_quote_200(date_sentence or date_window),
                                "label": _deadline_label_from_context(context),
                            }
                        )
                if _contains_trigger_phrase(context, _FIELD_SENTENCE_KEYWORDS["datum_beginn"]):
                    ctx_lower = context.lower()
                    _is_price_adj_context = any(
                        kw in ctx_lower
                        for kw in (
                            "preisanpassung", "preiserhöhung", "preiserhoehung",
                            "stichtag", "frühestens", "fruehestens",
                            "nach vertragsbeginn", "nach dem vertragsbeginn",
                            # FIX 5: "X monate nach" = Berechnungsstichtag, kein Vertragsbeginn
                            "monate nach", "monate nach dem",
                        )
                    )
                    if _is_meta_block_context(context) or _is_price_adj_context:
                        pass  # kein datum_beginn eintrag
                    else:
                        begin_quote = _select_best_sentence_snippet(
                            flat_text,
                            date_value,
                            field_hint="datum_beginn",
                            fallback=date_window,
                        )
                        evidence.append(
                            {
                                "field": "datum_beginn",
                                "value": date_value,
                                "page": page_no,
                                "quote": _clip_quote_200(begin_quote or date_window),
                            }
                        )

            # Long-Format-Daten: "31. Januar 2025" – _select_best_sentence_snippet wird
            # NICHT verwendet, weil _SENTENCE_BOUNDARY_RE "31. " als Satzgrenze erkennt
            # und das Datum immer auf zwei Sätze aufteilt. Stattdessen direkt das
            # ±120-Zeichen-Kontextfenster als Quote verwenden.
            for long_date_match in _GERMAN_MONTH_DATE_RE.finditer(flat_text):
                long_date_raw = _normalize_ws(long_date_match.group(0))
                _iso = _normalize_date_to_iso(long_date_raw)
                if _iso:
                    _y, _m, _d = _iso.split("-")
                    long_date_value = f"{_d}.{_m}.{_y}"
                else:
                    long_date_value = long_date_raw
                long_date_window = _window_quote(
                    flat_text, long_date_match.start(), long_date_match.end(), left=120, right=120
                )
                context = long_date_window
                if _is_meta_block_context(long_date_window):
                    continue
                if _contains_trigger_phrase(context, _DEADLINE_TRIGGER_PHRASES):
                    # FIX 4: Filter past long-format dates without strong Frist context
                    if not _is_past_date_no_strong_frist_context(long_date_value, context):
                        evidence.append(
                            {
                                "field": "fristen_termine",
                                "value": long_date_value,
                                "page": page_no,
                                "quote": _clip_quote_200(long_date_window),
                                "label": _deadline_label_from_context(context),
                            }
                        )

            # PR12 P2: Verlängerungsfristen vorab ermitteln — diese dürfen nicht als
            # Kündigungsfrist klassifiziert werden, auch wenn 'Kündigung' im Kontextfenster steht.
            from core.legacy_deadlines import extract_renewal_and_termination
            _renewal_pairs = extract_renewal_and_termination(flat_text)
            _renewal_duration_values = {
                _normalize_ws(v) for v, lbl in _renewal_pairs if lbl == "verlaengerung"
            }

            for dur_match in _DEADLINE_DURATION_RE.finditer(flat_text):
                duration_value = _normalize_ws(dur_match.group(0))
                dur_window = _window_quote(flat_text, dur_match.start(), dur_match.end(), left=120, right=120)
                dur_quote = _select_best_sentence_snippet(
                    flat_text,
                    duration_value,
                    field_hint="fristen_termine",
                    fallback=dur_window,
                )
                context = f"{dur_window} {dur_quote}"
                if _is_meta_block_context(context):
                    continue
                if _contains_trigger_phrase(context, _DEADLINE_TRIGGER_PHRASES + ("kündigung", "kuendigung", "laufzeit")):
                    # PR12 P1: Wortgrenze-Check verhindert Match auf 'vorankündigung'
                    if _KUENDIGUNG_WORD_RE.search(context):
                        # PR12 P2: Verlängerungszeitraum (z.B. "12 Monate") nicht als
                        # Kündigungsfrist eintragen — extract_renewal_and_termination() liefert
                        # die Kündigungsfrist separat korrekt.
                        if duration_value not in _renewal_duration_values:
                            evidence.append(
                                {
                                    "field": "kuendigung_fristen",
                                    "value": duration_value,
                                    "page": page_no,
                                    "quote": _clip_quote_200(dur_quote or dur_window),
                                }
                            )
                    else:
                        evidence.append(
                            {
                                "field": "fristen_termine",
                                "value": duration_value,
                                "page": page_no,
                                "quote": _clip_quote_200(dur_quote or dur_window),
                                "label": _deadline_label_from_context(context),
                            }
                        )

            # PR12 P2: Kündigungsfrist aus Verlängerungs-/Kündigungsklausel eintragen
            for renewal_value, renewal_label in _renewal_pairs:
                if renewal_label == "kuendigungsfrist":
                    # Quote enthält flat_text damit _has_termination_label_trigger greift
                    evidence.append(
                        {
                            "field": "kuendigung_fristen",
                            "value": renewal_value,
                            "page": page_no,
                            "quote": _clip_quote_200(flat_text),
                        }
                    )

        _page_lines = page_text.splitlines()
        for _line_idx, raw_line in enumerate(_page_lines):
            line = _normalize_ws(raw_line)
            if not line:
                continue
            line_l = line.lower()

            # Parteien ausschließlich aus Segment-Zeilen extrahieren und Artefakte verwerfen.
            # Erweitert um Liefervertrags-/Werkvertrags-Rollen (lieferant, auftragnehmer, auftraggeber).
            if any(k in line_l for k in ("zwischen", "vertragsparteien", "arbeitgeber", "arbeitnehmer", "vermieter", "mieter", "käufer", "verkäufer", "lieferant", "auftragnehmer", "auftraggeber")):
                for party in _extract_party_candidates(line):
                    evidence.append(
                        {
                            "field": "parteien",
                            "value": party[:120],
                            "page": page_no,
                            "quote": _clip_quote_200(line),
                        }
                    )

            for amount_match in amount_re.finditer(line):
                amount_value = _normalize_ws(amount_match.group(0))
                if _is_artifact_value(amount_value):
                    continue
                if _is_year_like_amount(amount_value):  # PR13 P4: Jahrestoken ausschließen
                    continue
                # Betragsmapping: Einzelzeile zuerst, ±2-Zeilen-Fallback
                _kontext = extract_context_lines(_page_lines, _line_idx, window=2)
                klassifikation = klassifiziere_betrag_mit_kontext(line, _kontext)
                if klassifikation.confidence >= MIN_BETRAG_CONFIDENCE:
                    field_name = klassifikation.feldname
                elif klassifikation.is_ambiguous:
                    field_name = "betraege_unklar"  # Mehrdeutig → nicht rendern
                else:
                    field_name = "betraege"  # Kein Keyword → generischer Betrag
                _entry: dict[str, Any] = {
                    "field": field_name,
                    "value": amount_value,
                    "page": page_no,
                    "quote": _clip_quote_200(line),
                }
                if field_name == "betraege_unklar":
                    _entry["label"] = "unklar – bitte prüfen"
                evidence.append(_entry)

            # Fristen/Termine dokumentbasiert aus Segmentzeilen.
            if any(x in line_l for x in ("frist", "termin", "fällig", "faellig", "spätestens", "spaetestens", "kündigung", "kuendigung", "laufzeit")):
                values: list[str] = []
                values.extend(m.group(0) for m in date_re.finditer(line))
                values.extend(_normalize_ws(m.group(0)) for m in _DEADLINE_DURATION_RE.finditer(line))
                # FIX 2 (P1): Rohtext-Fallback entfernt. Nur Zeilen mit tatsächlichem
                # Datum oder Dauer-Wert erzeugen Fristen-Evidence. Dokument-Header,
                # Firmenname-Zeilen und Adressblöcke werden so nicht als Frist-Wert
                # ausgegeben (z.B. "Kündigungsfrist: Innovatech Solutions GmbH...").
                # (Früher: values.append(line[:120]) als Fallback)
                for value in _dedupe_str_keep_order(values):
                    if _is_artifact_value(value):
                        continue
                    best_quote = _select_best_sentence_snippet(
                        page_text,
                        value,
                        field_hint="fristen_termine",
                        fallback=line,
                    )
                    context = f"{line} {best_quote}"
                    if _is_meta_block_context(context):
                        continue
                    # FIX 1/2: Filter birth/employment/founding dates from line-based path too.
                    if _is_past_date_no_strong_frist_context(value, context):
                        continue
                    # PR12 P1: Wortgrenze-Check verhindert Match auf 'vorankündigung'
                    if _KUENDIGUNG_WORD_RE.search(line_l):
                        kf_quote = _select_best_sentence_snippet(
                            page_text,
                            value,
                            field_hint="kuendigung_fristen",
                            fallback=line,
                        )
                        evidence.append(
                            {
                                "field": "kuendigung_fristen",
                                "value": value[:120],
                                "page": page_no,
                                "quote": _clip_quote_200(kf_quote or line),
                            }
                        )
                    else:
                        evidence.append(
                            {
                                "field": "fristen_termine",
                                "value": value[:120],
                                "page": page_no,
                                "quote": _clip_quote_200(best_quote or line),
                                "label": _deadline_label_from_context(context),
                            }
                        )

            if any(x in line_l for x in ("beginn", "vertragsbeginn", "ab dem", "beginn des vertrages")):
                # FIX 5: Preisanpassungs-Stichtage sind kein Vertragsbeginn.
                # Nur spezifische Muster prüfen die eindeutig auf berechnete Stichtage hinweisen.
                # "frühestens" und "preiserhöhung" allein dürfen nicht sperren — sie können
                # in einem anderen Satz auf derselben Zeile stehen (→ echter Vertragsbeginn bleibt).
                _line_is_price_adj = any(
                    kw in line_l
                    for kw in (
                        "nach vertragsbeginn", "nach dem vertragsbeginn",
                        "monate nach vertragsbeginn",
                        "stichtag",
                        "preisanpassung",
                    )
                )
                if _is_meta_block_context(line) or _line_is_price_adj:
                    pass  # kein datum_beginn eintrag
                else:
                    for match in date_re.finditer(line):
                        begin_value = _normalize_ws(match.group(0))
                        begin_quote = _select_best_sentence_snippet(
                            page_text,
                            begin_value,
                            field_hint="datum_beginn",
                            fallback=line,
                        )
                        evidence.append(
                            {
                                "field": "datum_beginn",
                                "value": begin_value,
                                "page": page_no,
                                "quote": _clip_quote_200(begin_quote or line),
                            }
                        )

            if any(x in line_l for x in ("urlaub", "überstunden", "ueberstunden", "mehrarbeit")) and not _is_rhetoric_value(line):
                evidence.append(
                    {
                        "field": "urlaub_ueberstunden",
                        "value": line[:120],
                        "page": page_no,
                        "quote": _clip_quote_200(line),
                    }
                )

            if any(x in line_l for x in ("wettbewerbsverbot", "konkurrenz", "wettbewerbsklausel")) and not _is_rhetoric_value(line):
                evidence.append(
                    {
                        "field": "wettbewerbsverbot",
                        "value": line[:120],
                        "page": page_no,
                        "quote": _clip_quote_200(line),
                    }
                )

            # Aktenzeichen: "Az. 4 O 321/24", "Aktenzeichen: ABC-2025", "Geschäftszeichen: ..."
            _az_match = _AZ_LINE_RE.search(line)
            if _az_match:
                az_value = _clean_az_value(_normalize_ws(_az_match.group(1)).strip())
                if az_value and not _is_artifact_value(az_value):
                    evidence.append(
                        {
                            "field": "aktenzeichen",
                            "value": az_value[:80],
                            "page": page_no,
                            "quote": _clip_quote_200(line),
                        }
                    )

            # Gericht: "Amtsgericht Frankfurt", "Landgericht Berlin", "OLG München"
            _g_match = _GERICHT_RE.search(line)
            if _g_match:
                gericht_value = _normalize_ws(_g_match.group(1))
                if gericht_value and not _is_artifact_value(gericht_value):
                    evidence.append(
                        {
                            "field": "gericht",
                            "value": gericht_value[:80],
                            "page": page_no,
                            "quote": _clip_quote_200(line),
                        }
                    )

    # FIX 3: Filter Gericht-Treffer die nur auf der letzten Seite eines Mehrseiten-Segments
    # stehen. Diese kommen meist aus dem nächsten Segment (Cross-Segment-Kontamination).
    # Einzelseiten-Segmente werden nicht gefiltert.
    # Bei Segmenten mit <= 3 Seiten: Filter NICHT anwenden – kurze Dokumente haben
    # Gerichtsstandklauseln oft in den Schlussbestimmungen (letzte Seite).
    if len(searchable_pages) > 3:
        last_page_no = _get_page_no(searchable_pages[-1])
        gericht_pages = {e["page"] for e in evidence if e["field"] == "gericht"}
        if gericht_pages and gericht_pages <= {last_page_no}:
            evidence = [e for e in evidence if e["field"] != "gericht"]

    return _normalize_evidence_map(evidence)


def build_evidence_context(evidence_map: list[dict[str, Any]]) -> str:
    lines = [
        "EVIDENCE MAP (nur diese Werte verwenden):",
        "Wenn ein Feld 'missing' ist, antworte mit 'unverified' oder 'missing' und formuliere eine Rückfrage.",
    ]
    for item in evidence_map:
        field = item.get("field", "")
        value = item.get("value", "missing")
        page = item.get("page")
        quote = _clip_quote_200(str(item.get("quote", "")))
        page_token = "?" if page is None else str(page)
        lines.append(f"- field={field} | value={value} | page={page_token} | quote={quote}")
    return "\n".join(lines)


def attach_evidence_map_to_parsed(parsed: dict, evidence_map: list[dict[str, Any]]) -> dict:
    out = dict(parsed or {})
    out["evidence_map"] = evidence_map
    return out
