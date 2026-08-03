import unittest

from core.pipeline import _to_legacy_segment_analysis


class MahnungDeadlineRoutingTests(unittest.TestCase):
    def test_analysis_payload_shape_routes_payment_deadline_into_fristen(self):
        payload = {
            "document_type": "Mahnung",
            "facts": [
                {
                    "text": "Bitte zahlen Sie den offenen Betrag bis spätestens 17.06.2026.",
                    "citations": [
                        {
                            "page_number": 16,
                            "quote_snippet": "Bitte zahlen Sie den offenen Betrag bis spätestens 17.06.2026.",
                        }
                    ],
                }
            ],
            "dates": [],
            "issues": [],
        }

        legacy = _to_legacy_segment_analysis(payload)
        fristen = list(legacy.get("fristen_termine", []) or [])

        self.assertIn(
            "Zahlungsfrist: 17.06.2026",
            fristen,
            f"analysis_payload-Facts müssen in fristen_termine geroutet werden, war: {fristen!r}",
        )

    def test_deadline_from_facts_is_routed_when_dates_are_empty(self):
        payload = {
            "document_type": "Mahnung",
            "facts": [
                {
                    "text": "Letzte Zahlungsaufforderung: Zahlung bis spätestens 17.06.2026.",
                    "citations": [
                        {
                            "page_number": 16,
                            "quote_snippet": "Letzte Zahlungsaufforderung: Zahlung bis spätestens 17.06.2026.",
                        }
                    ],
                }
            ],
            "dates": [],
            "issues": [],
        }

        legacy = _to_legacy_segment_analysis(payload)
        fristen = list(legacy.get("fristen_termine", []) or [])

        self.assertTrue(fristen, "Fristenliste darf nicht leer sein.")
        self.assertTrue(
            any("17.06.2026" in str(item) for item in fristen),
            f"17.06.2026 muss in fristen_termine enthalten sein, war: {fristen!r}",
        )
        self.assertIn(
            "Zahlungsfrist: 17.06.2026",
            fristen,
            f"Zahlungsfrist-Label muss in fristen_termine enthalten sein, war: {fristen!r}",
        )

    def test_fact_citation_context_can_drive_payment_deadline_label(self):
        payload = {
            "document_type": "Mahnung",
            "facts": [
                {
                    "text": "17.06.2026",
                    "citations": [
                        {
                            "page_number": 16,
                            "quote_snippet": "Zahlung bis spätestens 17.06.2026 auf das Kanzleikonto.",
                        }
                    ],
                }
            ],
            "dates": [],
            "issues": [],
        }

        legacy = _to_legacy_segment_analysis(payload)
        fristen = list(legacy.get("fristen_termine", []) or [])

        self.assertIn(
            "Zahlungsfrist: 17.06.2026",
            fristen,
            f"Zahlungsfrist muss aus Fact-Citation-Kontext abgeleitet werden, war: {fristen!r}",
        )

    def test_non_deadline_fact_date_is_not_promoted(self):
        payload = {
            "document_type": "Mietvertrag",
            "facts": [
                {
                    "text": "Vertragsbeginn am 01.05.2026.",
                    "citations": [
                        {"page_number": 2, "quote_snippet": "Vertragsbeginn am 01.05.2026."}
                    ],
                }
            ],
            "dates": [],
            "issues": [],
        }

        legacy = _to_legacy_segment_analysis(payload)
        self.assertEqual(list(legacy.get("fristen_termine", []) or []), [])


if __name__ == "__main__":
    unittest.main()
