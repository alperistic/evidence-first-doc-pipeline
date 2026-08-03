#!/usr/bin/env python3
"""
smoke_test.py – P1-P9 Regressionstest gegen echte PDFs (kein LLM-Call).

Prüft:
  P1  Keine "Kündigungsfrist" für Audit-Ankündigung (14 Tage)
  P2  Verlängerung (12 Monate) nicht als Kündigungsfrist; 3 Monate korrekt
  P3  Gehalt = EUR 4.600,00 (nicht EUR 10.000,00)
  P4  Kein "2026 EUR"; Ratenbeträge EUR 1.000,00 / EUR 997,40 vorhanden
  P5  Kein [PERSON_X] für "Soweit", "Die" in §9/§12 IT-Rahmenvertrag
  P6  Kein [PERSON_X] für "Nachvertragliches", "Mahnkosten", "Verzugszinsen"
  P7  24.05.2026 (Rechnungsdatum) als Referenzdatum, nicht als Frist
  P8  01.04.2026 nicht doppelt (Frist + Beginn)
  P9  Beide Firmen konsistent maskiert oder beide im Klartext
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PDFS = Path(__file__).parent.parent.parent / "pdfs"
PDF_IT   = PDFS / "Kanzlei_Testvertrag_50S_Rahmenvertrag_IT_Dienstleistungen.pdf"
PDF_MIX  = PDFS / "Kanzlei_Testpaket_Mixed_55S_Mietvertrag_Arbeitsvertrag_Mahnung_Vergleich_Anlagen.pdf"

GREEN  = "\033[32m"; RED   = "\033[31m"; YELLOW = "\033[33m"
BOLD   = "\033[1m";  RESET = "\033[0m"

POLICY = {
    "dictionary_entities": [],
    "dictionary_fuzzy": False,
    "dictionary_threshold": 92,
    "pseudonymize_people": True,
}

# ---------------------------------------------------------------------------

def extract_and_redact(pdf_path: Path) -> tuple:
    """Gibt (pages, redacted_text, ctx) zurück."""
    from core.extractor import extract_text_with_meta
    from governance.redaction import prepare_for_llm

    fb = pdf_path.read_bytes()
    ex = extract_text_with_meta(fb, pdf_path.name)
    ctx = prepare_for_llm(ex.text, POLICY)
    return ex.pages, ctx.redacted_text, ctx


def build_ev(pages: list) -> list:
    from core.evidence import build_evidence_map
    return build_evidence_map(pages)


def label_betrag(value: str, quote: str, doc_type: str) -> str:
    from core.export_render import _human_label_for_evidence
    return _human_label_for_evidence("betraege", value, quote, doc_type)


def classif(text: str, label: str = "") -> str:
    from core.legacy_deadlines import classify_date_category
    return classify_date_category(text, label)


# ---------------------------------------------------------------------------

def section(title: str):
    print(f"\n{BOLD}{'─'*72}{RESET}")
    print(f"{BOLD}{title}{RESET}")
    print(f"{BOLD}{'─'*72}{RESET}")


def show(label: str, value):
    print(f"  {label}: {value}")


def ok(msg: str)  -> bool: print(f"  {GREEN}✅ {msg}{RESET}"); return True
def warn(msg: str)-> bool: print(f"  {YELLOW}⚠️  {msg}{RESET}"); return None
def fail(msg: str)-> bool: print(f"  {RED}❌ {msg}{RESET}"); return False


# ---------------------------------------------------------------------------
# Hauptprüfungen
# ---------------------------------------------------------------------------

results: dict[str, bool | None] = {}

def run():
    # ── PDF laden ──────────────────────────────────────────────────────────
    section("Lade PDFs")
    for p in (PDF_IT, PDF_MIX):
        if not p.exists():
            print(f"{RED}Datei nicht gefunden: {p}{RESET}")
            sys.exit(1)
        print(f"  ✓ {p.name}")

    # ── IT-Rahmenvertrag ───────────────────────────────────────────────────
    section("IT-Rahmenvertrag  (50 S.)")
    pages_it, redacted_it, ctx_it = extract_and_redact(PDF_IT)
    print(f"  Seiten extrahiert : {len(pages_it)}")
    print(f"  Redacted-Text     : {len(redacted_it):,} Zeichen")

    ev_it = build_ev(pages_it)
    kf_it   = [i for i in ev_it if i.get("field") == "kuendigung_fristen"]
    ft_it   = [i for i in ev_it if i.get("field") == "fristen_termine"]
    bet_it  = [i for i in ev_it if i.get("field") == "betraege"]

    print(f"\n  kuendigung_fristen ({len(kf_it)} Einträge):")
    for i in kf_it: print(f"    value={i['value']!r:30} quote={i.get('quote','')[:60]!r}")

    print(f"\n  fristen_termine ({len(ft_it)} Einträge):")
    for i in ft_it: print(f"    value={i['value']!r:30} quote={i.get('quote','')[:60]!r}")

    # P1 ──────────────────────────────────────────────────────────────────
    section("P1 – Audit-Vorankündigung 14 Tage NICHT Kündigungsfrist")
    # Zeilen, die "vorankündigung" + "14" enthalten → dürfen nicht in kf landen
    kf_audit_hits = [
        i for i in kf_it
        if ("14" in str(i.get("value","")) or "vorankündigung" in str(i.get("quote","")).lower())
        and "vorankündigung" in str(i.get("quote","")).lower()
    ]
    show("kuendigung_fristen-Einträge mit 'vorankündigung' im Quote", len(kf_audit_hits))
    for i in kf_audit_hits:
        print(f"    TREFFER: value={i['value']!r}  quote={i.get('quote','')[:80]!r}")
    if not kf_audit_hits:
        results["P1"] = ok("Kein '14 Tage' / Vorankündigung in kuendigung_fristen")
    else:
        results["P1"] = fail(f"{len(kf_audit_hits)} Einträge mit Vorankündigung in kuendigung_fristen")

    # P2 ──────────────────────────────────────────────────────────────────
    section("P2 – Verlängerung 12 Monate ≠ Kündigungsfrist; 3 Monate = Kündigungsfrist")
    kf_12 = [i for i in kf_it if "12" in str(i.get("value","")) and "monat" in str(i.get("value","")).lower()]
    kf_3  = [i for i in kf_it if re.match(r"3\s*monate?", str(i.get("value","")).lower())]

    show("'12 Monate' in kuendigung_fristen", [i["value"] for i in kf_12])
    show("'3 Monate' in kuendigung_fristen",  [i["value"] for i in kf_3])

    p2_no12 = len(kf_12) == 0
    p2_has3 = len(kf_3) > 0

    if p2_no12 and p2_has3:
        results["P2"] = ok("12 Monate nicht in KF; 3 Monate korrekt als KF")
    elif p2_no12 and not p2_has3:
        results["P2"] = warn("12 Monate nicht in KF (OK), aber 3 Monate auch nicht gefunden")
    elif not p2_no12:
        results["P2"] = fail(f"12 Monate fälschlich in kuendigung_fristen: {[i['value'] for i in kf_12]}")
    else:
        results["P2"] = warn(f"Teilergebnis: no12={p2_no12}, has3={p2_has3}")

    # P5 ──────────────────────────────────────────────────────────────────
    section("P5 – Kein [PERSON_X] für 'Soweit', 'Die' in §9/§12 IT-Rahmenvertrag")
    # Suche nach [PERSON_N] direkt nach Soweit/Die/Während am Satzanfang
    p5_patterns = [
        r"\bSoweit\s+\[(?:PERSON|PARTEI)_\w+\]",
        r"\bDie\s+\[(?:PERSON|PARTEI)_\w+\]",
        r"\bWährend\s+\[(?:PERSON|PARTEI)_\w+\]",
        r"\bÄnderungen\s+\[(?:PERSON|PARTEI)_\w+\]",
        r"\bAnsprüche\s+\[(?:PERSON|PARTEI)_\w+\]",
    ]
    p5_hits = []
    for pat in p5_patterns:
        found = re.findall(pat, redacted_it)
        if found:
            p5_hits.extend(found)
    show("False-Positive-Treffer", p5_hits)
    if not p5_hits:
        results["P5"] = ok("Keine False-Positive-Maskierungen für Satzanfänge")
    else:
        results["P5"] = fail(f"False Positives gefunden: {p5_hits[:5]}")

    # P9 (IT-Rahmenvertrag) ────────────────────────────────────────────────
    section("P9 – Firmen konsistent maskiert (IT-Rahmenvertrag)")
    # Bekannte Firmennamen aus Testdatei (Auftraggeber/Auftragnehmer)
    it_firm_candidates = re.findall(r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß]+\s+(?:GmbH|AG|UG|KG|GbR|SE)\b", redacted_it)
    it_pseudonyms = re.findall(r"\[PARTEI_\d+\]", redacted_it)
    it_pseudonym_set = set(it_pseudonyms)
    # Check: keine PARTEI_1/PARTEI_2 Inkonsistenz
    show("Firmennamen im Klartext (nach Redaktion)", list(set(it_firm_candidates))[:8])
    show("Pseudonyme gefunden", sorted(it_pseudonym_set)[:8])
    # Bewertung: wenn Pseudonyme vorhanden und keine bekannten Testfirmen im Klartext
    known_it_firms = ["Nordstadt", "LegalTech", "TechSolutions", "DataSystems"]
    klartext_hits = [f for f in known_it_firms if f in redacted_it]
    if klartext_hits and it_pseudonym_set:
        results["P9_it"] = warn(f"Firmennamen teilweise im Klartext: {klartext_hits}; Pseudonyme: {sorted(it_pseudonym_set)}")
    elif it_pseudonym_set:
        results["P9_it"] = ok(f"Firmen pseudonymisiert: {sorted(it_pseudonym_set)[:4]}")
    else:
        results["P9_it"] = warn("Keine Partei-Pseudonyme gefunden (Firmen evtl. nicht erkannt)")

    # ── Mixed PDF ─────────────────────────────────────────────────────────
    section("Mixed PDF  (55 S.)")
    pages_mix, redacted_mix, ctx_mix = extract_and_redact(PDF_MIX)
    print(f"  Seiten extrahiert : {len(pages_mix)}")
    print(f"  Redacted-Text     : {len(redacted_mix):,} Zeichen")

    ev_mix = build_ev(pages_mix)
    kf_mix  = [i for i in ev_mix if i.get("field") == "kuendigung_fristen"]
    ft_mix  = [i for i in ev_mix if i.get("field") == "fristen_terme"]
    bet_mix = [i for i in ev_mix if i.get("field") == "betraege"]
    db_mix  = [i for i in ev_mix if i.get("field") == "datum_beginn"]

    print(f"\n  betraege ({len(bet_mix)} Einträge):")
    for i in bet_mix: print(f"    value={i['value']!r:30} quote={i.get('quote','')[:70]!r}")

    print(f"\n  datum_beginn ({len(db_mix)} Einträge):")
    for i in db_mix: print(f"    value={i['value']!r:30} quote={i.get('quote','')[:60]!r}")

    # P3 ──────────────────────────────────────────────────────────────────
    section("P3 – Gehalt = EUR 4.600,00 (nicht EUR 10.000,00)")
    # Suche im redacted_mix nach Gehalt/Vergütung + Betrag
    gehalt_amounts = re.findall(r"(?:[Gg]ehalt|[Vv]ergütung|[Bb]ruttogehalt)[^.]{0,60}EUR\s+[\d.,]+", redacted_mix)
    vertragsstrafe_amounts = re.findall(r"[Vv]ertragsstrafe[^.]{0,60}EUR\s+[\d.,]+", redacted_mix)
    show("Gehalt/Vergütung + Betrag im Text", gehalt_amounts[:5])
    show("Vertragsstrafe + Betrag im Text",   vertragsstrafe_amounts[:5])

    # Prüfe betraege evidence für Arbeitsvertrag
    # Betrag mit Gehalt-Kontext vs. Vertragsstrafe-Kontext
    bet_gehalt = [
        i for i in bet_mix
        if any(t in str(i.get("quote","")).lower() for t in ("gehalt","vergütung","bruttogehalt"))
        and not any(t in str(i.get("quote","")).lower() for t in ("vertragsstrafe","verstoß"))
    ]
    bet_vstr = [
        i for i in bet_mix
        if any(t in str(i.get("quote","")).lower() for t in ("vertragsstrafe","verstoß","pönale"))
    ]
    show("betraege mit Gehalts-Kontext", [(i["value"], label_betrag(i["value"], i.get("quote",""), "arbeitsvertrag")) for i in bet_gehalt])
    show("betraege mit Vertragsstrafe-Kontext", [(i["value"], label_betrag(i["value"], i.get("quote",""), "arbeitsvertrag")) for i in bet_vstr])

    # Hauptprüfung: Hat irgendeiner der betraege EUR 4.600 ODER EUR 10.000 als Gehalt gelabelt?
    labeled_as_gehalt = [
        i for i in bet_mix
        if label_betrag(i["value"], i.get("quote",""), "arbeitsvertrag") == "Gehalt"
    ]
    show("Als 'Gehalt' gelabelte Beträge", [(i["value"], i.get("quote","")[:50]) for i in labeled_as_gehalt])

    gehalt_4600 = any("4.600" in i["value"] or "4600" in i["value"] for i in labeled_as_gehalt)
    gehalt_10000 = any("10.000" in i["value"] for i in labeled_as_gehalt)

    if gehalt_10000 and not gehalt_4600:
        results["P3"] = fail("EUR 10.000,00 als Gehalt gelabelt (Vertragsstrafe!)")
    elif gehalt_4600 and not gehalt_10000:
        results["P3"] = ok("EUR 4.600,00 als Gehalt; EUR 10.000,00 korrekt NICHT als Gehalt")
    elif not labeled_as_gehalt:
        # Kein Gehalt-Label → prüfen ob Betrag überhaupt da
        all_bet_values = [i["value"] for i in bet_mix]
        show("Alle betraege-Werte", all_bet_values)
        results["P3"] = warn("Kein Betrag als 'Gehalt' gelabelt — Betrag-Extraktion prüfen")
    else:
        results["P3"] = warn(f"Teilergebnis: gehalt_4600={gehalt_4600}, gehalt_10000={gehalt_10000}")

    # P4 ──────────────────────────────────────────────────────────────────
    section("P4 – Kein '2026 EUR'; Ratenbeträge EUR 1.000,00 / EUR 997,40")
    all_bet_values_mix = [i["value"] for i in bet_mix]
    year_hits = [v for v in all_bet_values_mix if re.search(r"\b(?:19|20)\d{2}\b", v) and "," not in v and "." not in v.replace(",","")]
    has_1000 = any("1.000" in v for v in all_bet_values_mix)
    has_997  = any("997" in v for v in all_bet_values_mix)

    show("Alle extrahierten betraege-Werte", all_bet_values_mix)
    show("Jahres-Artefakte in betraege",     year_hits)
    show("EUR 1.000,00 vorhanden",           has_1000)
    show("EUR 997,40 vorhanden",             has_997)

    if year_hits:
        results["P4"] = fail(f"Jahres-Token in betraege: {year_hits}")
    elif has_1000 or has_997:
        details = []
        if has_1000: details.append("EUR 1.000,00 ✓")
        if has_997:  details.append("EUR 997,40 ✓")
        if not has_1000: details.append("EUR 1.000,00 fehlt")
        if not has_997:  details.append("EUR 997,40 fehlt")
        if has_1000 and has_997:
            results["P4"] = ok("Keine Jahres-Token; beide Ratenbeträge vorhanden")
        else:
            results["P4"] = warn(f"Keine Jahres-Token; {', '.join(details)}")
    else:
        results["P4"] = warn("Weder Jahres-Token noch Ratenbeträge gefunden (andere Seite?)")

    # P6 ──────────────────────────────────────────────────────────────────
    section("P6 – Kein [PERSON_X] für 'Nachvertragliches', 'Mahnkosten', 'Verzugszinsen'")
    p6_patterns = [
        r"\b[Nn]achvertragliche[s]?\s+\[(?:PERSON|PARTEI)_\w+\]",
        r"\b[Mm]ahnkosten\s+\[(?:PERSON|PARTEI)_\w+\]",
        r"\b[Vv]erzugszinsen\s+\[(?:PERSON|PARTEI)_\w+\]",
        r"\b[Gg]egenüber\s+\[(?:PERSON|PARTEI)_\w+\]",
        r"\[(?:PERSON|PARTEI)_\w+\]\s+[Ww]ettbewerbsverbot",
    ]
    p6_hits = []
    for pat in p6_patterns:
        found = re.findall(pat, redacted_mix)
        if found: p6_hits.extend(found)
    show("False-Positive-Treffer im Mixed PDF", p6_hits)
    if not p6_hits:
        results["P6"] = ok("Keine False-Positive-Maskierungen für Klauselnamen")
    else:
        results["P6"] = fail(f"False Positives: {p6_hits[:5]}")

    # P7 ──────────────────────────────────────────────────────────────────
    section("P7 – 24.05.2026 als Referenzdatum, nicht als Frist")
    date_24_05 = "24.05.2026"
    cat = classif(date_24_05, date_24_05)
    show(f"classify_date_category('{date_24_05}')", cat)

    # Prüfe in den Pages ob 24.05.2026 vorkommt und in welchem Kontext
    pages_with_24 = [p for p in pages_mix if date_24_05 in str(p.get("text",""))]
    show(f"Seiten mit '{date_24_05}'", len(pages_with_24))
    for p in pages_with_24[:2]:
        idx = str(p.get("text","")).find(date_24_05)
        snippet = str(p.get("text",""))[max(0,idx-80):idx+60]
        print(f"    Kontext: ...{snippet.strip()[:120]}...")

    # Datum im evidence check
    ft_24 = [i for i in ev_mix if date_24_05 in str(i.get("value",""))]
    show(f"Evidence-Einträge mit {date_24_05}", [(i["field"], i["value"]) for i in ft_24])

    # Klassifikation des Rechnungsdatum-Kontexts
    rechnungs_ctx = f"RE-2026-0142 vom {date_24_05}"
    cat_re = classif(rechnungs_ctx, date_24_05)
    show(f"classify mit RE-ID-Kontext", cat_re)

    if cat_re == "Referenzdatum":
        results["P7"] = ok(f"{date_24_05} mit RE-ID-Kontext korrekt als Referenzdatum")
    elif cat == "Referenzdatum":
        results["P7"] = ok(f"{date_24_05} als Referenzdatum klassifiziert")
    else:
        results["P7"] = fail(f"{date_24_05} als '{cat}' klassifiziert (erwartet: Referenzdatum)")

    # P8 ──────────────────────────────────────────────────────────────────
    section("P8 – 01.04.2026 nicht doppelt (Frist + Beginn)")
    date_01_04 = "01.04.2026"
    ev_01_04 = [i for i in ev_mix if date_01_04 in str(i.get("value",""))]
    fields_01_04 = [i["field"] for i in ev_01_04]
    show(f"Evidence-Felder mit {date_01_04}", fields_01_04)
    for i in ev_01_04:
        print(f"    field={i['field']!r}  value={i['value']!r}  quote={i.get('quote','')[:60]!r}")

    is_doppelt = "fristen_terme" in fields_01_04 and "datum_beginn" in fields_01_04
    if is_doppelt:
        results["P8"] = fail(f"{date_01_04} sowohl in fristen_termine als auch datum_beginn")
    elif not ev_01_04:
        results["P8"] = warn(f"{date_01_04} nicht in evidence gefunden (Datum evtl. auf anderer Seite)")
    else:
        results["P8"] = ok(f"{date_01_04} nur in {set(fields_01_04)} — keine Dopplung")

    # P9 (Mixed PDF) ───────────────────────────────────────────────────────
    section("P9 – Firmen konsistent maskiert (Mixed PDF)")
    mix_firms_klartext = re.findall(r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß]+\s+(?:GmbH|AG|UG|KG|GbR|SE)\b", redacted_mix)
    mix_pseudonyms = re.findall(r"\[PARTEI_\d+\]", redacted_mix)
    mix_pseudonym_set = set(mix_pseudonyms)

    show("Firmennamen im Klartext (Auswahl)", list(set(mix_firms_klartext))[:10])
    show("Partei-Pseudonyme", sorted(mix_pseudonym_set)[:8])

    known_mix_firms = ["Nordstadt", "LegalTech", "TechSolutions"]
    klartext_mix = [f for f in known_mix_firms if f in redacted_mix]

    # Konsistenz: wenn Pseudonyme vorhanden, dann sollen bekannte Firmen nicht im Klartext sein
    if mix_pseudonym_set and not klartext_mix:
        results["P9"] = ok(f"Firmen pseudonymisiert, keine bekannten Testnamen im Klartext")
    elif mix_pseudonym_set and klartext_mix:
        results["P9"] = warn(f"Inkonsistenz: Pseudonyme vorhanden {sorted(mix_pseudonym_set)[:3]} "
                             f"aber auch Testnamen im Klartext: {klartext_mix}")
    elif not mix_pseudonym_set and not mix_firms_klartext:
        results["P9"] = warn("Weder Pseudonyme noch GmbH-Firmen gefunden — Dokument prüfen")
    else:
        results["P9"] = warn(f"Firmennamen im Klartext: {list(set(mix_firms_klartext))[:5]}, keine Pseudonyme")

    # ── ZUSAMMENFASSUNG ───────────────────────────────────────────────────
    section("ZUSAMMENFASSUNG")
    status_map = {
        "P1": "Keine 'Kündigungsfrist' für Audit 14 Tage",
        "P2": "Verlängerung 12M ≠ KF; 3M = KF korrekt",
        "P3": "Gehalt = EUR 4.600,00",
        "P4": "Kein '2026 EUR'; Raten korrekt",
        "P5": "Kein [PERSON_X] für Satzanfänge (IT-AV)",
        "P6": "Kein [PERSON_X] für Klauselnamen (Mixed)",
        "P7": "24.05.2026 als Referenzdatum",
        "P8": "01.04.2026 nicht doppelt",
        "P9": "Firmen konsistent maskiert",
    }
    # P9_it wird in P9 zusammengefasst
    if "P9_it" in results and "P9" in results:
        if results["P9_it"] is True and results["P9"] is True:
            results["P9"] = True
        elif results["P9_it"] is False or results["P9"] is False:
            results["P9"] = False
        else:
            results["P9"] = None

    rows = []
    for key, desc in status_map.items():
        v = results.get(key)
        if v is True:
            sym = f"{GREEN}✅{RESET}"
        elif v is False:
            sym = f"{RED}❌{RESET}"
        else:
            sym = f"{YELLOW}⚠️ {RESET}"
        rows.append((key, sym, desc))

    print(f"\n  {'Punkt':<5} {'Status':<12} Beschreibung")
    print(f"  {'─'*5} {'─'*10} {'─'*50}")
    for key, sym, desc in rows:
        print(f"  {key:<5} {sym:<12} {desc}")

    total = len(rows)
    passed = sum(1 for k,_,__ in rows if results.get(k) is True)
    warned = sum(1 for k,_,__ in rows if results.get(k) is None)
    failed = sum(1 for k,_,__ in rows if results.get(k) is False)
    print(f"\n  Gesamt: {total}  |  {GREEN}✅ {passed}{RESET}  |  {YELLOW}⚠️  {warned}{RESET}  |  {RED}❌ {failed}{RESET}\n")


if __name__ == "__main__":
    run()
