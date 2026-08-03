"""governance/redaction_patterns.py – Regex patterns, constants, and rule set.

Extracted from governance/redaction.py (W2 god-file split).
Contains all compiled regular expressions, stoplists,
the _RegexRule dataclass, render functions, validators,
and the _REGEX_RULES list.

Note: the patterns below target German-language legal/business documents
(company suffixes, address formats, honorifics, etc.) and are intentionally
not translated — they match content, not code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

# ---------------------------------------------------------------------------
# Base patterns and charsets
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
# Person/party detection patterns
# ---------------------------------------------------------------------------

# Robust, simple heuristic for persons with an optional "Dr." title.
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
# PR11 – P9: Company names after a role designation (Auftraggeber/Auftragnehmer
# etc.) are masked in full as a party – including CamelCase names (LegalTech).
# Scanned BEFORE _PARTY_PERSON_RE, so the full company name overrides the
# partial person match (e.g. "Nordstadt LegalTech" without "GmbH").
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
# P1-A: Legal-form suffixes that identify a company name.
# If a name matched via _PARTY_PERSON_RE is directly followed by one of these
# suffixes, it must be classified as ORG (not PERSON).
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
# Stoplists and token sets
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
# Stoplist: common legal terms / table headers / boilerplate that could
# be mistakenly detected as PERSON.
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
# Regex for lines consisting only of uppercase letters, digits, and special
# characters (table headers, case-number fragments).
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
# PR11 – P5/P6: Extensible whitelist of legal sentence-starting words and
# common contract nouns that are NEVER person names.
# Each token here (lowercase) blocks a false-positive match in
# _is_person_candidate when it appears as the first or second word.
_NOT_A_PERSON_TOKENS: frozenset[str] = frozenset(
    {
        # Sentence-starting words (appear after a line break / clause heading)
        "soweit",
        "während",
        "waehrend",
        "gegenüber",
        "gegenueber",
        "sofern",
        "insoweit",
        "unbeschadet",
        # Clause and section headings
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
        # Table-start / boilerplate nouns
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
# Exclude patterns (guard against false positives)
# ---------------------------------------------------------------------------

_MONEY_EXCLUDE_RE = re.compile(r"\b\d{1,3}(?:\.\d{3})*,\d{2}\s?(?:EUR|€)\b")
_DATE_EXCLUDE_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b")
_CASE_ID_EXCLUDE_RE = re.compile(r"\b[A-Z]{1,4}[-/][A-Z0-9-]{3,}\b")

# ---------------------------------------------------------------------------
# Address detection patterns
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
# External/party metadata patterns
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
# Bug 1 (P1): Standalone-ORG detection for company names without role context.
# Matches company names with German legal-form suffixes (extended set).
# Used in pseudonymize_people() BEFORE _PERSON_CANDIDATE_RE, so no partial
# name (e.g. "Bäckerei Sonnenschein") gets masked as PERSON and the suffix
# ("GmbH & Co. KG") stays in plaintext.
_STANDALONE_ORG_RE = re.compile(
    r"\b(?:[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&'.\-]+\s+){1,5}"
    r"(?:GmbH(?:\s*&\s*Co\.?\s*(?:KGaA|KG|OHG))?|AG|UG(?:\s*\(haftungsbeschränkt\))?|"
    r"KG|OHG|GbR|e\.?\s*K\.|SE|Co\.?\s*(?:KGaA|KG|OHG)|"
    r"&\s*Co\.?\s*(?:KGaA|KG|OHG)|&\s*Partner(?:schaft)?|"
    r"Partnerschaft(?:sgesellschaft)?|PartG(?:\s*mbB)?|mbB)\b",
)
# FIX 4 (P1): Post-processing step – cleans up remaining legal-form suffixes
# after a placeholder. Safety net for cases where a company name was only
# partially captured by an earlier pattern.
# BLOCKER 2: Extended to cover all German legal-form suffixes incl. "& Co.", GmbH, AG, etc.
_ORPHAN_COMPANY_SUFFIX_RE = re.compile(
    r"(\[[A-Z][A-Z_0-9]*_\d+\])"
    r"\s*(?:"
    r"&\s*Co\.?\s*(?:KGaA|KG|OHG)\b"         # & Co. KG, & Co. KGaA, & Co. OHG
    r"|&\s*Co\.?"                              # & Co. (standalone)
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
# BIC validation
# ---------------------------------------------------------------------------

# FIX 5 (P2): Valid ISO 3166-1 alpha-2 country codes for BIC/SWIFT validation.
# Prevents false positives for uppercase words like "SCHRIFTSATZ" or "NOVEMBER",
# whose positions 5-6 do not form a valid country code.
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
    """Checks whether a matched string has a valid BIC format.

    Validates: length 8 or 11 characters, positions 5-6 are a valid
    ISO 3166-1 alpha-2 country code.
    """
    s = bic.strip()
    if len(s) not in (8, 11):
        return False
    return s[4:6].upper() in _BIC_VALID_COUNTRY_CODES

# ---------------------------------------------------------------------------
# _RegexRule dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RegexRule:
    pattern: re.Pattern
    mask_type: str
    source_group: str | int = 0
    render: Callable[[re.Match[str], str], str] | None = None
    # FIX 5 (P2): Optional validator – if set, the matched source string is
    # checked; if the validator returns False, the match is not masked.
    validate: Callable[[str], bool] | None = None

# ---------------------------------------------------------------------------
# Render functions for _RegexRule
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
    """Preserves the context 'geboren am' / 'geb.' and replaces only the date."""
    return f"{match.group(1)}{placeholder}"

# ---------------------------------------------------------------------------
# ORG name validator
# ---------------------------------------------------------------------------

# P1-A: Validator – rejects names ending with a legal-form suffix (→ ORG, not PERSON).
_ORG_SUFFIX_ENDS_RE = re.compile(
    r"(?:GmbH|AG|UG|KG|OHG|GbR|SE|mbH|mbB|eG|Inc\.?|Ltd\.?|S\.?A\.?|e\.?\s*V\.?|e\.?\s*K\.?"
    r"|PartG|Partner(?:schaft)?|Partnerschaft(?:sgesellschaft)?)\s*$",
    re.IGNORECASE,
)


def _is_not_org_name(name: str) -> bool:
    """Returns False if the name has a legal-form suffix (→ it's a company)."""
    return not _ORG_SUFFIX_ENDS_RE.search(str(name or "").strip())

# ---------------------------------------------------------------------------
# _REGEX_RULES – ordered list of all regex redaction rules
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
    # P1-A: validate=_is_not_org_name rejects names with a legal-form suffix →
    # these get masked instead as ORG/PARTEI in pseudonymize_people.
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
    # Bug 1 (P1): Firm names with a legal-form suffix (e.g. "RA Dr. Hoffmann & Partner")
    # must be masked in full as [ORG_N], not as [PERSON_N] + plaintext suffix.
    # This rule runs BEFORE the RA-PERSON rule, so the full firm name
    # (incl. "& Partner / & Co. KG") is replaced in one step.
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
    # Bug 3 (P0): Masks BIC codes (8 or 11 characters, SWIFT format).
    # Format: 4 letters (bank) + 2 letters (country) + 2 alphanumeric (location)
    # + optional 3 alphanumeric (branch) = 8 or 11 characters.
    # FIX 5 (P2): validate=_is_valid_bic checks whether positions 5-6 are a
    # valid ISO 3166-1 alpha-2 country code. Prevents false positives for
    # German uppercase words like "SCHRIFTSATZ" (IF) or "NOVEMBER" (MB).
    _RegexRule(
        pattern=re.compile(r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b"),
        mask_type="BIC",
        validate=_is_valid_bic,
    ),
    # Bug 2a (P0): Masks dates of birth – context pattern "geboren am" / "geb.".
    # source_group=2 → only the date is stored as the secret value.
    # render → context ("geboren am ") is preserved, the date is replaced.
    # FIX 3 (P1): Extended to cover the textual format "DD. Monatname YYYY" for
    # all German month names (e.g. "14. März 1982", "22. September 1968").
    # The four rules cover all combinations of DD.MM.YYYY and DD. Monatname YYYY
    # with the context prefixes "geboren am" and "geb.".
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
    # P1-B: Additional date-of-birth context patterns – "geboren:", "Geburtstag:", "Geburtsdatum:"
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
    # Bug 2 (P1): Masks commercial-register numbers (HRB/HRA/PR/VR + digit sequence).
    # Reference pattern: analogous to BIC/IBAN on the fix/redaction-and-fristen-p0 branch.
    # In M&A documents, the HRB number uniquely identifies the target company.
    _RegexRule(
        pattern=re.compile(r"\b(?:HRB|HRA|PR|VR)\s+\d+\b"),
        mask_type="HRB",
    ),
]

# ---------------------------------------------------------------------------
# Company name patterns (for _apply_company_redaction)
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
# External-mask post-processing patterns
# ---------------------------------------------------------------------------

_EXTERNAL_SOURCE_LINE_RE = re.compile(r"^\s*Quelle\s+S\.\s*\S", re.IGNORECASE)

# Post-processing: removes a plaintext name directly after a placeholder
# e.g. "[PERSON_3]. Anna Weiß" → "[PERSON_3]"
_EXT_NAME_AFTER_PLACEHOLDER_RE = re.compile(
    r'(\[(?:PERSON|PARTEI|ORG)_\d+\])[.\s]+[A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)*',
    re.UNICODE,
)

# Post-processing: removes a house number directly after a placeholder
# e.g. "[PERSON_1] 77" or "[ADDR_2] 4a" → placeholder only
_EXT_HAUSNR_AFTER_PLACEHOLDER_RE = re.compile(
    r'(\[(?:PERSON|PARTEI|ORG|ADDR)_\d+\])\s+\d{1,4}[a-zA-Z]?(?=\s|$)',
    re.UNICODE,
)

# ---------------------------------------------------------------------------
# Post-redaction PII validator (fail-closed for client export)
# ---------------------------------------------------------------------------

# Removes all known redaction placeholders before PII validation.
# Prevents false positives from digit sequences inside placeholders.
# IMPORTANT: PARTEI must be listed explicitly here, so that complaint/filing
# exports with [PARTEI_1]/[PARTEI_2] labels are not incorrectly blocked by
# the leak guard (e.g. via digit sequences in "[PARTEI_1]" that could
# resemble a phone or postal-code pattern).
_VALIDATOR_PLACEHOLDER_PATTERN = re.compile(
    r'\[(?:ADDR|PERSON|BIC|EMAIL|IBAN|PARTEI|MASK|ORG|'
    r'TELEFON|PHONE|DATUM|AKTENZEICHEN|BETRAG|ADRESSE|'
    r'DICT|TAXID|FIRMA|HRB)(?:_[A-Z0-9]+)?\]',
    re.IGNORECASE,
)

# Patterns that must NOT appear in a client export after redaction
_POST_REDACTION_PII_PATTERNS = [
    # Street + house number
    re.compile(
        r'\b[A-ZÄÖÜ][a-zäöüß\-]+'
        r'(?:straße|str\.|gasse|weg|allee|platz|ring|damm|chaussee|ufer)'
        r'\s+\d+[a-zA-Z]?\b',
        re.IGNORECASE,
    ),
    # Postal code + city
    re.compile(r'\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß\-]+\b'),
    # IBAN
    re.compile(r'\bDE\d{2}[\s\d]{15,25}\b'),
    # Email
    re.compile(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b'),
    # Phone
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
# Shared engine helpers (needed by redaction.py AND pseudonymizer.py)
# ---------------------------------------------------------------------------


def _new_placeholder(mask_type: str, counters: dict[str, int]) -> str:
    counters[mask_type] = int(counters.get(mask_type, 0)) + 1
    return f"[{mask_type}_{counters[mask_type]}]"


def _extract_mask_type(placeholder: str) -> str:
    match = _PLACEHOLDER_RE.fullmatch(str(placeholder or "").strip())
    if not match:
        return "UNKNOWN"
    return str(match.group("kind") or "UNKNOWN")
