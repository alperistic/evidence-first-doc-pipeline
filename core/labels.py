"""core/labels.py – Shared label logic for summary and export rendering.

Single source of truth for label functions needed both in
core/export_render.py (text export) and ui/components.py (Streamlit UI).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.models import _normalize_ws


_SUMMARY_LABELS: dict[str, str] = {
    "parteien": "Parteien",
    "betraege": "Betrag",
    "fristen_termine": "Frist/Termin",
    "datum_beginn": "Beginn",
    "kuendigung_fristen": "Kündigungsfrist",
    "miete": "Miete",
    "nebenkosten": "Nebenkosten",
    "kaution": "Kaution",
    "rueckstand": "Rückstand",
    "schadensersatz": "Schadensersatz",
    "streitwert": "Streitwert",
    "aktenzeichen": "Aktenzeichen",
    "gericht": "Gericht",
    "beweismittel": "Beweismittel",
}


def _summary_doc_type_key(doc_text: str, title_hint: str = "") -> str:
    joined = f"{doc_text} {title_hint}".lower()
    if "mietvertrag" in joined:
        return "mietvertrag"
    if "arbeitsvertrag" in joined:
        return "arbeitsvertrag"
    if "klageschrift" in joined:
        return "klageschrift"
    if "mahnung" in joined or "mahnschreiben" in joined:
        return "mahnung"
    return "generic"


BETRAG_KONTEXT_KEYWORDS: dict[str, list[str]] = {
    "rueckstand": [
        "rückstand", "rückstände", "mietrückstand", "zahlungsrückstand",
        "schuldet", "offen", "verzug", "forderung",
    ],
    "kaution": [
        "kaution", "kautionsbetrag", "sicherheitsleistung",
        "sicherheitsdepot", "hinterlegt", "hinterlegung",
    ],
    "miete": [
        "kaltmiete", "grundmiete", "nettomiete",
        "monatsmiete", "mietzins", "gesamtmiete",
    ],
    "nebenkosten": [
        "nebenkosten", "betriebskosten", "betriebskostenvorauszahlung",
        "betriebskostenvorauszahlungen", "vorauszahlung",
    ],
    "schadensersatz": [
        "schadensersatz", "schadensbetrag",
        "gesamtschaden", "schadensposten", "reparaturkosten",
    ],
    "streitwert": [
        "streitwert", "gesamtstreitwert", "räumungsstreitwert",
        "gegenstandswert", "beschwerdewert", "verfahrenswert", "klageforderung",
    ],
    "gehalt": [
        "gehalt", "bruttogehalt", "nettogehalt", "monatsgehalt",
        "jahresgehalt", "jahresbrutto", "lohn", "vergütung", "verguetung",
        "entlohnung",
    ],
}

# Time-period detection pattern for the salary gate:
# Salary amounts are only classified when a time period is present in the context.
_ZEITRAUM_RE = re.compile(
    r"\b(?:"
    r"januar|februar|m[äa]rz|april|mai|juni|juli|august|september|oktober|november|dezember"
    r"|\d{1,2}\.\d{1,2}\.\d{2,4}"
    r"|monat(?:lich)?"
    r"|j[äa]hrlich"
    r"|\d{4}"
    r"|pro\s+monat|pro\s+jahr"
    r"|ab\s+\d"
    r"|\d{1,2}/\d{2,4}"
    r"|halbjahr(?:lich)?"
    r"|wöchentlich"
    r")\b",
    re.IGNORECASE,
)


def _has_zeitraum(context: str) -> bool:
    """Checks whether the context contains a time-period indicator."""
    return bool(_ZEITRAUM_RE.search(context))


def extract_context_lines(all_lines: list[str], line_idx: int, window: int = 2) -> str:
    """Returns the ±window lines around line_idx joined into a single string.

    Used in evidence.py for context-based amount mapping (±2 lines).
    """
    start = max(0, line_idx - window)
    end = min(len(all_lines), line_idx + window + 1)
    parts = [_normalize_ws(l) for l in all_lines[start:end] if _normalize_ws(l)]
    return " ".join(parts)


def klassifiziere_betrag_mit_kontext(line: str, kontext_lines: str) -> "BetragKlassifikation":
    """Two-stage amount mapping with a ±2-line fallback.

    Stage 1: single line (klassifiziere_betrag_mit_confidence).
             On a hit (confidence 1.0) or ambiguity → return directly.
    Stage 2: no hit on the single line → ±2-line context with a
             proximity search (the keyword closest to the line's midpoint wins).
             This correctly types amounts that sit on their own line.

    The salary gate applies at both stages.
    """
    # Stage 1: the single line takes precedence (prevents bleed from neighboring lines)
    einzel = klassifiziere_betrag_mit_confidence(line)
    if einzel.confidence >= 1.0:
        return einzel
    if einzel.is_ambiguous:
        return einzel
    # Stage 2: no keyword on the single line → proximity search across ±2 lines
    feld = klassifiziere_betrag(kontext_lines)
    if feld == "betrag":
        return BetragKlassifikation(feldname="betrag", confidence=0.0, matched_keyword=None)
    # Salary gate: also check for a time period in the extended context
    if feld == "gehalt" and not _has_zeitraum(kontext_lines):
        return BetragKlassifikation(
            feldname="betrag", confidence=0.0, matched_keyword=None, is_ambiguous=True
        )
    return BetragKlassifikation(feldname=feld, confidence=1.0, matched_keyword=feld)


def klassifiziere_betrag(kontext: str) -> str:
    """Determines the field type from the surrounding text context.

    Returns the field name, or 'betrag' as a fallback.

    If multiple categories match within the context window, the one
    whose keyword match is closest to the window's midpoint wins
    (= closest to the amount itself).
    """
    kontext_lower = kontext.lower()
    mitte = len(kontext_lower) // 2
    best_feld: str | None = None
    best_dist = len(kontext_lower) + 1

    for feldname, keywords in BETRAG_KONTEXT_KEYWORDS.items():
        for kw in keywords:
            # Word boundary: the keyword must not be part of a longer word
            # (e.g. "monatsmiete" must not match inside "monatsmieten")
            for m in re.finditer(rf"\b{re.escape(kw)}\b", kontext_lower):
                pos = m.start()
                kw_mitte = pos + len(kw) // 2
                dist = abs(kw_mitte - mitte)
                if dist < best_dist:
                    best_dist = dist
                    best_feld = feldname

    return best_feld if best_feld is not None else "betrag"


@dataclass
class BetragKlassifikation:
    feldname: str        # e.g. "kaution", "rueckstand", "miete"
    confidence: float    # 0.0 – 1.0
    matched_keyword: str | None  # which keyword matched
    is_ambiguous: bool = False   # True if matched in 2+ categories


def klassifiziere_betrag_mit_confidence(kontext: str) -> BetragKlassifikation:
    """Classifies an amount with a confidence score.

    Confidence logic:
    - 1 unambiguous match in exactly 1 category → confidence 1.0
    - Match in 2+ categories → confidence 0.0, is_ambiguous=True
    - No match → feldname="betrag", confidence 0.0, is_ambiguous=False
    """
    kontext_lower = kontext.lower()
    treffer: dict[str, str] = {}  # feldname → matched_keyword

    for feldname, keywords in BETRAG_KONTEXT_KEYWORDS.items():
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", kontext_lower):
                treffer[feldname] = kw
                break

    if len(treffer) == 1:
        feldname, kw = next(iter(treffer.items()))
        # Salary gate: only emit when a time period is present in the context
        if feldname == "gehalt" and not _has_zeitraum(kontext_lower):
            return BetragKlassifikation(
                feldname="betrag", confidence=0.0, matched_keyword=None, is_ambiguous=True
            )
        return BetragKlassifikation(feldname=feldname, confidence=1.0, matched_keyword=kw)
    elif len(treffer) > 1:
        return BetragKlassifikation(feldname="betrag", confidence=0.0, matched_keyword=None, is_ambiguous=True)
    else:
        return BetragKlassifikation(feldname="betrag", confidence=0.0, matched_keyword=None, is_ambiguous=False)


def _amount_label_from_context(context: str) -> str | None:
    """Derives a context-specific label from the immediate evidence text.

    Returns None if no specific amount term is recognizable — in that
    case the amount should be suppressed from the output rather than
    shown under a generic label ("Betrag").

    Order: more specific terms take precedence over more general ones.
    Contract penalty/breach takes precedence over salary (PR13 P3).
    """
    ctx = str(context or "").lower()
    # Purchase price / M&A
    if "basiskaufpreis" in ctx:
        return "Basiskaufpreis"
    if "kaufpreis" in ctx:
        return "Kaufpreis"
    if "earnout" in ctx:
        return "Earnout"
    # Severance pay / amount in dispute
    if "abfindung" in ctx:
        return "Abfindung"
    if "streitwert" in ctx:
        return "Streitwert"
    # Monthly rent: ONLY "Gesamte monatliche Zahllast" / "monatliche Zahllast" / "Gesamtmiete"
    if "monatliche zahllast" in ctx or "monatl. zahllast" in ctx or "gesamtmiete" in ctx:
        return "Monatsmiete"
    if "fällig jeweils am" in ctx or "faellig jeweils am" in ctx or "fällig jeweils zum" in ctx:
        return "Monatsmiete"
    # Security deposit / rental security — before the arrears check (deposit takes
    # precedence even if "Rückstand" appears in the same sentence, e.g.
    # "Kaution verwerten wegen Mietrückstand")
    # Exception: IBAN/BIC context → payment demand, not a deposit receipt.
    # Covers: raw text ("iban:", "bic:"), redacted placeholder ("[iban_1]", "[bic_1]").
    _has_bank = (
        "iban" in ctx
        or "[iban_" in ctx
        or bool(re.search(r"\bbic\b", ctx))
        or "[bic_" in ctx
    )
    if ("kaution" in ctx or "mietsicherheit" in ctx) and not _has_bank:
        return "Kaution"
    # Rent arrears
    if "rückstand" in ctx or "rueckstand" in ctx:
        return "Rückstand"
    # Ancillary/operating costs — proximity-based disambiguation from total rent.
    # Signature as called in production: context = f"{quote} {value}".
    # When value was appended at the end, the last EUR amount appears twice in the
    # string. We use that signal to distinguish "appended value" (proximity
    # algorithm) from "only text" (old position-based algorithm).
    if "nebenkosten" in ctx or "betriebskosten" in ctx:
        if "insgesamt" in ctx:
            _all_amts = list(re.finditer(r'eur\s*\d[\d.,]+|\d[\d.]+,\d{2}', ctx))
            if _all_amts:
                _last_str = _all_amts[-1].group()
                _first_pos = ctx.find(_last_str)
                _last_pos = ctx.rfind(_last_str)
                if _first_pos != _last_pos:
                    # Value was appended (second occurrence = appended value) →
                    # proximity algorithm: closest keyword before the first occurrence wins.
                    _best_gesamt = max(
                        (ctx.rfind(k, 0, _first_pos) for k in ("insgesamt", "gesamtmiete")),
                        default=-1,
                    )
                    _best_nk = max(
                        (ctx.rfind(k, 0, _first_pos) for k in ("nebenkostenvorauszahlung", "nebenkosten", "betriebskosten")),
                        default=-1,
                    )
                    if _best_gesamt >= 0 and _best_gesamt > _best_nk:
                        return "Gesamtmiete"
                else:
                    # No appended value — old position-based algorithm:
                    # "insgesamt" before "nebenkosten/betriebskosten" → amount is the total.
                    nk_idx = min(
                        ctx.find("nebenkosten") if "nebenkosten" in ctx else len(ctx),
                        ctx.find("betriebskosten") if "betriebskosten" in ctx else len(ctx),
                    )
                    if ctx.find("insgesamt") < nk_idx:
                        return "Gesamtmiete"
        return "Nebenkosten"
    # Damages / total damage — BEFORE the "insgesamt" check (prevents a total-rent label
    # for "Gesamtschaden insgesamt EUR X" — damages take precedence over total rent)
    if any(k in ctx for k in ("gesamtschaden", "schadensersatz", "schadensposten", "schadensbetrag")):
        return "Schadensersatz"
    # FIX 1: Revenue volume / portfolio — BEFORE the "insgesamt" check (prevents a total-rent
    # label for "Gesamtumsatz insgesamt EUR 12 Mio." — revenue volume takes precedence over total rent)
    if any(k in ctx for k in ("umsatzvolumen", "jahresumsatz", "gesamtumsatz", "jahresumsatz", "portfoliowert", "portfolio-wert")):
        return "Umsatzvolumen"
    # FIX 4: Expert/appraisal costs — BEFORE the "insgesamt" check and BEFORE rent
    if any(k in ctx for k in ("gutachten", "sachverständigen", "sachverstaendigen", "gutachterkosten", "sachverständigenkosten", "sachverstaendigenkosten")):
        return "Gutachtenkosten"
    # Total rent — "insgesamt" without an ancillary-costs keyword in the context
    if "insgesamt" in ctx:
        return "Gesamtmiete"
    # Rent — specific compounds including base rent and cold rent
    if any(k in ctx for k in ("kaltmiete", "grundmiete", "nettomiete", "monatsmiete", "mietzins")):
        return "Miete"
    # Fine / regulatory offense — takes precedence over contract penalty
    if any(k in ctx for k in ("bußgeld", "bussgeld", "verwarnungsgeld", "geldbuße", "geldbusse", "owi", "ordnungswidrigkeit")):
        return "Bußgeld"
    # Contract penalty — takes precedence over salary (PR13 P3)
    if any(k in ctx for k in ("vertragsstrafe", "pönale", "poenale", "verstoß", "verstoss")):
        return "Vertragsstrafe"
    # Annual bonus (before the general salary match)
    if "jahresprämie" in ctx or "jahrespraemie" in ctx:
        return "Jahresprämie"
    # Salary / remuneration
    if any(k in ctx for k in ("bruttogehalt", "monatsgehalt", "jahresgehalt", "jahresbrutto")):
        return "Gehalt"
    if any(k in ctx for k in ("gehalt", "vergütung", "verguetung", "lohn", "entlohnung")):
        return "Gehalt"
    # Liability cap / liability limit
    if any(k in ctx for k in ("haftungsbegrenzung", "haftungslimit", "haftungsgrenze", "haftungsobergrenze")):
        return "Haftungsbegrenzung"
    if "haftung" in ctx:
        return "Haftung"
    # Minimum purchase quantity / minimum order value
    if any(k in ctx for k in ("mindestabnahme", "mindestabnahmeverpflichtung", "mindestbestellwert", "mindestmenge")):
        return "Mindestabnahme"
    # Claim/receivable (specific)
    if "forderungsbetrag" in ctx:
        return "Forderungsbetrag"
    if "forderung" in ctx:
        return "Forderung"
    return None


def _human_label_for_evidence(field: str, value: str, quote: str, doc_type: str) -> str | None:
    field_key = str(field or "").strip().lower()
    quote_l = str(quote or "").lower()
    # Amounts: derive from context; None = suppress
    if field_key == "betraege":
        # Combined context from quote + value for robust detection
        return _amount_label_from_context(f"{quote} {value}")
    if field_key == "datum_beginn":
        # FIX 4/7: Use doc_type to assign meaningful label; avoid "Mietbeginn" for non-Miet docs.
        # doc_type may be the normalized key ("mietvertrag") or a raw string ("Gewerbemietvertrag").
        # Use substring checks so both normalized keys and raw strings are handled correctly.
        doc_type_l = str(doc_type or "").lower()
        if any(k in doc_type_l for k in ("miet", "räum", "raeumung")):
            return "Mietbeginn"
        if any(k in doc_type_l for k in ("arbeit", "kündigung", "kuendigung", "beschäftigung", "beschaeftigung")):
            return "Beschäftigungsbeginn"
        # For unrecognized doc_type, derive label from quote context.
        # FIX 3: "Mietbeginn" may ONLY be assigned via doc_type (lines 349-350).
        # The quote context must NOT produce "Mietbeginn", to prevent misclassification
        # on non-rental documents (e.g. a divorce settlement agreement).
        if any(k in quote_l for k in ("unterhalt", "unterhaltszahlung", "kindesunterhalt", "unterhaltspflicht")):
            return "Unterhaltsbeginn"
        if any(k in quote_l for k in ("arbeit", "beschäftigung", "dienstverhältnis", "anstellung")):
            return "Beschäftigungsbeginn"
        return "Beginn"
    if doc_type == "mietvertrag":
        if field_key == "fristen_termine" and "kaution" in quote_l:
            return "Kaution fällig bis"
    if field_key in _SUMMARY_LABELS:
        return _SUMMARY_LABELS[field_key]
    return field_key.replace("_", " ").title() or "Angabe"


_2DIGIT_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2})\b")


def _normalize_date_value_for_dedup(value: str) -> str:
    """FIX 3: Normalizes 2-digit years to 4-digit for deduplication.

    '15.12.24' → '15.12.2024'  (treated as same as '15.12.2024')
    """
    def _expand(m: re.Match) -> str:  # type: ignore[type-arg]
        day, month, year2 = m.group(1), m.group(2), int(m.group(3))
        year4 = 2000 + year2 if year2 < 70 else 1900 + year2
        return f"{day}.{month}.{year4}"
    return _2DIGIT_DATE_RE.sub(_expand, value)


def _dedupe_summary_items(items: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items or []:
        value = _normalize_ws(str(item.get("value", "") or "")).lower()
        page = str(item.get("page", "") or "")
        field = str(item.get("field", "") or "").lower()
        # FIX 3: normalize 2-digit years so "15.12.24" and "15.12.2024" share a key
        value_norm = _normalize_date_value_for_dedup(value)
        key = (field, value_norm, page)
        if not value or value == "missing" or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _norm_amount_canonical(v: Any) -> str:
    """Normalizes an amount value to a plain number without a currency symbol (lowercase).

    'EUR 12.600,00' ≡ '12.600,00 EUR' ≡ '12.600,00 €' → '12.600,00'
    Used for the deposit-guard comparison, so that prefix/suffix variants
    of the currency symbol are treated as equal.
    """
    stripped = re.sub(r"\s*(?:€|eur(?:o)?)\s*", " ", _normalize_ws(str(v or "")), flags=re.IGNORECASE)
    return _normalize_ws(stripped).strip().lower()


def _collect_all_kaution_amounts(segment_results: list[dict]) -> frozenset[str]:
    """Collects all deposit ("Kaution") amounts across ALL segments (normalized, lowercase).

    Needed for the cross-segment deposit guard: if an arrears item has the
    same amount as a deposit from another segment, it should also be removed.

    Takes into account:
    - Items with field=='kaution'
    - Items of any field where _amount_label_from_context(quote + value) == "Kaution"
      (covers cases where field='betraege' was set but the context signals a deposit)

    Stores each amount in TWO forms:
    - fully normalized: "eur 12.600,00"
    - numeric canonical: "12.600,00" (currency symbol removed)
    This reliably catches prefix/suffix variants ("EUR 12.600,00" vs. "12.600,00 EUR").
    """
    def _norm(v: Any) -> str:
        return _normalize_ws(str(v or "")).strip().lower()

    amounts: set[str] = set()
    for seg in segment_results or []:
        ev_map = seg.get("evidence_map", []) if isinstance(seg, dict) else []
        for item in ev_map or []:
            field = str(item.get("field", "") or "").lower()
            val = str(item.get("value", "") or "")
            if not val or val == "missing":
                continue
            is_kaution = field == "kaution"
            if not is_kaution:
                quote = str(item.get("quote", "") or "")
                if _amount_label_from_context(f"{quote} {val}") == "Kaution":
                    is_kaution = True
            if is_kaution:
                amounts.add(_norm(val))
                amounts.add(_norm_amount_canonical(val))
    return frozenset(amounts)


def _postprocess_eckdaten_items(
    items: list[dict],
    doc_type: str = "generic",
    known_kaution_amounts: "frozenset[str] | None" = None,
) -> list[dict]:
    """Post-processing for ECKDATEN amounts after collection from the evidence map.

    1. Deposit guard: if the same normalized amount value exists as both a "kaution"
       item and a "rueckstand" item → remove the rueckstand entry.
       Also covers deposit amounts known from other segments
       (known_kaution_amounts across all segments of the document).
       Deposit takes priority (even in a dunning-letter context).

    2. Label-duplicate dedup: identical (label, normalized_value) → only the first
       entry is kept (when multiple sources report the same amount, keep the first).
    """
    def _norm_val(v: Any) -> str:
        return _normalize_ws(str(v or "")).strip().lower()

    # 1. Deposit guard: local deposit amounts + cross-segment known amounts
    local_kaution: set[str] = {
        _norm_val(it.get("value", ""))
        for it in items
        if str(it.get("field", "") or "").lower() == "kaution"
        and str(it.get("value", "") or "") not in ("", "missing")
    }
    kaution_values = local_kaution | (known_kaution_amounts or frozenset())

    # Deposit amounts also as a canonical (currency-less) form for prefix/suffix comparison
    kaution_values_canonical = {_norm_amount_canonical(v) for v in kaution_values}

    filtered: list[dict] = []
    for it in items:
        field = str(it.get("field", "") or "").lower()
        val_norm = _norm_val(it.get("value", ""))
        # Remove an arrears item for a known deposit amount
        # Compares both exact ("eur 12.600,00") and canonical ("12.600,00") forms
        if field == "rueckstand" and (
            val_norm in kaution_values
            or _norm_amount_canonical(it.get("value", "")) in kaution_values_canonical
        ):
            continue
        filtered.append(it)

    # 2. Label-duplicate dedup by the computed display label
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for it in filtered:
        label = _human_label_for_evidence(
            str(it.get("field", "") or ""),
            str(it.get("value", "") or ""),
            str(it.get("quote", "") or ""),
            doc_type,
        )
        val_norm = _norm_val(it.get("value", ""))
        # Items with no label are suppressed by _evidence_line; pass them through.
        if label is None:
            deduped.append(it)
            continue
        key = (label.lower(), val_norm)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    # 3. FIX 3: Suppress generic labels (Miete, Betrag, …) when a specific label
    #    (Gutachtenkosten, Rückstand, …) covers the same canonical amount.
    _GENERIC_LABELS: frozenset[str] = frozenset({"miete", "betrag", "zahlung", "mietzahlung"})
    specific_amounts: set[str] = set()
    for it in deduped:
        lbl = _human_label_for_evidence(
            str(it.get("field", "") or ""),
            str(it.get("value", "") or ""),
            str(it.get("quote", "") or ""),
            doc_type,
        )
        if lbl and lbl.lower() not in _GENERIC_LABELS:
            ca = _norm_amount_canonical(str(it.get("value", "") or ""))
            if ca:
                specific_amounts.add(ca)
    if not specific_amounts:
        return deduped
    final: list[dict] = []
    for it in deduped:
        lbl = _human_label_for_evidence(
            str(it.get("field", "") or ""),
            str(it.get("value", "") or ""),
            str(it.get("quote", "") or ""),
            doc_type,
        )
        if lbl and lbl.lower() in _GENERIC_LABELS:
            ca = _norm_amount_canonical(str(it.get("value", "") or ""))
            if ca and ca in specific_amounts:
                continue
        final.append(it)
    return final


def _should_suppress_party_list(dokumenttyp_parteien: str) -> bool:
    text = str(dokumenttyp_parteien or "").lower()
    return " zwischen " in f" {text} " or "kläger" in text or "klaeger" in text or "beklagt" in text
