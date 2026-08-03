"""governance/redaction_patterns.py – Regex-Patterns, Konstanten und Regelwerk.

Extrahiert aus governance/redaction.py (W2 God-File-Split).
Enthält alle kompilierten regulären Ausdrücke, Stoplisten,
die _RegexRule-Dataclass, Render-Funktionen, Validatoren
und die _REGEX_RULES-Liste.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

# ---------------------------------------------------------------------------
# Basis-Patterns und Charsets
# ---------------------------------------------------------------------------

_WORD_CHARS = r"A-Za-z0-9ÄÖÜäöüß"
_SPACE_RE = re.compile(r"\s+")
_FUZZY_TOKEN_RE = re.compile(rf"[{_WORD_CHARS}][{_WORD_CHARS}\-.]*")
_FUZZY_CLEAN_RE = re.compile(rf"[^{_WORD_CHARS}]+")
_PLACEHOLDER_RE = re.compile(r"\[(?P<kind>[A-ZÄÖÜ_]+)(?:_[A-Z0-9]+)?\]")
_RESTORE_TOKEN_RE = re.compile(
    r"\[(?:DICT|PERSON|ADDR|EMAIL|IBAN|PARTEI|AKTENZEICHEN|PHONE|TAXID|FIRMA|DATUM|BIC|ORG|HRB)_[0-9]+\]"
)
_PLACEHOLDER_LEAK_RE = re.compile(
    r"\[(?:DICT|PERSON|ADDR|EMAIL|IBAN|PARTEI|AKTENZEICHEN|PHONE|TAXID|FIRMA|DATUM|BIC|ORG|HRB)_[0-9]+\]"
)

# ---------------------------------------------------------------------------
# Person/Party-Detection-Patterns
# ---------------------------------------------------------------------------

# Robuste, einfache Heuristik für Personen mit optionalem Dr.-Titel.
_PERSON_CANDIDATE_RE = re.compile(
    r"\b(?:Dr\.\s*)?[A-ZÄÖÜ][a-zäöüß]+(?:[\t\n\r\f\v ]+[A-ZÄÖÜ][a-zäöüß]+){1}\b"
)
_PARTY_PERSON_RE = re.compile(
    r"\b(?:kläger(?:in)?|klaeger(?:in)?|beklagt(?:e|er|en)?|"
    r"vermieter(?:in)?|mieter(?:in)?|arbeitgeber(?:in)?|arbeitnehmer(?:in)?|"
    r"käufer(?:in)?|kaeufer(?:in)?|verkäufer(?:in)?|verkaeufer(?:in)?|"
    r"gläubiger(?:in)?|glaeubiger(?:in)?|schuldner(?:in)?|"
    r"mandant(?:in)?|auftraggeber(?:in)?|auftragnehmer(?:in)?|"
    r"antragsteller(?:in)?|antragsgegner(?:in)?|"
    r"darlehensnehmer(?:in)?|darlehensgeber(?:in)?|"
    r"versicherungsnehmer(?:in)?|gesellschafter(?:in)?|"
    r"geschäftsführer(?:in)?|geschaeftsfuehrer(?:in)?|"
    r"bürge|buerge|bürgin|buergin)\b"
    r"\s*[:,-]?\s*"
    r"(?P<name>(?:Dr\.\s*)?[A-ZÄÖÜ][a-zäöüß]+(?:[\t\n\r\f\v ]+[A-ZÄÖÜ][a-zäöüß]+){1})",
    flags=re.IGNORECASE,
)
# PR11 – P9: Firmennamen nach Rollenbezeichnung (Auftraggeber/Auftragnehmer etc.)
# werden vollständig als Partei maskiert – auch bei CamelCase-Namen (LegalTech).
# Wird VOR _PARTY_PERSON_RE gescannt, damit der volle Firmenname den partiellen
# Person-Match (z. B. "Nordstadt LegalTech" ohne "GmbH") überschreibt.
_PARTY_COMPANY_RE = re.compile(
    r"\b(?:kläger(?:in)?|klaeger(?:in)?|beklagt(?:e|er|en)?|"
    r"vermieter(?:in)?|mieter(?:in)?|arbeitgeber(?:in)?|arbeitnehmer(?:in)?|"
    r"käufer(?:in)?|kaeufer(?:in)?|verkäufer(?:in)?|verkaeufer(?:in)?|"
    r"gläubiger(?:in)?|glaeubiger(?:in)?|schuldner(?:in)?|"
    r"mandant(?:in)?|auftraggeber(?:in)?|auftragnehmer(?:in)?|"
    r"antragsteller(?:in)?|antragsgegner(?:in)?|"
    r"darlehensnehmer(?:in)?|darlehensgeber(?:in)?|"
    r"versicherungsnehmer(?:in)?|gesellschafter(?:in)?|"
    r"geschäftsführer(?:in)?|geschaeftsfuehrer(?:in)?|"
    r"bürge|buerge|bürgin|buergin)\b"
    r"\s*[:,-]?\s*"
    r"(?P<company>(?:[A-ZÄÖÜ0-9][A-Za-zÄÖÜäöüß0-9&'.\-]+\s+){1,4}"
    r"(?:GmbH(?:\s*&\s*Co\.?\s*(?:KG|OHG))?|AG|UG(?:\s*\(haftungsbeschränkt\))?|"
    r"KG|OHG|e\.?\s?K\.|GbR|SE|mbH|Co\.?\s*(?:KG|OHG)|"
    r"&\s*Co\.?\s*(?:KG|OHG)|&\s*Partner(?:schaft)?|"
    r"Partnerschaft(?:sgesellschaft)?|PartG(?:\s*mbB)?|mbB))\b",
    flags=re.IGNORECASE,
)
_TITLE_PREFIX_RE = re.compile(r"^(?:Dr\.\s*)", flags=re.IGNORECASE)
_PSEUDONYM_TOKEN_RE = re.compile(r"^\[(?:PARTEI|PERSON)_[A-Z0-9]+\]$")
# P1-A: Rechtsform-Suffixe die einen Firmennamen kennzeichnen.
# Wenn ein via _PARTY_PERSON_RE gematchter Name direkt von einem dieser Suffixe
# gefolgt wird, muss er als ORG (nicht PERSON) klassifiziert werden.
_TRAILING_ORG_SUFFIX_RE = re.compile(
    r"\s+(?:GmbH(?:\s*&\s*Co\.?\s*(?:KGaA|KG|OHG))?|AG|UG(?:\s*\(haftungsbeschränkt\))?|"
    r"KG|OHG|GbR|e\.?\s*K\.|SE|mbH|Co\.?\s*(?:KGaA|KG|OHG)|"
    r"&\s*Co\.?\s*(?:KGaA|KG|OHG)|Inc\.?|Ltd\.?|S\.?A\.?|"
    r"&\s*Partner(?:schaft)?|Partnerschaft(?:sgesellschaft)?|PartG(?:\s*mbB)?|mbB|"
    r"e\.?\s*V\.?|eG)\b"
)
_ADDRESS_HINT_RE = re.compile(
    r"\b(straße|str\.|strasse|platz|weg|allee|gasse|ring|ufer|damm|chaussee|promenade|stieg|steig)\b",
    flags=re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Stoplisten und Token-Sets
# ---------------------------------------------------------------------------

_NON_PERSON_PREFIXES = {
    "Landgericht",
    "Amtsgericht",
    "Oberlandesgericht",
    "Bundesgerichtshof",
    "Verwaltungsgericht",
    "Sozialgericht",
    "Finanzgericht",
}
# Compound legal/professional terms: if the matched token is followed by these words
# in the same phrase it is a compound term, not a person name.
# Also includes job titles / Berufsbezeichnungen that must not be classified as persons.
_COMPOUND_TERM_SUFFIXES = re.compile(
    r"\b(?:tagessätze|tagessatz|sätze|satz|kosten|pauschale|aufwand|budget|"
    r"aufwände|aufwandsentschädig|honorar|vergütung|vergütungen|"
    r"engineer|manager|consultant|director|analyst|developer|"
    r"architect|designer|specialist|coordinator|advisor|assistant|"
    r"officer|partner|associate|counsel|sachbearbeiter|buchhalter|prokurist)\b",
    re.IGNORECASE,
)
# Stoplist: häufige juristische Begriffe / Tabellenköpfe / Boilerplate,
# die fälschlich als PERSON erkannt werden können.
_PERSON_STOPLIST = {
    "fiktives testdokument",
    "nur zur systemprüfung",
    "nur zur systempruefung",
    "ratenplan vereinbart",
    "ratenplan fälligkeit",
    "ratenplan faelligkeit",
    "rate fälligkeit",
    "rate faelligkeit",
    "fälligkeit betrag",
    "faelligkeit betrag",
    "zinsen verzug",
    "vertragliche vereinbarung",
    "gesetzliche grundlage",
    "rechtliche begründung",
    "rechtliche begruendung",
    "fiktives testdokument",
    "allgemeine geschäftsbedingungen",
    "allgemeine geschaeftsbedingungen",
    "besondere vereinbarungen",
}
# Regex für Zeilen, die nur aus Großbuchstaben, Ziffern und Sonderzeichen
# bestehen (Tabellenköpfe, Aktenzeichen-Fragmente).
_ALL_UPPER_LINE_RE = re.compile(r"^[A-ZÄÖÜ0-9\s\W]+$")
_NON_PERSON_TOKENS = {
    "der",
    "die",
    "das",
    "den",
    "dem",
    "des",
    "ein",
    "eine",
    "einen",
    "einem",
    "und",
    "oder",
    "im",
    "am",
    "an",
    "mit",
    "von",
    "zu",
    "zur",
    "zum",
    "aktenzeichen",
    "klageschrift",
    "sachverhalt",
    "streitwert",
    "rueckzahlung",
    "rückzahlung",
    "darlehen",
    "antrag",
    "anträge",
    "beweis",
    "beweismittel",
    "hamburg",
    "berlin",
    "muenchen",
    "münchen",
    "seite",
    "datum",
    "klägerin",
    "kläger",
    "klaegerin",
    "klaeger",
    "beklagte",
    "beklagter",
    "beklagten",
    "rechtsanwalt",
    "rechtsanwältin",
    "kosten",
    "betrag",
    "fälligkeit",
    "faelligkeit",
    "anträge",
    "antraege",
    "rate",
    "ratenplan",
    "verzug",
    "zinsen",
    "tilgung",
    "restschuld",
    "fristablauf",
    "mahnung",
    "vertrag",
    "vereinbarung",
    "fiktives",
    "testdokument",
    "dokument",
    "dieses",
    "systemprüfung",
    "systempruefung",
    "rechtliche",
    "begründung",
    "begruendung",
    "amtsgericht",
    "landgericht",
}
# PR11 – P5/P6: Erweiterbare Whitelist juristischer Satzanfangswörter und
# allgemeiner Vertrags-Substantive, die NIE Personennamen sind.
# Jedes Token hier (lowercase) blockiert einen False-Positive-Match in
# _is_person_candidate, wenn es als erstes oder zweites Wort auftaucht.
_NOT_A_PERSON_TOKENS: frozenset[str] = frozenset(
    {
        # Satzanfangswörter (erscheinen nach Zeilenumbruch / Paragraphenkopf)
        "soweit",
        "während",
        "waehrend",
        "gegenüber",
        "gegenueber",
        "sofern",
        "insoweit",
        "unbeschadet",
        # Klausel- und Paragraphenköpfe
        "nachvertragliches",
        "nachvertraglicher",
        "nachvertraglichen",
        "wettbewerbsverbot",
        "haftungsausschluss",
        "haftungsbeschränkung",
        "haftungsbeschraenkung",
        "kündigungsrecht",
        "kuendigungsrecht",
        "kündigungsfrist",
        "kuendigungsfrist",
        "kündigung",
        "kuendigung",
        "vertragslaufzeit",
        "gewährleistung",
        "gewaehrleistung",
        # Tabellenanfänge / Boilerplate-Substantive
        "mahnkosten",
        "verzugszinsen",
        "bearbeitungsgebühren",
        "bearbeitungsgebuehren",
        "ansprüche",
        "ansprueche",
        "ansprüchen",
        "anspruechen",
        "leistungen",
        "änderungen",
        "aenderungen",
        "zahlungen",
        "fristen",
        "obliegenheiten",
        "pflichten",
        "rechte",
        "regelungen",
    }
)
_ROLE_CONTEXT_RE = re.compile(
    r"(kläger(?:in)?|klaeger(?:in)?|beklagt(?:e|er|en)?|zeuge(?:n|in)?|"
    r"rechtsanw[aä]lt(?:in)?|anwalt|vertreter(?:in)?|herr|frau|"
    r"arbeitgeber(?:in)?|arbeitnehmer(?:in)?|vermieter(?:in)?|mieter(?:in)?|"
    r"gläubiger(?:in)?|glaeubiger(?:in)?|schuldner(?:in)?|"
    r"mandant(?:in)?|auftraggeber(?:in)?|auftragnehmer(?:in)?|"
    r"antragsteller(?:in)?|antragsgegner(?:in)?|"
    r"darlehensnehmer(?:in)?|darlehensgeber(?:in)?|"
    r"käufer(?:in)?|kaeufer(?:in)?|verkäufer(?:in)?|verkaeufer(?:in)?|"
    r"versicherungsnehmer(?:in)?|gesellschafter(?:in)?|"
    r"geschäftsführer(?:in)?|geschaeftsfuehrer(?:in)?|"
    r"bürge|buerge|bürgin|buergin)\s*[:,-]?\s*$",
    flags=re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Exclude-Patterns (Schutz vor Fehlalarmen)
# ---------------------------------------------------------------------------

_MONEY_EXCLUDE_RE = re.compile(r"\b\d{1,3}(?:\.\d{3})*,\d{2}\s?(?:EUR|€)\b")
_DATE_EXCLUDE_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b")
_CASE_ID_EXCLUDE_RE = re.compile(r"\b[A-Z]{1,4}[-/][A-Z0-9-]{3,}\b")

# ---------------------------------------------------------------------------
# Adress-Detection-Patterns
# ---------------------------------------------------------------------------

_ADDR_STREET_RE = re.compile(
    r"\b(?:[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+\s+){0,4}"
    r"(?:[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+(?:straße|str\.|strasse|weg|allee|platz|gasse|ring|"
    r"ufer|damm|chaussee|promenade|stieg|steig)"
    r"|Straße|Strasse|Str\.|Weg|Allee|Platz|Gasse|Ring|Ufer|Damm|Chaussee|Promenade|Stieg|Steig)"
    r"\s+\d{1,4}[a-zA-Z]?(?:\s*,\s*|\s+)?(?:\d{5}\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+"
    r"(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+){0,2})?\b",
    re.IGNORECASE,
)
_ADDR_PLZ_CITY_RE = re.compile(
    r"\b\d{5}\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+){0,2}\b"
)

# ---------------------------------------------------------------------------
# External/Party-Meta-Patterns
# ---------------------------------------------------------------------------

_EXTERNAL_PARTY_META_LINE_RE = re.compile(
    r"^\s*(?:dokumenttyp|kläger(?:in)?|klaeger(?:in)?|beklagt(?:e|er|en)?|partei(?:en)?|"
    r"mandant(?:in)?|gegner(?:in)?|vertret(?:er|ung)|anschrift|adresse|sitz|"
    r"rechtsanw[aä]lt(?:in)?|kanzlei)\b",
    flags=re.IGNORECASE,
)
_EXTERNAL_ORG_RE = re.compile(
    r"\b(?:[A-ZÄÖÜ0-9][A-Za-zÄÖÜäöüß0-9&'.,\-]*\s+){0,6}"
    r"(?:GmbH|AG|UG(?:\s*\(haftungsbeschränkt\))?|Partnerschaft(?:sgesellschaft)?|"
    r"Kanzlei|&\s*Partner)\b(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&'.,\-]*)*",
    flags=re.IGNORECASE,
)
# Bug 1 (P1): Standalone-ORG-Detection für Unternehmensname ohne Rollenkontext.
# Matcht Firmennamen mit deutschen Rechtsform-Suffixen (erweiterter Satz).
# Wird in pseudonymize_people() VOR _PERSON_CANDIDATE_RE eingesetzt, damit
# kein Teilname (z.B. "Bäckerei Sonnenschein") als PERSON maskiert wird
# und der Suffix ("GmbH & Co. KG") im Klartext bleibt.
_STANDALONE_ORG_RE = re.compile(
    r"\b(?:[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&'.\-]+\s+){1,5}"
    r"(?:GmbH(?:\s*&\s*Co\.?\s*(?:KGaA|KG|OHG))?|AG|UG(?:\s*\(haftungsbeschränkt\))?|"
    r"KG|OHG|GbR|e\.?\s*K\.|SE|Co\.?\s*(?:KGaA|KG|OHG)|"
    r"&\s*Co\.?\s*(?:KGaA|KG|OHG)|&\s*Partner(?:schaft)?|"
    r"Partnerschaft(?:sgesellschaft)?|PartG(?:\s*mbB)?|mbB)\b",
)
# FIX 4 (P1): Post-Processing-Schritt – bereinigt verbleibende Rechtsform-Suffixe
# nach einem Platzhalter. Sicherheitsnetz für Fälle in denen ein Unternehmensname
# nur teilweise durch ein vorheriges Pattern erfasst wurde.
# BLOCKER 2: Erweitert um alle deutschen Rechtsform-Suffixe inkl. "& Co.", GmbH, AG etc.
_ORPHAN_COMPANY_SUFFIX_RE = re.compile(
    r"(\[[A-Z][A-Z_0-9]*_\d+\])"
    r"\s*(?:"
    r"&\s*Co\.?\s*(?:KGaA|KG|OHG)\b"         # & Co. KG, & Co. KGaA, & Co. OHG
    r"|&\s*Co\.?"                              # & Co. (alleinstehend)
    r"|GmbH(?:\s*&\s*Co\.?\s*(?:KGaA|KG|OHG))?"  # GmbH, GmbH & Co. KG
    r"|AG\b"
    r"|KG\b"
    r"|UG(?:\s*\(haftungsbeschränkt\))?\b"
    r"|OHG\b"
    r"|GbR\b"
    r"|e\.?\s*V\.?"
    r"|eG\b"
    r"|SE\b"
    r"|mbH\b"
    r"|PartG(?:\s*mbB)?\b"
    r"|mbB\b"
    r"|&\s*Partner(?:schaft)?\b"
    r")",
)

# ---------------------------------------------------------------------------
# BIC-Validation
# ---------------------------------------------------------------------------

# FIX 5 (P2): Gültige ISO-3166-1-Alpha-2-Ländercodes für BIC/SWIFT-Validierung.
# Verhindert Fehlalarme für Großbuchstaben-Wörter wie "SCHRIFTSATZ" oder "NOVEMBER",
# deren Positionen 5-6 keinen gültigen Ländercode bilden.
_BIC_VALID_COUNTRY_CODES: frozenset[str] = frozenset({
    "AD", "AE", "AF", "AG", "AI", "AL", "AM", "AO", "AQ", "AR", "AS", "AT",
    "AU", "AW", "AX", "AZ", "BA", "BB", "BD", "BE", "BF", "BG", "BH", "BI",
    "BJ", "BL", "BM", "BN", "BO", "BQ", "BR", "BS", "BT", "BV", "BW", "BY",
    "BZ", "CA", "CC", "CD", "CF", "CG", "CH", "CI", "CK", "CL", "CM", "CN",
    "CO", "CR", "CU", "CV", "CW", "CX", "CY", "CZ", "DE", "DJ", "DK", "DM",
    "DO", "DZ", "EC", "EE", "EG", "EH", "ER", "ES", "ET", "FI", "FJ", "FK",
    "FM", "FO", "FR", "GA", "GB", "GD", "GE", "GF", "GG", "GH", "GI", "GL",
    "GM", "GN", "GP", "GQ", "GR", "GS", "GT", "GU", "GW", "GY", "HK", "HM",
    "HN", "HR", "HT", "HU", "ID", "IE", "IL", "IM", "IN", "IO", "IQ", "IR",
    "IS", "IT", "JE", "JM", "JO", "JP", "KE", "KG", "KH", "KI", "KM", "KN",
    "KP", "KR", "KW", "KY", "KZ", "LA", "LB", "LC", "LI", "LK", "LR", "LS",
    "LT", "LU", "LV", "LY", "MA", "MC", "MD", "ME", "MF", "MG", "MH", "MK",
    "ML", "MM", "MN", "MO", "MP", "MQ", "MR", "MS", "MT", "MU", "MV", "MW",
    "MX", "MY", "MZ", "NA", "NC", "NE", "NF", "NG", "NI", "NL", "NO", "NP",
    "NR", "NU", "NZ", "OM", "PA", "PE", "PF", "PG", "PH", "PK", "PL", "PM",
    "PN", "PR", "PS", "PT", "PW", "PY", "QA", "RE", "RO", "RS", "RU", "RW",
    "SA", "SB", "SC", "SD", "SE", "SG", "SH", "SI", "SJ", "SK", "SL", "SM",
    "SN", "SO", "SR", "SS", "ST", "SV", "SX", "SY", "SZ", "TC", "TD", "TF",
    "TG", "TH", "TJ", "TK", "TL", "TM", "TN", "TO", "TR", "TT", "TV", "TW",
    "TZ", "UA", "UG", "UM", "US", "UY", "UZ", "VA", "VC", "VE", "VG", "VI",
    "VN", "VU", "WF", "WS", "XK", "YE", "YT", "ZA", "ZM", "ZW",
})


def _is_valid_bic(bic: str) -> bool:
    """Prüft ob ein gematchter String ein valides BIC-Format hat.

    Validiert: Länge 8 oder 11 Zeichen, Positionen 5-6 sind ein gültiger
    ISO-3166-1-Alpha-2-Ländercode.
    """
    s = bic.strip()
    if len(s) not in (8, 11):
        return False
    return s[4:6].upper() in _BIC_VALID_COUNTRY_CODES

# ---------------------------------------------------------------------------
# _RegexRule-Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RegexRule:
    pattern: re.Pattern
    mask_type: str
    source_group: str | int = 0
    render: Callable[[re.Match[str], str], str] | None = None
    # FIX 5 (P2): Optionaler Validator – wenn gesetzt, wird der gematchte source-
    # String geprüft; gibt der Validator False zurück, wird der Match nicht maskiert.
    validate: Callable[[str], bool] | None = None

# ---------------------------------------------------------------------------
# Render-Funktionen für _RegexRule
# ---------------------------------------------------------------------------


def _render_full_placeholder(_: re.Match[str], placeholder: str) -> str:
    return placeholder


def _render_role_and_placeholder(match: re.Match[str], placeholder: str) -> str:
    role = str(match.group("role") or "").strip()
    return f"{role}: {placeholder}" if role else placeholder


def _render_court_and_placeholder(match: re.Match[str], placeholder: str) -> str:
    court = str(match.group("court") or "").strip()
    return f"{court} {placeholder}" if court else placeholder


def _render_birthdate_placeholder(match: re.Match[str], placeholder: str) -> str:
    """Erhält den Kontext 'geboren am' / 'geb.' und ersetzt nur das Datum."""
    return f"{match.group(1)}{placeholder}"

# ---------------------------------------------------------------------------
# ORG-Name-Validator
# ---------------------------------------------------------------------------

# P1-A: Validator – lehnt Namen ab, die mit Rechtsform-Suffix enden (→ ORG, nicht PERSON).
_ORG_SUFFIX_ENDS_RE = re.compile(
    r"(?:GmbH|AG|UG|KG|OHG|GbR|SE|mbH|mbB|eG|Inc\.?|Ltd\.?|S\.?A\.?|e\.?\s*V\.?|e\.?\s*K\.?"
    r"|PartG|Partner(?:schaft)?|Partnerschaft(?:sgesellschaft)?)\s*$",
    re.IGNORECASE,
)


def _is_not_org_name(name: str) -> bool:
    """Gibt False zurück wenn der Name ein Rechtsform-Suffix hat (→ ist eine Firma)."""
    return not _ORG_SUFFIX_ENDS_RE.search(str(name or "").strip())

# ---------------------------------------------------------------------------
# _REGEX_RULES – geordnete Liste aller Regex-Redaction-Regeln
# ---------------------------------------------------------------------------

_REGEX_RULES: list[_RegexRule] = [
    _RegexRule(
        pattern=re.compile(
            r"\b(?:[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+\s+){0,4}"
            r"(?:[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+(?:straße|str\.|strasse|weg|allee|platz|gasse|ring|damm|ufer|"
            r"chaussee|promenade|stieg|steig)"
            r"|Straße|Strasse|Str\.|Weg|Allee|Platz|Gasse|Ring|Ufer|Damm|Chaussee|Promenade|Stieg|Steig)"
            r"\s+\d{1,4}[a-zA-Z]?(?:\s*,\s*|\s+)\d{5}\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+"
            r"(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+){0,2}\b",
            re.IGNORECASE,
        ),
        mask_type="ADDR",
    ),
    # P1-A: validate=_is_not_org_name lehnt Namen mit Rechtsform-Suffix ab →
    # diese werden stattdessen in pseudonymize_people als ORG/PARTEI maskiert.
    _RegexRule(
        pattern=re.compile(
            r"\b(?P<role>Klägerin|Kläger|Beklagte|Beklagter|Zeuge|Zeugin|"
            r"Arbeitgeber(?:in)?|Arbeitnehmer(?:in)?|Vermieter(?:in)?|Mieter(?:in)?|"
            r"Gläubiger(?:in)?|Schuldner(?:in)?|Mandant(?:in)?|"
            r"Auftraggeber(?:in)?|Auftragnehmer(?:in)?|"
            r"Antragsteller(?:in)?|Antragsgegner(?:in)?|"
            r"Darlehensnehmer(?:in)?|Darlehensgeber(?:in)?|"
            r"Versicherungsnehmer(?:in)?|Gesellschafter(?:in)?|"
            r"Geschäftsführer(?:in)?|Bürge|Bürgin)\s*:\s*"
            r"(?P<name>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+){0,2})\b"
        ),
        mask_type="PERSON",
        source_group="name",
        render=_render_role_and_placeholder,
        validate=_is_not_org_name,
    ),
    # Bug 1 (P1): Kanzleinamen mit Rechtsform-Suffix (z.B. "RA Dr. Hoffmann & Partner")
    # müssen vollständig als [ORG_N] maskiert werden, nicht als [PERSON_N] + Klartextsuffix.
    # Diese Regel läuft VOR der RA-PERSON-Regel, damit der vollständige Kanzleiname
    # (inkl. "& Partner / & Co. KG") in einem Schritt ersetzt wird.
    _RegexRule(
        pattern=re.compile(
            r"\b(?:Rechtsanwältin|Rechtsanwalt|RA)\s+"
            r"(?:Dr\.\s+)?[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+){0,2}"
            r"\s*&\s*(?:Partner(?:schaft)?|Co\.?\s*(?:KG|OHG))\b"
        ),
        mask_type="ORG",
    ),
    _RegexRule(
        pattern=re.compile(
            r"\b(?:Rechtsanwältin|Rechtsanwalt|RA)\s+"
            r"(?:Dr\.\s+)?[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+){0,2}\b"
        ),
        mask_type="PERSON",
    ),
    _RegexRule(
        pattern=re.compile(
            r"\b(?:[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+\s+){0,4}"
            r"(?:[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+(?:straße|str\.|strasse|weg|allee|platz|gasse|ring|damm|ufer|chaussee|stieg|steig)"
            r"|Straße|Strasse|Str\.|Weg|Allee|Platz|Gasse|Ring|Ufer|Damm|Chaussee|Promenade|Stieg|Steig)"
            r"\s+\d{1,4}[a-zA-Z]?\b",
            re.IGNORECASE,
        ),
        mask_type="ADDR",
    ),
    _RegexRule(
        pattern=re.compile(
            r"\b\d{5}\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+)*\b"
        ),
        mask_type="ADDR",
    ),
    _RegexRule(pattern=re.compile(r"\b[A-Z]{2}\d{2}(?:[\s\-]*\d){10,30}\b"), mask_type="IBAN"),
    # Bug 3 (P0): BIC-Codes (8 oder 11 Zeichen, SWIFT-Format) maskieren.
    # Format: 4 Buchstaben (Bank) + 2 Buchstaben (Land) + 2 alphanumerisch (Ort)
    # + optional 3 alphanumerisch (Filiale) = 8 oder 11 Zeichen.
    # FIX 5 (P2): validate=_is_valid_bic prüft ob Positionen 5-6 ein gültiger
    # ISO-3166-1-Alpha-2-Ländercode sind. Verhindert Fehlalarme für deutsche
    # Großbuchstaben-Wörter wie "SCHRIFTSATZ" (IF) oder "NOVEMBER" (MB).
    _RegexRule(
        pattern=re.compile(r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b"),
        mask_type="BIC",
        validate=_is_valid_bic,
    ),
    # Bug 2a (P0): Geburtsdaten maskieren – Kontextmuster "geboren am" / "geb.".
    # source_group=2 → nur das Datum wird als geheimer Wert gespeichert.
    # render → Kontext ("geboren am ") bleibt erhalten, Datum wird ersetzt.
    # FIX 3 (P1): Erweitert um textuelles Format "DD. Monatname YYYY" für alle
    # deutschen Monatsnamen (z.B. "14. März 1982", "22. September 1968").
    # Die vier Regeln decken alle Kombinationen aus DD.MM.YYYY und DD. Monatname YYYY
    # mit den Kontextpräfixen "geboren am" und "geb." ab.
    _RegexRule(
        pattern=re.compile(
            r"(geboren\s+am\s+)(\d{1,2}\.\d{1,2}\.\d{2,4})\b",
            re.IGNORECASE,
        ),
        mask_type="DATUM",
        source_group=2,
        render=_render_birthdate_placeholder,
    ),
    _RegexRule(
        pattern=re.compile(
            r"(geboren\s+am\s+)(\d{1,2}\.\s+"
            r"(?:Januar|Februar|M(?:ä|ae)rz|April|Mai|Juni|Juli|August|"
            r"September|Oktober|November|Dezember)\s+\d{4})\b",
            re.IGNORECASE,
        ),
        mask_type="DATUM",
        source_group=2,
        render=_render_birthdate_placeholder,
    ),
    _RegexRule(
        pattern=re.compile(
            r"(geb\.?\s+)(\d{1,2}\.\d{1,2}\.\d{2,4})\b",
            re.IGNORECASE,
        ),
        mask_type="DATUM",
        source_group=2,
        render=_render_birthdate_placeholder,
    ),
    _RegexRule(
        pattern=re.compile(
            r"(geb\.?\s+)(\d{1,2}\.\s+"
            r"(?:Januar|Februar|M(?:ä|ae)rz|April|Mai|Juni|Juli|August|"
            r"September|Oktober|November|Dezember)\s+\d{4})\b",
            re.IGNORECASE,
        ),
        mask_type="DATUM",
        source_group=2,
        render=_render_birthdate_placeholder,
    ),
    # P1-B: Zusätzliche Geburtsdatum-Kontextmuster – "geboren:", "Geburtstag:", "Geburtsdatum:"
    _RegexRule(
        pattern=re.compile(
            r"(geboren\s*:\s*)(\d{1,2}\.\d{1,2}\.\d{2,4})\b",
            re.IGNORECASE,
        ),
        mask_type="DATUM",
        source_group=2,
        render=_render_birthdate_placeholder,
    ),
    _RegexRule(
        pattern=re.compile(
            r"(geboren\s*:\s*)(\d{1,2}\.\s+"
            r"(?:Januar|Februar|M(?:ä|ae)rz|April|Mai|Juni|Juli|August|"
            r"September|Oktober|November|Dezember)\s+\d{4})\b",
            re.IGNORECASE,
        ),
        mask_type="DATUM",
        source_group=2,
        render=_render_birthdate_placeholder,
    ),
    _RegexRule(
        pattern=re.compile(
            r"((?:Geburtstag|Geburtsdatum)\s*:\s*)(\d{1,2}\.\d{1,2}\.\d{2,4})\b",
            re.IGNORECASE,
        ),
        mask_type="DATUM",
        source_group=2,
        render=_render_birthdate_placeholder,
    ),
    _RegexRule(
        pattern=re.compile(
            r"((?:Geburtstag|Geburtsdatum)\s*:\s*)(\d{1,2}\.\s+"
            r"(?:Januar|Februar|M(?:ä|ae)rz|April|Mai|Juni|Juli|August|"
            r"September|Oktober|November|Dezember)\s+\d{4})\b",
            re.IGNORECASE,
        ),
        mask_type="DATUM",
        source_group=2,
        render=_render_birthdate_placeholder,
    ),
    _RegexRule(
        pattern=re.compile(r"\b(?:Az\.?\s*)?\d{1,3}\s+[A-Za-z]{1,5}\s+\d{1,6}/\d{2,4}\b"),
        mask_type="AKTENZEICHEN",
    ),
    _RegexRule(
        pattern=re.compile(
            r"(?:(?<!\w)(?:\+49|0049)[\s]?\d{2,4}(?:[\s\/\-]\d{2,8}){1,4})"
            r"|(?:\b0\d{2,4}[\s\/\-]\d{2,8}(?:[\s\/\-]\d{2,8}){0,3}\b)"
        ),
        mask_type="PHONE",
    ),
    _RegexRule(pattern=re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), mask_type="EMAIL"),
    _RegexRule(
        pattern=re.compile(r"\b(?:DE\s?)?\d{9,11}\b(?=\s*(?:USt|Steuer|St\.-Nr))"),
        mask_type="TAXID",
    ),
    # Bug 2 (P1): Handelsregisternummern maskieren (HRB/HRA/PR/VR + Ziffernfolge).
    # Referenz-Muster: analog zu BIC/IBAN auf dem fix/redaction-and-fristen-p0-Branch.
    # In M&A-Dokumenten identifiziert die HRB-Nummer die Zielgesellschaft eindeutig.
    _RegexRule(
        pattern=re.compile(r"\b(?:HRB|HRA|PR|VR)\s+\d+\b"),
        mask_type="HRB",
    ),
]

# ---------------------------------------------------------------------------
# Company-Name-Patterns (für _apply_company_redaction)
# ---------------------------------------------------------------------------

_COMPANY_ARTICLE_RE = re.compile(
    r"^(?:Der|Die|Das|Den|Dem|Des|Ein|Eine|Einen|Einem|Zur|Zum)\s+",
    flags=re.IGNORECASE,
)
_COMPANY_NAME_RE = re.compile(
    r"\b(?:[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&'.\-]+\s+){1,4}"
    r"(?:GmbH(?:\s*&\s*Co\.?\s*(?:KGaA|KG|OHG))?|AG|UG(?:\s*\(haftungsbeschränkt\))?|"
    r"KG|OHG|e\.?\s?K\.|GbR|SE|Partnerschaft(?:sgesellschaft)?|mbH|Co\.?\s*(?:KGaA|KG|OHG)|"
    r"&\s*Co\.?\s*(?:KGaA|KG|OHG)|&\s*Partner(?:schaft)?|PartG(?:\s*mbB)?|mbB)\b",
)

# ---------------------------------------------------------------------------
# External-Mask Post-Processing-Patterns
# ---------------------------------------------------------------------------

_EXTERNAL_SOURCE_LINE_RE = re.compile(r"^\s*Quelle\s+S\.\s*\S", re.IGNORECASE)

# Post-Processing: Klarname direkt nach Platzhalter entfernen
# z.B. "[PERSON_3]. Anna Weiß" → "[PERSON_3]"
_EXT_NAME_AFTER_PLACEHOLDER_RE = re.compile(
    r'(\[(?:PERSON|PARTEI|ORG)_\d+\])[.\s]+[A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)*',
    re.UNICODE,
)

# Post-Processing: Hausnummer direkt nach Platzhalter entfernen
# z.B. "[PERSON_1] 77" oder "[ADDR_2] 4a" → nur Platzhalter
_EXT_HAUSNR_AFTER_PLACEHOLDER_RE = re.compile(
    r'(\[(?:PERSON|PARTEI|ORG|ADDR)_\d+\])\s+\d{1,4}[a-zA-Z]?(?=\s|$)',
    re.UNICODE,
)

# ---------------------------------------------------------------------------
# Post-Redaction PII-Validator (fail-closed für Mandantenexport)
# ---------------------------------------------------------------------------

# Entfernt alle bekannten Redaction-Platzhalter vor PII-Validierung.
# Verhindert False Positives durch Zahlenfolgen in Platzhaltern.
# WICHTIG: PARTEI muss hier explizit gelistet sein, damit Klageschrift-Exporte
# mit [PARTEI_1]/[PARTEI_2]-Labels nicht fälschlicherweise vom Leak-Guard
# geblockt werden (z. B. durch Ziffernfolgen in "[PARTEI_1]" die einem
# Telefon- oder PLZ-Muster ähneln könnten).
_VALIDATOR_PLACEHOLDER_PATTERN = re.compile(
    r'\[(?:ADDR|PERSON|BIC|EMAIL|IBAN|PARTEI|MASK|ORG|'
    r'TELEFON|PHONE|DATUM|AKTENZEICHEN|BETRAG|ADRESSE|'
    r'DICT|TAXID|FIRMA|HRB)(?:_[A-Z0-9]+)?\]',
    re.IGNORECASE,
)

# Pattern die nach Redaction NICHT mehr im Mandantenexport vorkommen dürfen
_POST_REDACTION_PII_PATTERNS = [
    # Straße + Hausnummer
    re.compile(
        r'\b[A-ZÄÖÜ][a-zäöüß\-]+'
        r'(?:straße|str\.|gasse|weg|allee|platz|ring|damm|chaussee|ufer)'
        r'\s+\d+[a-zA-Z]?\b',
        re.IGNORECASE,
    ),
    # PLZ + Stadt
    re.compile(r'\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß\-]+\b'),
    # IBAN
    re.compile(r'\bDE\d{2}[\s\d]{15,25}\b'),
    # E-Mail
    re.compile(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b'),
    # Telefon
    re.compile(r'(?<!\d)(?:\+49[\s]?|0)[1-9][0-9]{2,}(?:[\s\/\-][0-9]{2,})+|(?<!\d)(?:\+49|0)[1-9][0-9]{4,}|(?<!\d)0[1-9][0-9]{1,}\/[0-9]{3,}'),
]

_POST_REDACTION_PII_PATTERN_NAMES = [
    "STRASSE_HAUSNR",
    "PLZ_STADT",
    "IBAN",
    "EMAIL",
    "TELEFON",
]

# ---------------------------------------------------------------------------
# Shared Engine-Helfer (gebraucht von redaction.py UND pseudonymizer.py)
# ---------------------------------------------------------------------------


def _new_placeholder(mask_type: str, counters: dict[str, int]) -> str:
    counters[mask_type] = int(counters.get(mask_type, 0)) + 1
    return f"[{mask_type}_{counters[mask_type]}]"


def _extract_mask_type(placeholder: str) -> str:
    match = _PLACEHOLDER_RE.fullmatch(str(placeholder or "").strip())
    if not match:
        return "UNKNOWN"
    return str(match.group("kind") or "UNKNOWN")
