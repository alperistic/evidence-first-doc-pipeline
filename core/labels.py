"""core/labels.py – Gemeinsame Label-Logik für Summary- und Export-Rendering.

Single Source of Truth für Label-Funktionen, die sowohl in
core/export_render.py (Text-Export) als auch ui/components.py (Streamlit-UI)
benötigt werden.
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

# Zeitraum-Erkennungsmuster für den Gehalt-Gate:
# Gehalt-Beträge werden nur klassifiziert wenn ein Zeitraum im Kontext steht.
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
    """Prüft ob der Kontext einen Zeitraum-Hinweis enthält."""
    return bool(_ZEITRAUM_RE.search(context))


def extract_context_lines(all_lines: list[str], line_idx: int, window: int = 2) -> str:
    """Gibt ±window Zeilen um line_idx als zusammengefügten String zurück.

    Genutzt in evidence.py für kontextbasiertes Betragsmapping (±2 Zeilen).
    """
    start = max(0, line_idx - window)
    end = min(len(all_lines), line_idx + window + 1)
    parts = [_normalize_ws(l) for l in all_lines[start:end] if _normalize_ws(l)]
    return " ".join(parts)


def klassifiziere_betrag_mit_kontext(line: str, kontext_lines: str) -> "BetragKlassifikation":
    """Zweistufiges Betragsmapping mit ±2-Zeilen-Fallback.

    Stufe 1: Einzelzeile (klassifiziere_betrag_mit_confidence).
             Bei Treffer (confidence 1.0) oder Ambiguität → direkte Rückgabe.
    Stufe 2: Kein Treffer auf Einzelzeile → ±2-Zeilen-Kontext mit
             Proximity-Suche (das Keyword am nächsten zur Zeilenmitte gewinnt).
             So werden Beträge auf eigenen Zeilen korrekt typisiert.

    Gehalt-Gate gilt in beiden Stufen.
    """
    # Stufe 1: Einzelzeile hat Vorrang (verhindert Bleeding aus Nachbarzeilen)
    einzel = klassifiziere_betrag_mit_confidence(line)
    if einzel.confidence >= 1.0:
        return einzel
    if einzel.is_ambiguous:
        return einzel
    # Stufe 2: Kein Keyword auf Einzelzeile → Proximity-Suche in ±2 Zeilen
    feld = klassifiziere_betrag(kontext_lines)
    if feld == "betrag":
        return BetragKlassifikation(feldname="betrag", confidence=0.0, matched_keyword=None)
    # Gehalt-Gate: auch im erweiterten Kontext Zeitraum prüfen
    if feld == "gehalt" and not _has_zeitraum(kontext_lines):
        return BetragKlassifikation(
            feldname="betrag", confidence=0.0, matched_keyword=None, is_ambiguous=True
        )
    return BetragKlassifikation(feldname=feld, confidence=1.0, matched_keyword=feld)


def klassifiziere_betrag(kontext: str) -> str:
    """Bestimmt Feldtyp anhand des umliegenden Textkontexts.

    Gibt den Feldnamen zurück oder 'betrag' als Fallback.

    Wenn mehrere Kategorien im Kontext-Fenster matchen, wird diejenige
    gewählt, deren Keyword-Match am nächsten zur Fenstermitte liegt
    (= am nächsten zum Betrag selbst).
    """
    kontext_lower = kontext.lower()
    mitte = len(kontext_lower) // 2
    best_feld: str | None = None
    best_dist = len(kontext_lower) + 1

    for feldname, keywords in BETRAG_KONTEXT_KEYWORDS.items():
        for kw in keywords:
            # Wortgrenze: Keyword darf nicht Teil eines längeren Wortes sein
            # (z.B. "monatsmiete" soll nicht in "monatsmieten" matchen)
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
    feldname: str        # z.B. "kaution", "rueckstand", "miete"
    confidence: float    # 0.0 – 1.0
    matched_keyword: str | None  # welches Keyword hat getroffen
    is_ambiguous: bool = False   # True wenn Treffer in 2+ Kategorien


def klassifiziere_betrag_mit_confidence(kontext: str) -> BetragKlassifikation:
    """Klassifiziert Betrag mit Confidence-Score.

    Confidence-Logik:
    - 1 eindeutiger Treffer in genau 1 Kategorie → confidence 1.0
    - Treffer in 2+ Kategorien → confidence 0.0, is_ambiguous=True
    - Kein Treffer → feldname="betrag", confidence 0.0, is_ambiguous=False
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
        # Gehalt-Gate: nur ausgeben wenn Zeitraum im Kontext vorhanden
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
    """Leitet ein kontextspezifisches Label aus dem unmittelbaren Belegtext ab.

    Gibt None zurück wenn kein spezifischer Betragsbegriff erkennbar ist —
    dann soll der Betrag in der Ausgabe unterdrückt werden statt ein
    generisches Label ("Betrag") zu zeigen.

    Reihenfolge: spezifischere Begriffe haben Vorrang vor allgemeineren.
    Vertragsstrafe/Verstoß hat Vorrang vor Gehalt (PR13 P3).
    """
    ctx = str(context or "").lower()
    # Kaufpreis / M&A
    if "basiskaufpreis" in ctx:
        return "Basiskaufpreis"
    if "kaufpreis" in ctx:
        return "Kaufpreis"
    if "earnout" in ctx:
        return "Earnout"
    # Abfindung / Streitwert
    if "abfindung" in ctx:
        return "Abfindung"
    if "streitwert" in ctx:
        return "Streitwert"
    # Monatsmiete: NUR "Gesamte monatliche Zahllast" / "monatliche Zahllast" / "Gesamtmiete"
    if "monatliche zahllast" in ctx or "monatl. zahllast" in ctx or "gesamtmiete" in ctx:
        return "Monatsmiete"
    if "fällig jeweils am" in ctx or "faellig jeweils am" in ctx or "fällig jeweils zum" in ctx:
        return "Monatsmiete"
    # Kaution / Mietsicherheit — vor Rückstand-Check (Kaution hat Vorrang auch wenn
    # "Rückstand" im selben Satz steht, z.B. "Kaution verwerten wegen Mietrückstand")
    # Ausnahme: IBAN/BIC-Kontext → Zahlungsaufforderung, kein Kaution-Hinterlegungsbeleg.
    # Abdeckt: roher Text ("iban:", "bic:"), redacted Placeholder ("[iban_1]", "[bic_1]").
    _has_bank = (
        "iban" in ctx
        or "[iban_" in ctx
        or bool(re.search(r"\bbic\b", ctx))
        or "[bic_" in ctx
    )
    if ("kaution" in ctx or "mietsicherheit" in ctx) and not _has_bank:
        return "Kaution"
    # Mietrückstand
    if "rückstand" in ctx or "rueckstand" in ctx:
        return "Rückstand"
    # Nebenkosten / Betriebskosten — proximity-basierte Unterscheidung von Gesamtmiete.
    # Aufgerufene Signatur in Produktion: context = f"{quote} {value}".
    # Wenn value an letzter Stelle angehängt wurde, erscheint der letzte EUR-Betrag im
    # String zweimal. Wir nutzen dieses Signal um zwischen "appended value" (proximity-
    # Algorithmus) und "only text" (alter Positions-Algorithmus) zu unterscheiden.
    if "nebenkosten" in ctx or "betriebskosten" in ctx:
        if "insgesamt" in ctx:
            _all_amts = list(re.finditer(r'eur\s*\d[\d.,]+|\d[\d.]+,\d{2}', ctx))
            if _all_amts:
                _last_str = _all_amts[-1].group()
                _first_pos = ctx.find(_last_str)
                _last_pos = ctx.rfind(_last_str)
                if _first_pos != _last_pos:
                    # Value wurde angehängt (zweites Vorkommen = appended value) →
                    # proximity-Algorithmus: Keyword nächstgelegen vor erstem Vorkommen gewinnt.
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
                    # Kein angehängter Value — alter Positions-Algorithmus:
                    # "insgesamt" vor "nebenkosten/betriebskosten" → Betrag ist das Gesamt.
                    nk_idx = min(
                        ctx.find("nebenkosten") if "nebenkosten" in ctx else len(ctx),
                        ctx.find("betriebskosten") if "betriebskosten" in ctx else len(ctx),
                    )
                    if ctx.find("insgesamt") < nk_idx:
                        return "Gesamtmiete"
        return "Nebenkosten"
    # Schadensersatz / Gesamtschaden — VOR insgesamt-Check (verhindert Gesamtmiete-Label bei
    # "Gesamtschaden insgesamt EUR X" — Schadensersatz hat Vorrang vor Gesamtmiete)
    if any(k in ctx for k in ("gesamtschaden", "schadensersatz", "schadensposten", "schadensbetrag")):
        return "Schadensersatz"
    # FIX 1: Umsatzvolumen / Portfolio — VOR insgesamt-Check (verhindert Gesamtmiete-Label bei
    # "Gesamtumsatz insgesamt EUR 12 Mio." — Umsatzvolumen hat Vorrang vor Gesamtmiete)
    if any(k in ctx for k in ("umsatzvolumen", "jahresumsatz", "gesamtumsatz", "jahresumsatz", "portfoliowert", "portfolio-wert")):
        return "Umsatzvolumen"
    # FIX 4: Gutachtenkosten / Sachverständigenkosten — VOR insgesamt-Check und VOR Miete
    if any(k in ctx for k in ("gutachten", "sachverständigen", "sachverstaendigen", "gutachterkosten", "sachverständigenkosten", "sachverstaendigenkosten")):
        return "Gutachtenkosten"
    # Gesamtmiete — "insgesamt" ohne Nebenkosten-Keyword im Kontext
    if "insgesamt" in ctx:
        return "Gesamtmiete"
    # Miete — spezifische Komposita inkl. Grundmiete und Kaltmiete
    if any(k in ctx for k in ("kaltmiete", "grundmiete", "nettomiete", "monatsmiete", "mietzins")):
        return "Miete"
    # Bußgeld / OWi — Vorrang vor Vertragsstrafe
    if any(k in ctx for k in ("bußgeld", "bussgeld", "verwarnungsgeld", "geldbuße", "geldbusse", "owi", "ordnungswidrigkeit")):
        return "Bußgeld"
    # Vertragsstrafe / Pönale — Vorrang vor Gehalt (PR13 P3)
    if any(k in ctx for k in ("vertragsstrafe", "pönale", "poenale", "verstoß", "verstoss")):
        return "Vertragsstrafe"
    # Jahresprämie (vor allgemeinem Gehalt-Match)
    if "jahresprämie" in ctx or "jahrespraemie" in ctx:
        return "Jahresprämie"
    # Gehalt / Vergütung
    if any(k in ctx for k in ("bruttogehalt", "monatsgehalt", "jahresgehalt", "jahresbrutto")):
        return "Gehalt"
    if any(k in ctx for k in ("gehalt", "vergütung", "verguetung", "lohn", "entlohnung")):
        return "Gehalt"
    # Haftungsbegrenzung / Haftungslimit
    if any(k in ctx for k in ("haftungsbegrenzung", "haftungslimit", "haftungsgrenze", "haftungsobergrenze")):
        return "Haftungsbegrenzung"
    if "haftung" in ctx:
        return "Haftung"
    # Mindestabnahme / Mindestbestellwert
    if any(k in ctx for k in ("mindestabnahme", "mindestabnahmeverpflichtung", "mindestbestellwert", "mindestmenge")):
        return "Mindestabnahme"
    # Forderung (spezifisch)
    if "forderungsbetrag" in ctx:
        return "Forderungsbetrag"
    if "forderung" in ctx:
        return "Forderung"
    return None


def _human_label_for_evidence(field: str, value: str, quote: str, doc_type: str) -> str | None:
    field_key = str(field or "").strip().lower()
    quote_l = str(quote or "").lower()
    # Beträge: kontextbasiert ableiten; None = unterdrücken
    if field_key == "betraege":
        # Kombinierter Kontext aus Quote + Value für robuste Erkennung
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
        # FIX 3: "Mietbeginn" darf NUR via doc_type vergeben werden (Zeilen 349-350).
        # Quote-Context darf KEIN "Mietbeginn" erzeugen um Fehlklassifizierung bei
        # Nicht-Miet-Dokumenten (z.B. Scheidungsfolgenvereinbarung) zu verhindern.
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
    """FIX 3: Normalize 2-digit years to 4-digit for deduplication.

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
    """Normiert Betragswert auf reine Zahl ohne Währungssymbol (lowercase).

    'EUR 12.600,00' ≡ '12.600,00 EUR' ≡ '12.600,00 €' → '12.600,00'
    Wird für den Kaution-Guard-Vergleich genutzt, um Prefix/Suffix-Varianten
    des Währungssymbols als gleich zu behandeln.
    """
    stripped = re.sub(r"\s*(?:€|eur(?:o)?)\s*", " ", _normalize_ws(str(v or "")), flags=re.IGNORECASE)
    return _normalize_ws(stripped).strip().lower()


def _collect_all_kaution_amounts(segment_results: list[dict]) -> frozenset[str]:
    """Sammelt alle Kautions-Beträge aus ALLEN Segmenten (normiert, lowercase).

    Wird für den cross-segment Kaution-Guard benötigt: Wenn ein Rückstand-Item
    denselben Betrag wie eine Kaution aus einem anderen Segment hat, soll es
    ebenfalls entfernt werden.

    Berücksichtigt:
    - Items mit field=='kaution'
    - Items beliebigen fields wo _amount_label_from_context(quote + value) == "Kaution"
      (deckt Fälle ab, wo field='betraege' gesetzt wurde, aber der Kontext Kaution signalisiert)

    Speichert jeden Betrag in ZWEI Formen:
    - vollständig normiert: "eur 12.600,00"
    - numerisch kanonisch:  "12.600,00"  (Währungssymbol entfernt)
    So werden Prefix/Suffix-Varianten ("EUR 12.600,00" vs. "12.600,00 EUR") sicher erkannt.
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
    """Post-Processing für ECKDATEN-Beträge nach Sammlung aus der Evidence-Map.

    1. Kaution-Schutz: Wenn derselbe normierte Betragswert als "kaution"- UND als
       "rueckstand"-Item vorhanden ist → rueckstand-Eintrag entfernen.
       Schließt auch cross-segment bekannte Kautions-Beträge ein
       (known_kaution_amounts aus allen Segmenten des Dokuments).
       Kaution hat Priorität (auch in Mahnungs-Kontext).

    2. Label-Duplikat-Dedup: Identisches (label, normalized_value) → nur erster
       Eintrag bleibt (bei mehreren Quellen für denselben Betrag erste behalten).
    """
    def _norm_val(v: Any) -> str:
        return _normalize_ws(str(v or "")).strip().lower()

    # 1. Kaution-Schutz: lokale Kautions-Beträge + cross-segment bekannte Beträge
    local_kaution: set[str] = {
        _norm_val(it.get("value", ""))
        for it in items
        if str(it.get("field", "") or "").lower() == "kaution"
        and str(it.get("value", "") or "") not in ("", "missing")
    }
    kaution_values = local_kaution | (known_kaution_amounts or frozenset())

    # Kaution-Beträge auch als kanonische (währungslose) Form für Prefix/Suffix-Vergleich
    kaution_values_canonical = {_norm_amount_canonical(v) for v in kaution_values}

    filtered: list[dict] = []
    for it in items:
        field = str(it.get("field", "") or "").lower()
        val_norm = _norm_val(it.get("value", ""))
        # Rückstand-Item für bekannten Kautions-Betrag entfernen
        # Vergleich sowohl exakt ("eur 12.600,00") als auch kanonisch ("12.600,00")
        if field == "rueckstand" and (
            val_norm in kaution_values
            or _norm_amount_canonical(it.get("value", "")) in kaution_values_canonical
        ):
            continue
        filtered.append(it)

    # 2. Label-Duplikat-Dedup nach berechnetem Anzeige-Label
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
        # Items ohne Label werden von _evidence_line unterdrückt; durchlassen.
        if label is None:
            deduped.append(it)
            continue
        key = (label.lower(), val_norm)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    # 3. FIX 3: Generische Labels (Miete, Betrag, …) unterdrücken, wenn ein
    #    spezifisches Label (Gutachtenkosten, Rückstand, …) denselben kanonischen
    #    Betrag abdeckt.
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
