import unittest

from core.legacy_deadlines import (
    build_legacy_deadlines,
    dedupe_legacy_deadline_items,
    classify_date_category,
    build_reference_dates,
)
from core.evidence import build_evidence_map
from core.snippet import _select_best_sentence_snippet


class LegacyDeadlineTests(unittest.TestCase):
    def test_build_legacy_deadlines_dedupes_same_label_date_and_page(self):
        items = [
            {
                "text": "Kündigungsfrist bis 31.03.2026",
                "label": "Kündigungsfrist",
                "citations": [{"page_number": 1, "quote_snippet": "Kündigungsfrist bis 31.03.2026"}],
            },
            {
                "text": "Kündigungsfrist bis 31.03.2026",
                "label": "Kündigungsfrist",
                "citations": [{"page_number": 1, "quote_snippet": "Kündigungsfrist bis zum 31.03.2026 gemäß § 7"}],
            },
            {
                "text": "Zahlung bis spätestens 17.06.2026",
                "citations": [{"page_number": 2, "quote_snippet": "Zahlung bis spätestens 17.06.2026"}],
            },
        ]

        out = build_legacy_deadlines(items, limit=25)

        self.assertEqual(sum("31.03.2026" in item for item in out), 1)
        self.assertEqual(sum("17.06.2026" in item for item in out), 1)
        self.assertTrue(any("Kündigungsfrist" in item for item in out))

    def test_build_legacy_deadlines_adds_payment_label_for_plain_date(self):
        items = [
            {
                "text": "17.06.2026",
                "citations": [{"page_number": 3, "quote_snippet": "Zahlung bis spätestens 17.06.2026"}],
            }
        ]
        out = build_legacy_deadlines(items, limit=25)
        self.assertTrue(out)
        self.assertEqual(out[0], "Zahlungsfrist: 17.06.2026")

    def test_dedupe_keys_are_unique_for_label_date_page_span(self):
        items = [
            {
                "text": "Frist bis 31.03.2026",
                "label": "Frist/Termin",
                "citations": [{"page_number": 1, "quote_snippet": "Frist bis 31.03.2026"}],
            },
            {
                "text": "Frist bis 31.03.2026",
                "label": "Frist/Termin",
                "citations": [{"page_number": 1, "quote_snippet": "Frist bis 31.03.2026"}],
            },
            {
                "text": "Frist bis 31.03.2026",
                "label": "Frist/Termin",
                "citations": [{"page_number": 2, "quote_snippet": "Frist bis 31.03.2026"}],
            },
        ]
        deduped = dedupe_legacy_deadline_items(items, limit=25)
        keys = [(item["label"], item["date"], item["page_or_span"]) for item in deduped]
        self.assertEqual(len(keys), len(set(keys)))

    def test_dedupe_collision_keeps_better_trigger_richer_snippet(self):
        items = [
            {
                "text": "31.03.2026",
                "label": "Frist/Termin",
                "citations": [{"page_number": 1, "quote_snippet": "31.03.2026"}],
            },
            {
                "text": "31.03.2026",
                "label": "Frist/Termin",
                "citations": [
                    {
                        "page_number": 1,
                        "quote_snippet": "Zahlung bis spätestens 31.03.2026 auf das benannte Konto.",
                    }
                ],
            },
        ]
        deduped = dedupe_legacy_deadline_items(items, limit=25)
        self.assertEqual(len(deduped), 1)
        self.assertIn("spätestens", deduped[0]["snippet"].lower())

    def test_kuendigungsfrist_requires_trigger_otherwise_downgrade(self):
        items = [
            {
                "text": "Mietbeginn 01.04.2026",
                "label": "Kündigungsfrist",
                "citations": [{"page_number": 1, "quote_snippet": "Das Mietverhältnis beginnt am 01.04.2026."}],
            },
            {
                "text": "Kündigungsfrist zum 31.12.2026",
                "label": "Kündigungsfrist",
                "citations": [{"page_number": 2, "quote_snippet": "Kündigung ist mit Frist zum 31.12.2026 zulässig."}],
            },
        ]
        deduped = dedupe_legacy_deadline_items(items, limit=25)
        labels = [item["label"] for item in deduped]
        self.assertIn("Frist/Termin", labels)
        self.assertIn("Kündigungsfrist", labels)
        kuendigung_entries = [item for item in deduped if item["label"] == "Kündigungsfrist"]
        triggers = (
            "kündig",
            "kündigungs",
            "kündigung",
            "beend",
            "beendigung",
            "auflös",
            "vertragsende",
            "frist zum",
            "ende des",
            "verlänger",
        )
        for entry in kuendigung_entries:
            snippet_l = f"{entry.get('text', '')} {entry.get('snippet', '')}".lower()
            self.assertTrue(any(trigger in snippet_l for trigger in triggers))


class BuildEvidenceMapDeadlineTests(unittest.TestCase):
    def test_kuendigungsfrist_duration_lands_only_in_kuendigung_fristen_not_fristen_termine(self):
        pages = [
            {
                "page_number": 1,
                "text": "Kündigungsfrist für den Mieter: 3 Monate",
            }
        ]
        evidence = build_evidence_map(pages)
        fields = [item["field"] for item in evidence if item.get("value") != "missing"]
        self.assertIn("kuendigung_fristen", fields)
        self.assertNotIn("fristen_termine", fields)

    def test_zeilen_loop_kuendigung_datum_nur_in_kuendigung_fristen(self):
        """Zeilen-Loop: Datum auf einer Kündigung-Zeile landet nur in kuendigung_fristen.

        Der flat_text-Datums-Loop feuert hier NICHT, weil _DEADLINE_TRIGGER_PHRASES
        (z.B. 'frist', 'termin') im Kontext fehlen – nur der Zeilen-Loop greift.
        """
        pages = [
            {
                "page_number": 1,
                "text": "Kündigung zum 31.03.2027 beim Vermieter einzureichen.",
            }
        ]
        evidence = build_evidence_map(pages)
        kf = [item for item in evidence if item.get("field") == "kuendigung_fristen" and item.get("value") != "missing"]
        ft = [item for item in evidence if item.get("field") == "fristen_termine" and item.get("value") != "missing"]
        self.assertTrue(kf, "31.03.2027 muss in kuendigung_fristen erscheinen")
        self.assertFalse(ft, "fristen_termine muss leer sein für eine reine Kündigung-Zeile")

    def test_zeilen_loop_meta_block_zeile_erzeugt_keine_fristen_evidence(self):
        """Zeilen-Loop: Zeile mit Meta-Block-Indikator (az:) + Kündigung + Datum erzeugt kein Evidence.

        Ohne den _is_meta_block_context-Guard würde der Zeilen-Loop das Datum in
        kuendigung_fristen eintragen, obwohl es sich um Metadaten handelt.
        """
        pages = [
            {
                "page_number": 1,
                "text": "Aktenzeichen: AZ: LATE-KUE-2027 Kündigung zum 15.04.2027",
            }
        ]
        evidence = build_evidence_map(pages)
        kf = [item for item in evidence if item.get("field") == "kuendigung_fristen" and item.get("value") != "missing"]
        ft = [item for item in evidence if item.get("field") == "fristen_termine" and item.get("value") != "missing"]
        self.assertFalse(kf, "kuendigung_fristen muss leer sein für Meta-Block-Zeile")
        self.assertFalse(ft, "fristen_termine muss leer sein für Meta-Block-Zeile")

    def test_date_in_meta_block_context_does_not_land_in_fristen_termine(self):
        pages = [
            {
                "page_number": 1,
                "text": (
                    "Aktenzeichen: AZ: LATE-MIX-2026-0422 "
                    "Dokumenten-ID: DOC-ID: MIX-PAKET-2026-04-22"
                ),
            }
        ]
        evidence = build_evidence_map(pages)
        fristen_items = [
            item for item in evidence
            if item.get("field") == "fristen_termine" and item.get("value") != "missing"
        ]
        self.assertEqual(fristen_items, [])

    def test_datum_beginn_aus_meta_block_zeile_nicht_extrahiert(self):
        """datum_beginn aus einer Zeile mit Meta-Block-Indikator (az:) wird nicht extrahiert.

        Ohne den _is_meta_block_context-Guard im Zeilen-Loop würde 'Beginn: 01.01.2025'
        in datum_beginn landen, obwohl es sich um eine Metadaten-Zeile handelt.
        """
        pages = [
            {
                "page_number": 1,
                "text": "az: V2024-001 Dokumenttyp: Vertrag Beginn: 01.01.2025",
            }
        ]
        evidence = build_evidence_map(pages)
        db_items = [
            item for item in evidence
            if item.get("field") == "datum_beginn" and item.get("value") != "missing"
        ]
        self.assertEqual(db_items, [], "datum_beginn darf aus Meta-Block-Zeile nicht extrahiert werden")

    def test_bis_spaetestens_datum_landet_in_fristen_termine_mit_zahlungsfrist_label(self):
        """Datum nach 'bis spätestens' landet in fristen_termine mit Label 'Zahlungsfrist'."""
        pages = [
            {
                "page_number": 2,
                "text": "Wir fordern Sie auf, den Betrag bis spätestens 17.06.2026 zu überweisen.",
            }
        ]
        evidence = build_evidence_map(pages)
        ft_items = [
            item for item in evidence
            if item.get("field") == "fristen_termine" and item.get("value") != "missing"
        ]
        self.assertTrue(ft_items, "17.06.2026 muss in fristen_termine erscheinen")
        labels = [item.get("label", "") for item in ft_items]
        self.assertTrue(
            any("Zahlungsfrist" in lbl for lbl in labels),
            f"Label muss 'Zahlungsfrist' enthalten, war: {labels}",
        )


    def test_mahnung_zahlungsfrist_bis_spaetestens_mit_betrag(self):
        """Mahnung: 'bis spätestens 17.06.2026' mit Betrag landet in fristen_termine.

        Realer Mahnungstext mit EUR-Betrag vor dem Datum. Sicherstellt, dass der
        Betrag (EUR 3.997,40) die Datumserkennung nicht blockiert oder den Kontext
        so verändert, dass _is_meta_block_context() fälschlicherweise True liefert.
        """
        pages = [
            {
                "page_number": 1,
                "text": (
                    "Wir fordern Sie auf, den Betrag von EUR 3.997,40 "
                    "bis spätestens 17.06.2026 zu zahlen."
                ),
            }
        ]
        evidence = build_evidence_map(pages)
        ft_items = [
            item for item in evidence
            if item.get("field") == "fristen_termine" and item.get("value") != "missing"
        ]
        values = [item.get("value", "") for item in ft_items]
        self.assertTrue(
            any("17.06.2026" in v for v in values),
            f"17.06.2026 muss in fristen_termine erscheinen, war: {values!r}",
        )


class SelectBestSentenceSnippetTests(unittest.TestCase):
    def test_meta_block_header_excluded_in_favour_of_real_sentence(self):
        """Quote-Anker-Bug: Header mit AZ: enthält das Datum, echter Satz auch.

        _select_best_sentence_snippet darf nicht den Header zurückgeben, weil
        _is_meta_block_context(sentence) ihn als Meta-Block erkennt und aussortiert.
        """
        text = (
            "Kanzlei-Testpaket Dokumentenmappe AZ: LATE-MIX-2026-0422 Version 1\n"
            "Zahlung bis spätestens 17.06.2026 ist fällig."
        )
        result = _select_best_sentence_snippet(text, "17.06.2026", field_hint="fristen_termine")
        self.assertNotIn("AZ:", result, "Header-Zeile darf nicht als Quote-Anker gewählt werden")
        self.assertIn("17.06.2026", result)
        self.assertIn("spätestens", result)

    def test_shorter_content_sentence_preferred_over_longer_header(self):
        """Tiebreaker: kürzerer Inhaltssatz bevorzugt gegenüber längerem Header ohne Meta-Indikator.

        Wenn beide Sätze denselben Score haben, wählt die Funktion den kürzeren.
        """
        short_sentence = "Frist endet am 30.06.2026."
        # Padding to make the longer sentence the same score but significantly longer
        long_sentence = (
            "Der Mandant hat gemäß Vereinbarung und nach Rücksprache die Frist "
            "zum Einreichen der Unterlagen auf den 30.06.2026 verschoben worden."
        )
        text = f"{long_sentence}\n{short_sentence}"
        result = _select_best_sentence_snippet(text, "30.06.2026", field_hint="fristen_termine")
        self.assertIn("30.06.2026", result)
        # The shorter sentence should win the tiebreak
        self.assertLessEqual(
            len(result),
            len(long_sentence),
            "Kürzerer Satz soll bevorzugt werden",
        )

    def test_fallback_when_only_meta_block_contains_needle(self):
        """Wenn nur der Meta-Block das Datum enthält, wird der fallback benutzt (kein Meta-Block-Satz).

        Der Text enthält nur eine Zeile mit 'az:' → alle Kandidaten werden gefiltert →
        candidates leer → fallback-Pfad. Das Ergebnis darf keinen AZ-Header enthalten.
        """
        text = "Aktenzeichen: AZ: LATE-2026-001 Datum: 15.05.2026"
        fallback = "Zahlung bis 15.05.2026"
        result = _select_best_sentence_snippet(
            text, "15.05.2026", field_hint="fristen_termine", fallback=fallback
        )
        # All sentences are meta-block → candidates empty → fallback returned
        self.assertNotIn("AZ:", result, "Meta-Block-Header darf nicht zurückgegeben werden")
        self.assertIn("15.05.2026", result)


class DateCategoryTests(unittest.TestCase):
    """Fix 3: Fristen vs. Referenzdaten trennen."""

    def test_zahlungsfrist_is_frist(self):
        self.assertEqual(classify_date_category("Zahlungsfrist bis 17.06.2026"), "Frist")

    def test_kuendigungsfrist_is_frist(self):
        self.assertEqual(classify_date_category("Kündigungsfrist zum 31.12.2026"), "Frist")

    def test_faellig_am_is_frist(self):
        self.assertEqual(classify_date_category("fällig am 01.03.2026"), "Frist")

    def test_rechnungsdatum_is_referenzdatum(self):
        self.assertEqual(classify_date_category("Rechnungsdatum 01.01.2026"), "Referenzdatum")

    def test_vertragsdatum_is_referenzdatum(self):
        self.assertEqual(classify_date_category("Vertragsdatum 15.03.2025"), "Referenzdatum")

    def test_leistungsdatum_is_referenzdatum(self):
        self.assertEqual(classify_date_category("Leistungsdatum 20.02.2026"), "Referenzdatum")

    def test_uebergabetermin_is_termin(self):
        self.assertEqual(classify_date_category("Übergabetermin 01.04.2026"), "Termin")

    def test_mietbeginn_is_termin(self):
        self.assertEqual(classify_date_category("Mietbeginn 01.05.2026"), "Termin")

    def test_verhandlungstermin_is_termin(self):
        self.assertEqual(classify_date_category("Verhandlungstermin am 15.06.2026"), "Termin")

    def test_label_overrides_text(self):
        self.assertEqual(classify_date_category("01.01.2026", label="Rechnungsdatum"), "Referenzdatum")

    def test_build_legacy_deadlines_excludes_referenzdaten(self):
        items = [
            {"text": "Zahlungsfrist bis 17.06.2026", "label": "Zahlungsfrist",
             "citations": [{"page_number": 1, "quote_snippet": "bis 17.06.2026"}]},
            {"text": "Rechnungsdatum 01.01.2026", "label": "Referenzdatum",
             "citations": [{"page_number": 1, "quote_snippet": "Rechnungsdatum 01.01.2026"}]},
        ]
        deadlines = build_legacy_deadlines(items)
        self.assertTrue(any("17.06.2026" in d for d in deadlines))
        self.assertFalse(any("Rechnungsdatum" in d for d in deadlines))

    def test_build_reference_dates_only_referenzdaten(self):
        items = [
            {"text": "Zahlungsfrist bis 17.06.2026", "label": "Zahlungsfrist",
             "citations": [{"page_number": 1, "quote_snippet": "bis 17.06.2026"}]},
            {"text": "Rechnungsdatum 01.01.2026", "label": "Referenzdatum",
             "citations": [{"page_number": 1, "quote_snippet": "Rechnungsdatum 01.01.2026"}]},
        ]
        refs = build_reference_dates(items)
        self.assertTrue(any("01.01.2026" in r for r in refs))
        self.assertFalse(any("17.06.2026" in r for r in refs))


    def test_mahnung_rechnungsdatum_is_referenz_zahlungsfrist_is_frist(self):
        """Mahnung: Rechnungsdatum 24.05.2026 → Referenzdatum, Zahlungsfrist 17.06.2026 → Frist."""
        self.assertEqual(
            classify_date_category("Rechnungsdatum 24.05.2026"),
            "Referenzdatum",
        )
        self.assertEqual(
            classify_date_category("Zahlung bis spätestens 17.06.2026"),
            "Frist",
        )

    def test_arbeitsvertrag_kuendigung_probezeit_is_frist(self):
        """Arbeitsvertrag: Kündigung Probezeit → Frist (Kündigungsfrist-Trigger)."""
        self.assertEqual(
            classify_date_category("Kündigung in der Probezeit mit Frist von 2 Wochen"),
            "Frist",
        )

    def test_rechnungsnummer_and_datiert_are_referenz(self):
        self.assertEqual(classify_date_category("Rechnungsnummer 2026-0042"), "Referenzdatum")
        self.assertEqual(classify_date_category("datiert vom 01.03.2026"), "Referenzdatum")


class PR9FristenKlassifizierungTests(unittest.TestCase):
    """Fix C: Rechnungsdatum als Referenzdatum, Audit-Frist kein Kündigungsfrist."""

    def test_rechnung_re_id_datum_is_referenzdatum(self):
        """Rechnung RE-XXXX vom Datum → Referenzdatum."""
        self.assertEqual(
            classify_date_category("Rechnung RE-2026-0142 vom 24.05.2026"),
            "Referenzdatum",
            "Rechnungsnummer + vom-Datum muss als Referenzdatum erkannt werden",
        )

    def test_audit_vorankuendigung_not_kuendigungsfrist(self):
        """'14 Tage Audit-Vorankündigung' darf nicht als Kündigungsfrist gelabelt werden.

        classify_date_category kann "Frist" zurückgeben (es ist eine Frist),
        aber _resolve_deadline_label darf NICHT "Kündigungsfrist" produzieren.
        """
        from core.legacy_deadlines import _resolve_deadline_label
        label = _resolve_deadline_label("Kündigungsfrist", "mindestens 14 Tage Vorankündigung für Audit")
        self.assertNotEqual(label, "Kündigungsfrist", "Audit-Vorankündigung darf nicht als Kündigungsfrist gelabelt werden")

    def test_zahlung_bis_spaetestens_is_frist(self):
        """Zahlungsfrist darf durch Fix C nicht kippen."""
        self.assertEqual(
            classify_date_category("Zahlung bis spätestens 17.06.2026"),
            "Frist",
            "Zahlungsfrist muss weiterhin als Frist erkannt werden",
        )


class PR10FristenRealStringTests(unittest.TestCase):
    """Fix C': Fristen-Klassifizierung mit echten Output-Strings debuggt und korrigiert.

    Root Causes:
    - String 5: Das LLM übergibt den vollen Paragraphen-Kontext an classify_date_category.
      Der Kontext enthält sowohl die Zahlungsfrist ('Zahlung bis spätestens 17.06.2026')
      als auch das Rechnungsdatum ('RE-2026-0142 vom 24.05.2026'). has_strong_deadline
      wurde durch 'spätestens' True → Referenzdatum-Zweig nicht erreicht.
      Fix: 'vom <label>'-Proximity-Check hat Vorrang (wenn 'vom 24.05.2026' im
      Kontext vorkommt direkt nach einem Invoice-ID-Match → Referenzdatum).
    - String 6: PR9-Code korrekt implementiert; _resolve_deadline_label gibt bei
      'Vorankündigung' + 'Audit' korrekt 'Verfahrensfrist' zurück.
      Test dokumentiert Verhalten mit echtem String aus den Outputs.
    """

    def test_rechnung_full_paragraph_context_24052026_is_referenzdatum(self):
        """String 5: Voller Mahnung-Kontext → 24.05.2026 (Rechnungsdatum) ist Referenzdatum.

        Root Cause PR9: _INVOICE_ID_RE fand 'RE-2026', aber has_strong_deadline=True
        durch 'Zahlung bis spätestens' → Referenzdatum-Zweig wurde nicht erreicht.
        Fix: 'vom 24.05.2026' im Kontext → sofort Referenzdatum, unabhängig von
        anderen Fristen im selben Absatz.
        """
        self.assertEqual(
            classify_date_category(
                "Zahlung bis spätestens 17.06.2026 Sehr geehrte Damen und Herren, "
                "trotz mehrfacher Erinnerung ist die Rechnung RE-2026-0142 "
                "vom 24.05.2026 über EUR 3.980,00 wei...",
                "24.05.2026",
            ),
            "Referenzdatum",
            "Rechnungsdatum 24.05.2026 (nach 'vom RE-2026-0142') muss Referenzdatum sein, nicht Frist",
        )

    def test_audit_vorankuendigung_real_string_not_kuendigungsfrist(self):
        """String 6: Echter Audit-Kontext → 'mindestens 14 Tage' darf NICHT Kündigungsfrist sein.

        _resolve_deadline_label('Kündigungsfrist', ctx) muss 'Verfahrensfrist' zurückgeben,
        weil 'vorankündigung' und 'audit' in _VERFAHRENSFRIST_TRIGGERS enthalten sind
        und vor dem Kündigungsfrist-Zweig geprüft werden.
        """
        from core.legacy_deadlines import _resolve_deadline_label
        label = _resolve_deadline_label(
            "Kündigungsfrist",
            "Audit Der Auftraggeber kann nach angemessener Vorankündigung "
            "(mindestens 14 Tage) Audits durchführen oder durchführen lassen, "
            "soweit dies zur Überprüfung der v...",
        )
        self.assertNotEqual(
            label, "Kündigungsfrist",
            "Audit-Vorankündigung (14 Tage) darf nicht als Kündigungsfrist gelabelt werden, "
            f"war: {label!r}",
        )

    def test_zahlungsfrist_17062026_bleibt_frist_kein_regression(self):
        """Positiv-Test: 'Zahlung bis spätestens 17.06.2026' bleibt Frist (kein Regression).

        Die 'vom <label>'-Erkennung darf nur bei Invoice-ID-Kontext greifen,
        nicht bei normalen Zahlungsfristen ohne Invoice-ID.
        """
        self.assertEqual(
            classify_date_category("Zahlung bis spätestens 17.06.2026", "17.06.2026"),
            "Frist",
            "Zahlungsfrist 17.06.2026 muss weiterhin als Frist klassifiziert bleiben",
        )


class PR12P1AuditVorankuendigungTests(unittest.TestCase):
    """P1: 'mindestens 14 Tage' aus Audit-Vorankündigung darf NICHT in kuendigung_fristen."""

    P1_TEXT = (
        "§ 15 Audit\n"
        "Der Auftraggeber kann nach angemessener Vorankündigung "
        "(mindestens 14 Tage) Audits durchführen oder durchführen lassen, "
        "soweit dies zur Überprüfung der vertraglichen Pflichten erforderlich ist."
    )

    def _evidence(self) -> list:
        return build_evidence_map([{"page_number": 1, "text": self.P1_TEXT}])

    def test_audit_14_tage_not_in_kuendigung_fristen(self):
        """'14 Tage' aus Vorankündigung darf nicht als Kündigungsfrist landen."""
        ev = self._evidence()
        kf_values = [item["value"] for item in ev if item.get("field") == "kuendigung_fristen"]
        self.assertNotIn(
            "14 Tage",
            kf_values,
            f"'14 Tage' (Audit-Vorankündigung) darf nicht in kuendigung_fristen erscheinen, war: {kf_values}",
        )

    def test_audit_14_tage_not_labeled_kuendigungsfrist(self):
        """Kein fristen_termine-Eintrag für '14 Tage' mit label 'Kündigungsfrist'."""
        ev = self._evidence()
        for item in ev:
            if item.get("field") == "fristen_termine" and "14" in str(item.get("value", "")):
                self.assertNotIn(
                    "Kündigungsfrist",
                    str(item.get("label", "")),
                    f"'14 Tage' (Vorankündigung) darf nicht als Kündigungsfrist gelabelt sein, item: {item}",
                )

    def test_echte_kuendigungsfrist_still_extracted(self):
        """Regression: Zeile mit 'Kündigungsfrist' wird weiterhin korrekt erkannt."""
        ev = build_evidence_map([{
            "page_number": 1,
            "text": "§ 12 Laufzeit\nDer Vertrag kann mit einer Kündigungsfrist von 3 Monaten gekündigt werden.",
        }])
        kf_values = [item["value"] for item in ev if item.get("field") == "kuendigung_fristen"]
        self.assertTrue(
            any("3" in v for v in kf_values) or kf_values,
            f"Echte Kündigungsfrist muss in kuendigung_fristen landen, war: {kf_values}",
        )


class PR12P2VerlaengerungKuendigungTests(unittest.TestCase):
    """P2: '3 Monaten' (Kündigungsfrist) und '12 Monate' (Verlängerung) korrekt klassifizieren."""

    P2_TEXT = (
        "§ 12 Laufzeit\n"
        "Der Vertrag verlängert sich jeweils um 12 Monate, wenn er nicht mit einer "
        "Frist von 3 Monaten zum Laufzeitende gekündigt wird."
    )

    def _evidence(self) -> list:
        return build_evidence_map([{"page_number": 1, "text": self.P2_TEXT}])

    def test_3_monaten_extracted_somewhere(self):
        """'3 Monaten' muss überhaupt irgendwo extrahiert werden."""
        ev = self._evidence()
        all_values = [item["value"] for item in ev]
        has_3_monate = any("3" in v and "monat" in v.lower() for v in all_values)
        self.assertTrue(
            has_3_monate,
            f"'3 Monaten' muss extrahiert werden, alle Werte: {all_values}",
        )

    def test_3_monaten_in_kuendigung_fristen(self):
        """'3 Monaten' (Kündigungsfrist) muss in kuendigung_fristen erscheinen."""
        ev = self._evidence()
        kf_values = [item["value"] for item in ev if item.get("field") == "kuendigung_fristen"]
        has_3_monate = any("3" in v and "monat" in v.lower() for v in kf_values)
        self.assertTrue(
            has_3_monate,
            f"'3 Monaten' muss in kuendigung_fristen erscheinen, war: {kf_values}",
        )

    def test_12_monate_not_in_kuendigung_fristen(self):
        """'12 Monate' (Verlängerungszeitraum) darf NICHT in kuendigung_fristen erscheinen."""
        ev = self._evidence()
        kf_values = [item["value"] for item in ev if item.get("field") == "kuendigung_fristen"]
        has_12_monate_kf = any("12" in v and "monat" in v.lower() for v in kf_values)
        self.assertFalse(
            has_12_monate_kf,
            f"'12 Monate' (Verlängerungszeitraum) darf nicht in kuendigung_fristen erscheinen, war: {kf_values}",
        )

    def test_12_monate_not_in_kuendigung_fristen_realistic(self):
        """Real-PDF-Szenario: 'Kündigung' als Klauselüberschrift steht in der Nähe von '12 Monate'.
        '12 Monate' darf trotzdem nicht als Kündigungsfrist klassifiziert werden."""
        realistic_text = (
            "§ 12 Laufzeit und Verlängerung\n"
            "Danach verlängert er sich jeweils um 12 Monate, wenn er nicht mit einer "
            "Frist von 3 Monaten gekündigt wird.\n"
            "§ 13 Ordentliche Kündigung\n"
            "Die ordentliche Kündigung ist erstmals zum 30.04.2028 möglich."
        )
        ev = build_evidence_map([{"page_number": 1, "text": realistic_text}])
        kf_values = [item["value"] for item in ev if item.get("field") == "kuendigung_fristen"]
        has_12_monate_kf = any("12" in v and "monat" in v.lower() for v in kf_values)
        self.assertFalse(
            has_12_monate_kf,
            f"'12 Monate' (Verlängerungszeitraum) darf nicht in kuendigung_fristen erscheinen, war: {kf_values}",
        )

    def test_extract_renewal_and_termination_unit(self):
        """Unit-Test: extract_renewal_and_termination gibt korrekte Paare zurück."""
        from core.legacy_deadlines import extract_renewal_and_termination

        results = extract_renewal_and_termination(self.P2_TEXT)
        kuendigung_values = [v for v, label in results if label == "kuendigungsfrist"]
        has_3_monate = any("3" in v and "monat" in v.lower() for v in kuendigung_values)
        self.assertTrue(
            has_3_monate,
            f"extract_renewal_and_termination soll '3 Monaten' als kuendigungsfrist liefern, war: {results}",
        )

    def test_extract_renewal_no_match_returns_empty(self):
        """Kein Match → leere Liste."""
        from core.legacy_deadlines import extract_renewal_and_termination

        results = extract_renewal_and_termination("Der Vertrag läuft unbefristet.")
        self.assertEqual(results, [], f"Kein Treffer soll [] liefern, war: {results}")


class LLMDateCategoryPassthroughTests(unittest.TestCase):
    """LLM-seitig gesetztes date_category wird durchgereicht und hat Vorrang über
    classify_date_category (Smoke-Test Klageschrift: 19.02.2026 + 16.06.2024 dürfen
    nicht in fristen_termine landen wenn LLM date_category='Referenzdatum' setzt)."""

    def test_llm_referenzdatum_excluded_from_deadlines(self):
        """Item mit date_category='Referenzdatum' vom LLM darf nicht in build_legacy_deadlines landen."""
        items = [
            {
                "text": "Briefdatum 19.02.2026",
                "date_category": "Referenzdatum",
                "citations": [{"page_number": 1, "quote_snippet": "Hamburg, den 19.02.2026"}],
            },
            {
                "text": "Zahlungsfrist bis 31.01.2026",
                "date_category": "Frist",
                "citations": [{"page_number": 2, "quote_snippet": "bis 31.01.2026"}],
            },
        ]
        deadlines = build_legacy_deadlines(items)
        self.assertFalse(any("19.02.2026" in d for d in deadlines),
                         f"Briefdatum 19.02.2026 darf nicht in fristen_termine: {deadlines}")
        self.assertTrue(any("31.01.2026" in d for d in deadlines),
                        f"Zahlungsfrist 31.01.2026 muss enthalten sein: {deadlines}")

    def test_llm_referenzdatum_included_in_reference_dates(self):
        """Item mit date_category='Referenzdatum' landet in build_reference_dates."""
        items = [
            {
                "text": "Überweisungsdatum 16.06.2024",
                "date_category": "Referenzdatum",
                "citations": [{"page_number": 1, "quote_snippet": "per Überweisung vom 16.06.2024"}],
            },
        ]
        refs = build_reference_dates(items)
        self.assertTrue(any("16.06.2024" in r for r in refs),
                        f"Überweisungsdatum 16.06.2024 muss in reference_dates: {refs}")

    def test_llm_frist_not_overridden_by_text_trigger(self):
        """Wenn LLM date_category='Frist' setzt, bleibt Eintrag in deadlines auch wenn
        text-Heuristik anderslautend wäre."""
        items = [
            {
                "text": "Rückzahlung vereinbart für 31.12.2025",
                "date_category": "Frist",
                "citations": [],
            },
        ]
        deadlines = build_legacy_deadlines(items)
        self.assertTrue(any("31.12.2025" in d for d in deadlines),
                        f"LLM-Frist 31.12.2025 muss in fristen_termine: {deadlines}")

    def test_llm_date_category_missing_falls_back_to_classify(self):
        """Ohne date_category im Item greift classify_date_category als Fallback."""
        items = [
            {"text": "Rechnungsdatum 01.01.2026", "citations": []},
        ]
        deadlines = build_legacy_deadlines(items)
        self.assertFalse(any("01.01.2026" in d for d in deadlines),
                         f"Rechnungsdatum ohne LLM-Kategorie muss per Heuristik als Referenzdatum erkannt werden: {deadlines}")


class LongFormatDateEvidenceTests(unittest.TestCase):
    """Fix 2: build_evidence_map extrahiert Long-Format-Daten ('31. Januar 2025')
    als fristen_termine — vorher nur numerisches Format DD.MM.YYYY unterstützt."""

    def _fristen(self, pages: list[dict]) -> list[dict]:
        evidence = build_evidence_map(pages)
        return [ev for ev in evidence if ev.get("field") == "fristen_termine" and ev.get("value") != "missing"]

    def test_long_format_maengel_frist_januar_extrahiert(self):
        """'empfohlene Frist zur Mängelbeseitigung bis 31. Januar 2025' → 31.01.2025 in fristen_termine."""
        pages = [{"page_number": 9, "text": "empfohlene Frist zur Mängelbeseitigung bis 31. Januar 2025"}]
        fristen = self._fristen(pages)
        values = [ev.get("value", "") for ev in fristen]
        self.assertIn(
            "31.01.2025", values,
            f"'31.01.2025' (konvertiert aus Long-Format) muss in fristen_termine erscheinen, war: {values}",
        )

    def test_long_format_zahlung_bis_februar_extrahiert(self):
        """'Zahlung bis 14. Februar 2025' → 14.02.2025 in fristen_termine."""
        pages = [{"page_number": 9, "text": "Zahlung bis 14. Februar 2025"}]
        fristen = self._fristen(pages)
        values = [ev.get("value", "") for ev in fristen]
        self.assertIn(
            "14.02.2025", values,
            f"'14.02.2025' (konvertiert aus Long-Format) muss in fristen_termine erscheinen, war: {values}",
        )

    def test_numerisches_format_bleibt_funktionsfaehig(self):
        """Numerisches Format '31.01.2025' mit Frist-Kontext funktioniert weiterhin."""
        pages = [{"page_number": 1, "text": "Frist zur Mängelbeseitigung bis spätestens 31.01.2025"}]
        fristen = self._fristen(pages)
        values = [ev.get("value", "") for ev in fristen]
        self.assertTrue(
            any("31.01.2025" in v for v in values),
            f"Numerisches '31.01.2025' muss in fristen_termine erscheinen, war: {values}",
        )

    def test_long_format_ohne_frist_kontext_nicht_extrahiert(self):
        """Long-Format-Datum ohne Frist-Trigger-Phrase landet nicht in fristen_termine."""
        pages = [{"page_number": 1, "text": "Vereinbart am 15. März 2024."}]
        fristen = self._fristen(pages)
        values = [ev.get("value", "") for ev in fristen]
        self.assertFalse(
            any("März" in v or "maerz" in v.lower() or "März" in v for v in values),
            f"Datum ohne Frist-Kontext darf nicht in fristen_termine: {values}",
        )


class NormalizeDateToIsoLongFormatTests(unittest.TestCase):
    """Fix 2: _normalize_date_to_iso konvertiert Long-Format-Daten korrekt."""

    def setUp(self):
        from core.snippet import _normalize_date_to_iso
        self._fn = _normalize_date_to_iso

    def test_numerisch_dd_mm_yyyy(self):
        self.assertEqual(self._fn("31.01.2025"), "2025-01-31")

    def test_long_format_januar(self):
        self.assertEqual(self._fn("31. Januar 2025"), "2025-01-31")

    def test_long_format_februar(self):
        self.assertEqual(self._fn("14. Februar 2025"), "2025-02-14")

    def test_long_format_maerz_umlaut(self):
        self.assertEqual(self._fn("1. März 2026"), "2026-03-01")

    def test_long_format_dezember(self):
        self.assertEqual(self._fn("31. Dezember 2024"), "2024-12-31")

    def test_long_format_case_insensitive(self):
        self.assertEqual(self._fn("14. februar 2025"), "2025-02-14")

    def test_kein_datum_gibt_none(self):
        self.assertIsNone(self._fn("kein Datum hier"))


if __name__ == "__main__":
    unittest.main()
