"""
test_validator_false_positives.py

Stellt sicher dass validate_mandantenexport() keine False Positives
bei korrekt maskierten Platzhaltern produziert und echte Leaks
weiterhin zuverlässig erkennt.
"""

import pytest

from governance.redaction import (
    RedactionLeakError,
    _strip_placeholders,
    validate_mandantenexport,
)

# Texte mit Platzhaltern die KEIN Leak sind
PLACEHOLDER_FIXTURES = [
    "Partei [PERSON_1] wohnt in [ADDR_1].",
    "IBAN: [IBAN_1], BIC: [BIC_1]",
    "Kontakt: [EMAIL_1], Tel: [TELEFON_1]",
    "Kläger [PARTEI_1] gegen Beklagte [PARTEI_2]",
    "[ADDR_1], [ADDR_2], [ADDR_3], [ADDR_4], [ADDR_5], [ADDR_6], [ADDR_7]",
    "[BIC_1], [BIC_2], [EMAIL_1]",
    "168 Personen: [PERSON_1] bis [PERSON_168]",
]

# Texte mit echten Leaks die weiterhin erkannt werden müssen
REAL_LEAK_FIXTURES = [
    "Wohnhaft Hauptstraße 23, 80331 München",
    "IBAN: DE89 3704 0044 0532 0130 00",
    "Email: max@beispiel.de",
    "Tel: 0171 123 45 67",
    "PLZ: 10115 Berlin",
]


@pytest.mark.parametrize("text", PLACEHOLDER_FIXTURES)
def test_platzhalter_kein_false_positive(text):
    """validate_mandantenexport() wirft KEINEN Fehler bei reinen Platzhalter-Texten."""
    validate_mandantenexport(text)


@pytest.mark.parametrize("text", REAL_LEAK_FIXTURES)
def test_echter_leak_wird_erkannt(text):
    """validate_mandantenexport() wirft RedactionLeakError bei echten Leaks."""
    with pytest.raises(RedactionLeakError):
        validate_mandantenexport(text)


def test_mixed_platzhalter_und_leak():
    """Text mit Platzhaltern + echtem Leak → RedactionLeakError (Leak wird erkannt)."""
    text = (
        "Kläger [PERSON_1] wohnt in [ADDR_1]. "
        "Bankverbindung: [IBAN_1]. "
        "Erreichbar unter 0171 123 45 67."  # echter Leak
    )
    with pytest.raises(RedactionLeakError):
        validate_mandantenexport(text)


def test_strip_placeholders_entfernt_alle_typen():
    """_strip_placeholders() entfernt alle bekannten Platzhalter-Formate."""
    text = (
        "[ADDR_1] [PERSON_2] [BIC_3] [EMAIL_4] [IBAN_5] "
        "[PARTEI_6] [MASK_7] [ORG_8] [TELEFON_9] [PHONE_10] "
        "[DATUM_11] [AKTENZEICHEN_12] [BETRAG_13] [ADRESSE_14] "
        "[DICT_15] [TAXID_16] [FIRMA_17] [HRB_18]"
    )
    result = _strip_placeholders(text)
    assert "[" not in result
    assert "]" not in result


def test_strip_placeholders_behaelt_normalen_text():
    """_strip_placeholders() lässt normalen Text unverändert."""
    text = "Das Gericht hat entschieden."
    assert _strip_placeholders(text) == text


def test_mixed_pdf_aequivalent_kein_block():
    """Text der dem Mixed-PDF-Output entspricht (viele [PERSON_x], [ADDR_x],
    [BIC_x], [EMAIL_x], [IBAN_x]) → kein RedactionLeakError."""
    text = (
        "Zwischen [PERSON_1] (Kläger) und [PERSON_2] (Beklagte) "
        "wird folgender Vergleich geschlossen:\n"
        "§ 1 [PERSON_1] zahlt an [PERSON_2] einen Betrag von [BETRAG_1].\n"
        "§ 2 Wohnsitz des Klägers: [ADDR_1], [ADDR_2].\n"
        "§ 3 Bankverbindung: IBAN [IBAN_1], BIC [BIC_1].\n"
        "§ 4 Kontakt: [EMAIL_1], [TELEFON_1].\n"
        "§ 5 Weitere Parteien: [PARTEI_1], [PARTEI_2], [PARTEI_3].\n"
        "§ 6 Adressen: [ADDR_3], [ADDR_4], [ADDR_5], [ADDR_6], [ADDR_7].\n"
        "§ 7 Konten: [BIC_1], [BIC_2], [IBAN_2].\n"
        "§ 8 Emails: [EMAIL_1], [EMAIL_2].\n"
        "§ 9 Aktenzeichen: [AKTENZEICHEN_1].\n"
    )
    validate_mandantenexport(text)


def test_klageschrift_partei_platzhalter_kein_false_positive():
    """Redaktierter Klageschrift-Export mit [PARTEI_1]/[PARTEI_2]-Labels darf
    validate_mandantenexport() NICHT blockieren.

    Regression: PARTEI_x-Platzhalter enthalten Ziffern (z. B. „_1"), die
    in bestimmten Kontexten Telefon- oder PLZ-Mustern ähneln können.
    _strip_placeholders() muss sie vor dem PII-Check entfernen.
    """
    klageschrift = (
        "AN DAS\n"
        "AMTSGERICHT [AKTENZEICHEN_1]\n"
        "\n"
        "KLAGESCHRIFT\n"
        "\n"
        "In der Sache\n"
        "\n"
        "[PARTEI_1]\n"
        "– Klägerin –\n"
        "\n"
        "gegen\n"
        "\n"
        "[PARTEI_2]\n"
        "– Beklagte –\n"
        "\n"
        "wegen: Schadensersatz aus Vertrag\n"
        "Streitwert: 12.500,00 EUR\n"
        "\n"
        "I. ANTRAG\n"
        "Die Klägerin [PARTEI_1] beantragt,\n"
        "die Beklagte [PARTEI_2] zu verurteilen, an die Klägerin\n"
        "12.500,00 EUR nebst Zinsen in Höhe von 5 Prozentpunkten\n"
        "über dem Basiszinssatz seit dem 01.03.2024 zu zahlen.\n"
        "\n"
        "II. SACHVERHALT\n"
        "Die Parteien schlossen am 15.01.2024 einen Kaufvertrag.\n"
        "Die Klägerin leistete die vereinbarte Zahlung.\n"
        "Die Beklagte [PARTEI_2] erbrachte die Gegenleistung nicht.\n"
        "\n"
        "III. RECHTLICHE WÜRDIGUNG\n"
        "Der Anspruch ergibt sich aus §§ 433, 323 BGB.\n"
        "\n"
        "Beweis: Kaufvertrag als Anlage K1.\n"
    )
    # Kein RedactionLeakError erlaubt – alle PII sind als Platzhalter maskiert
    validate_mandantenexport(klageschrift)
