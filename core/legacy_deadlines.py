from __future__ import annotations

import re
from datetime import date
from typing import Any


_DEADLINE_CONTEXT_TRIGGERS = (
    "bis spätestens",
    "spätestens",
    "zahlungsfrist",
    "zahlungsziel",
    "frist",
    "fristablauf",
    "fällig bis",
    "faellig bis",
    "fällig",
    "faellig",
    "zu zahlen bis",
    "zahlung bis",
    "letzte zahlungsaufforderung",
)
_PAYMENT_DEADLINE_TRIGGERS = (
    "zahlung",
    "zahlungsfrist",
    "zahlungsziel",
    "zahlungsaufforderung",
    "fällig",
    "faellig",
    "zu zahlen bis",
    "fällig bis",
    "faellig bis",
)
_KUENDIGUNGSFRIST_TRIGGERS = (
    "kündig",
    "kuendig",
    "kündigungs",
    "kuendigungs",
    "kündigung",
    "kuendigung",
    "beend",
    "beendigung",
    "auflös",
    "aufloes",
    "vertragsende",
    "frist zum",
    "ende des",
    "verlänger",
    "verlaenger",
)
# Reference date: pure date-of-record with no action required
_REFERENCE_DATE_TRIGGERS = (
    "rechnungsdatum",
    "leistungsdatum",
    "vertragsdatum",
    "ausstellungsdatum",
    "beurkundungsdatum",
    "buchungsdatum",
    "eingangsdatum",
    "erstellungsdatum",
    "zustellungsdatum",
    "unterzeichnung am",
    "unterzeichnet am",
    "datum der rechnung",
    "datum des vertrags",
    "datum der leistung",
    "rechnungsnummer",
    "datiert",
    "vom",  # "Rechnung vom 01.01.2025"
    "gewährt",   # "Zahlungsziel gewährt am 19.02.2026"
    "gewaehrt",
    "per überweisung",  # "bezahlt per Überweisung vom 16.06.2024"
    "per ueberweisung",
    "hamburg, den",  # letterhead date
    "münchen, den",
    "muenchen, den",
    "berlin, den",
    "frankfurt, den",
)

# Invoice-number pattern: RE-YYYY-NNNN or generic RE-\d+ → reference-date candidate.
# PR10: format RE-\d{4}-\d{3,4} (e.g. "RE-2026-0142") explicitly covered (previously only RE-\d+).
_INVOICE_ID_RE = re.compile(
    r"\b(?:RE|RG|INV|NR)[-/]?\d{4}[-/]\d{3,4}"  # RE-YYYY-NNNN (specific format)
    r"|\b(?:RE|RG|INV|NR)[-/]?\d+",              # generic format: RE-XXX (fallback)
    re.IGNORECASE,
)

# Procedural-deadline trigger: audit pre-notice etc. → NOT a termination deadline
_VERFAHRENSFRIST_TRIGGERS = (
    "vorankündigung",
    "vorankuendigung",
    "ankündigungsfrist",
    "ankuendigungsfrist",
    "audit",
    "prüfankündigung",
    "pruefankuendigung",
    "vorankündigungsfrist",
    "vorankuendigungsfrist",
)

# PR12: renewal/termination clause pattern.
# Matches: "verlängert sich um 12 Monate ... Frist von 3 Monaten"
_RENEWAL_TERMINATION_RE = re.compile(
    r"verl(?:ä|ae)nger\w*\s+(?:\w+\s+){0,5}um\s+"
    r"(?P<verlaengerung>\d+\s*(?:monate?n?|wochen?|tage?n?|jahre?n?))"
    r".{0,200}?"
    r"frist\s+von\s+"
    r"(?P<kuendigung>\d+\s*(?:monate?n?|wochen?|tage?n?|jahre?n?))",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_text_for_match(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    value = value.replace("ß", "ss")
    return re.sub(r"[^\w\s]", " ", value).strip()


def _contains_trigger(text: str, triggers: tuple[str, ...]) -> bool:
    norm_text = _normalize_text_for_match(text)
    if not norm_text:
        return False
    return any(_normalize_text_for_match(trigger) in norm_text for trigger in triggers)


def _first_date_match(text: str) -> re.Match[str] | None:
    return re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b", str(text or ""))


def _normalize_date_iso(text: str) -> str | None:
    match = _first_date_match(text)
    if not match:
        return None
    day = int(match.group(1))
    month = int(match.group(2))
    year_raw = match.group(3)
    year = int(year_raw)
    if len(year_raw) == 2:
        year += 2000 if year < 70 else 1900
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _deadline_label_from_text(text: str) -> str:
    if _contains_trigger(text, _PAYMENT_DEADLINE_TRIGGERS):
        return "Zahlungsfrist"
    return "Frist/Termin"


_NON_FRIST_ITEM_KEYWORDS: tuple[str, ...] = (
    # Birth dates
    "geboren", "geb.", "geburtsdatum", "geburtstag",
    # Employment start / continued employment
    "beschäftigt seit", "beschaeftigt seit", "eingestellt am",
    "eintritt", "beschäftigungsbeginn", "beschaeftigungsbeginn",
    "tätig seit", "taetig seit", "arbeitsverhältnis seit", "arbeitsverhaeltnis seit",
    "angestellt seit", "arbeitsbeginn", "seit dem", "in kraft seit",
    "weiterbeschäftigung", "weiterbeschaeftigung",
    # Founding dates
    "gegründet", "gegruendet", "gründung", "gruendung",
    "eingetragen", "eintragung", "registriert",
)


def classify_date_category(text: str, label: str = "") -> str:
    """Classifies a date as 'Frist' (deadline), 'Termin' (appointment), or 'Referenzdatum' (reference date).

    - Frist: date by which action must be taken (payment deadline, termination deadline)
    - Termin: concrete appointment requiring action (handover date, lease start)
    - Referenzdatum: pure date-of-record with no action required (invoice date, contract date)

    Priority: reference-date triggers are checked first, because terms like
    "Rechnungsdatum" (invoice date) can also have the word "fällig" (due) nearby.
    FIX 1/3: birth dates and employment-start dates are classified as reference dates.
    """
    context = f"{label} {text}".strip()
    # FIX 1/3: birth dates / employment start / founding dates → reference date (not deadline)
    context_l = context.lower()
    for kw in _NON_FRIST_ITEM_KEYWORDS:
        if kw in context_l:
            return "Referenzdatum"
    # 0) Invoice-number pattern ("RE-XXXX vom Datum") → always a reference date
    # PR10 root cause (string 5): when the full paragraph context is passed in,
    # it contains both the payment deadline ("Zahlung bis spätestens 17.06.2026") and
    # the invoice date ("RE-2026-0142 vom 24.05.2026"). has_strong_deadline is set
    # True by the payment deadline, even though the classified date (label) is the
    # invoice date. Fix: "vom <label>" proximity check takes precedence.
    if _INVOICE_ID_RE.search(context):
        label_stripped = str(label or "").strip()
        if label_stripped:
            _vom_re = re.compile(r"\bvom\s+" + re.escape(label_stripped), re.IGNORECASE)
            if _vom_re.search(context):
                return "Referenzdatum"
        has_strong_deadline = _contains_trigger(context, (
            "bis spätestens", "bis spaetestens", "spätestens", "zahlungsfrist", "fällig bis", "faellig bis",
        ))
        if not has_strong_deadline:
            return "Referenzdatum"
    # 1) Reference date takes precedence (unless explicit deadline triggers are present)
    has_reference = _contains_trigger(context, _REFERENCE_DATE_TRIGGERS)
    has_deadline = _contains_trigger(context, _DEADLINE_CONTEXT_TRIGGERS)
    has_kuendigung = _contains_trigger(context, _KUENDIGUNGSFRIST_TRIGGERS)
    if has_reference and not has_deadline and not has_kuendigung:
        return "Referenzdatum"
    # 2) Procedural deadline (audit pre-notice etc.) → deadline, but NOT a termination deadline
    if _contains_trigger(context, _VERFAHRENSFRIST_TRIGGERS):
        return "Frist"
    # 3) Explicit deadline triggers
    if has_deadline:
        return "Frist"
    if has_kuendigung:
        return "Frist"
    # 4) Appointment triggers: handover, start, hearing etc.
    termin_triggers = (
        "übergabe", "uebergabe", "mietbeginn", "arbeitsbeginn",
        "vertragsbeginn", "anhörung", "anhoerung", "verhandlung",
        "termin", "hauptverhandlung", "ortstermin",
    )
    if _contains_trigger(context, termin_triggers):
        return "Termin"
    return "Frist"


def _resolve_deadline_label(raw_label: str, context: str) -> str:
    label = str(raw_label or "").strip()
    context_text = str(context or "")
    # A procedural deadline (audit pre-notice) must never appear as a termination deadline
    has_verfahrensfrist = _contains_trigger(context_text, _VERFAHRENSFRIST_TRIGGERS)
    has_kuendigung_trigger = _contains_trigger(context_text, _KUENDIGUNGSFRIST_TRIGGERS)
    if "kündigungsfrist" in label.lower() or "kuendigungsfrist" in label.lower():
        if has_verfahrensfrist:
            return "Verfahrensfrist"
        return "Kündigungsfrist" if has_kuendigung_trigger else "Frist/Termin"
    if "referenzdatum" in label.lower():
        return "Referenzdatum"
    if label:
        return label
    if has_verfahrensfrist:
        return "Verfahrensfrist"
    if _contains_trigger(context_text, _PAYMENT_DEADLINE_TRIGGERS):
        return "Zahlungsfrist"
    if has_kuendigung_trigger:
        return "Kündigungsfrist"
    if _contains_trigger(context_text, _REFERENCE_DATE_TRIGGERS):
        if not _contains_trigger(context_text, _DEADLINE_CONTEXT_TRIGGERS + _PAYMENT_DEADLINE_TRIGGERS):
            return "Referenzdatum"
    return _deadline_label_from_text(context_text)


def _deadline_page_or_span(item: dict[str, Any]) -> str:
    citations = item.get("citations")
    if not isinstance(citations, list):
        return ""
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        page = citation.get("page_number")
        if isinstance(page, int):
            return f"p:{page}"
        if isinstance(page, str) and page.strip().isdigit():
            return f"p:{page.strip()}"
        section = str(citation.get("section_hint", "") or "").strip()
        if section:
            return f"s:{section[:40]}"
    return ""


def _deadline_item_quality(item: dict[str, Any]) -> tuple[int, int, int, int]:
    text = str(item.get("text", "") or "")
    snippet = str(item.get("snippet", "") or "")
    context = f"{text} {snippet}".strip()
    trigger_score = 1 if _contains_trigger(context, _DEADLINE_CONTEXT_TRIGGERS) else 0
    kuendigung_score = 1 if _contains_trigger(context, _KUENDIGUNGSFRIST_TRIGGERS) else 0
    return (
        kuendigung_score,
        trigger_score,
        len(snippet),
        len(text),
    )


def _render_legacy_deadline_text(item: dict[str, Any]) -> str:
    text = str(item.get("text", "") or "").strip()
    if not text:
        return ""
    # Bug 1 (P0): drop entries without a parseable DD.MM.YYYY date.
    # Prevents document titles / segment headers from appearing as a deadline/appointment.
    date_match = _first_date_match(text)
    if not date_match:
        return ""
    # Bug 2b (P0): filter out birth dates, employment-start dates, and reference dates.
    # Filter out known non-deadline keywords (continued employment, hire date, …) immediately.
    # Also suppress dates < 2024 without a strong deadline context (hire year 2019 etc.).
    year_raw = date_match.group(3)
    year = int(year_raw)
    if len(year_raw) == 2:
        year += 2000 if year < 70 else 1900
    snippet = str(item.get("snippet", "") or "").strip()
    context = f"{text} {snippet}".lower()
    has_deadline_ctx = _contains_trigger(
        context, _DEADLINE_CONTEXT_TRIGGERS + _KUENDIGUNGSFRIST_TRIGGERS
    )
    for kw in _NON_FRIST_ITEM_KEYWORDS:
        if kw in context:
            return ""
    if year < 2024 and not has_deadline_ctx:
        return ""
    # FIX 6: Normalize date to 4-digit year for deduplication (15.12.24 == 15.12.2024)
    day_n = int(date_match.group(1))
    month_n = int(date_match.group(2))
    normalized_date = f"{day_n:02d}.{month_n:02d}.{year}"
    label = str(item.get("label", "") or "Frist/Termin").strip()
    if not _contains_trigger(text, _DEADLINE_CONTEXT_TRIGGERS):
        return f"{label}: {normalized_date}"
    # Replace the raw date string with the normalized form in the full text
    return text.replace(date_match.group(0), normalized_date, 1)


def dedupe_legacy_deadline_items(items: list[dict[str, Any]], *, limit: int = 25) -> list[dict[str, Any]]:
    best_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for raw in list(items or []):
        text = str(raw.get("text", "") or "").strip()
        if not text:
            continue
        snippet = " ".join(
            str((citation or {}).get("quote_snippet", "") or "").strip()
            for citation in list(raw.get("citations", []) or [])
            if isinstance(citation, dict)
        )
        context_text = f"{text} {snippet}".strip()
        raw_label = str(raw.get("label", "") or "").strip()
        label = _resolve_deadline_label(raw_label, context_text)
        normalized_date = _normalize_date_iso(context_text)
        date_key = normalized_date or _normalize_text_for_match(text)
        page_or_span = _deadline_page_or_span(raw)
        key = (label.casefold(), date_key, page_or_span)
        candidate = {
            "text": text,
            "label": label,
            "date": date_key,
            "page_or_span": page_or_span,
            "snippet": snippet,
            "key": key,
        }
        llm_cat = str(raw.get("date_category", "") or "").strip()
        if llm_cat in ("Frist", "Termin", "Referenzdatum"):
            candidate["date_category"] = llm_cat
        existing = best_by_key.get(key)
        if existing is None:
            best_by_key[key] = candidate
            order.append(key)
        elif _deadline_item_quality(candidate) > _deadline_item_quality(existing):
            best_by_key[key] = candidate
    return [best_by_key[key] for key in order[: max(1, int(limit))]]


def _resolve_date_category(item: dict[str, Any]) -> str:
    """Returns the date_category — the LLM-provided value takes precedence over classify_date_category."""
    llm_category = str(item.get("date_category", "") or "").strip()
    if llm_category in ("Frist", "Termin", "Referenzdatum"):
        return llm_category
    label = str(item.get("label", "") or "")
    text = str(item.get("text", "") or "")
    return classify_date_category(text, label)


def build_legacy_deadlines(items: list[dict[str, Any]], *, limit: int = 25) -> list[str]:
    """Builds the legacy deadline strings. Reference dates are filtered out."""
    out: list[str] = []
    seen: set[str] = set()
    for item in dedupe_legacy_deadline_items(items, limit=limit):
        if _resolve_date_category(item) == "Referenzdatum":
            continue
        rendered = _render_legacy_deadline_text(item)
        if not rendered:
            continue
        dedupe_key = rendered.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(rendered)
        if len(out) >= max(1, int(limit)):
            break
    return out


def build_reference_dates(items: list[dict[str, Any]], *, limit: int = 10) -> list[str]:
    """Extracts only reference dates (invoice date, contract date, etc.)."""
    out: list[str] = []
    seen: set[str] = set()
    for item in dedupe_legacy_deadline_items(items, limit=limit):
        if _resolve_date_category(item) != "Referenzdatum":
            continue
        rendered = _render_legacy_deadline_text(item)
        if not rendered:
            continue
        dedupe_key = rendered.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(rendered)
        if len(out) >= max(1, int(limit)):
            break
    return out


def extract_renewal_and_termination(text: str) -> list[tuple[str, str]]:
    """Extracts renewal and termination deadlines from clauses of the form
    'verlängert sich um X Monate ... Frist von Y Monaten' (renews for X months ... Y months' notice).

    Returns:
        List of (value, label) tuples, where label is one of
        {'kuendigungsfrist', 'verlaengerung'}.
    """
    results: list[tuple[str, str]] = []
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    for match in _RENEWAL_TERMINATION_RE.finditer(normalized):
        kuendigung = re.sub(r"\s+", " ", match.group("kuendigung")).strip()
        verlaengerung = re.sub(r"\s+", " ", match.group("verlaengerung")).strip()
        if kuendigung:
            results.append((kuendigung, "kuendigungsfrist"))
        if verlaengerung:
            results.append((verlaengerung, "verlaengerung"))
    return results
