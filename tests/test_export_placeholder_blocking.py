"""
test_export_placeholder_blocking.py

Regression-Tests für den internen Export-Placeholder-Check (PR6b).
Stellt sicher, dass _find_internal_export_placeholder_hits unrestaured
Platzhalter in beliebig verschachtelten Export-Payloads erkennt.

Hinweis: Der externe Export (Mandantenfassung) wird NICHT blockiert –
Platzhalter dort sind gewollte Redaktionen für Externe.
"""

import unittest

from core.pipeline import _find_internal_export_placeholder_hits


class ExportPlaceholderBlockingTests(unittest.TestCase):

    def test_clean_payload_returns_empty(self):
        """Keine Platzhalter → leere Liste."""
        payload = {
            "summary": "Max Mustermann hat den Vertrag unterzeichnet.",
            "aktennotiz": "Frist läuft bis 31.12.2025.",
        }
        self.assertEqual(_find_internal_export_placeholder_hits(payload), [])

    def test_person_placeholder_detected(self):
        """[PERSON_1] im Text muss erkannt werden."""
        payload = {"summary": "Der Kläger [PERSON_1] hat Klage erhoben."}
        hits = _find_internal_export_placeholder_hits(payload)
        self.assertIn("[PERSON_1]", hits)

    def test_multiple_placeholder_types_detected(self):
        """PERSON, ADDR, EMAIL, IBAN, PARTEI und DICT werden alle erkannt."""
        payload = {
            "summary": (
                "Partei [PERSON_2] wohnt bei [ADDR_1]. "
                "Kontakt: [EMAIL_1]. IBAN: [IBAN_1]. "
                "Mandant [PARTEI_3] mit Aktenzeichen [DICT_3]."
            )
        }
        hits = _find_internal_export_placeholder_hits(payload)
        self.assertIn("[PERSON_2]", hits)
        self.assertIn("[ADDR_1]", hits)
        self.assertIn("[EMAIL_1]", hits)
        self.assertIn("[IBAN_1]", hits)
        self.assertIn("[PARTEI_3]", hits)
        self.assertIn("[DICT_3]", hits)

    def test_nested_list_payload_detected(self):
        """Platzhalter in verschachtelten Listen werden gefunden."""
        payload = {
            "sections": [
                {"text": "Kein Platzhalter hier."},
                {"text": "Aber hier: [PERSON_5] ist betroffen."},
            ]
        }
        hits = _find_internal_export_placeholder_hits(payload)
        self.assertIn("[PERSON_5]", hits)

    def test_deeply_nested_dict_detected(self):
        """Platzhalter tief im Dict werden gefunden."""
        payload = {
            "meta": {
                "parties": {
                    "plaintiff": "[PERSON_3]",
                }
            }
        }
        hits = _find_internal_export_placeholder_hits(payload)
        self.assertIn("[PERSON_3]", hits)

    def test_deduplication_of_same_placeholder(self):
        """Derselbe Platzhalter mehrfach → nur einmal in der Liste."""
        payload = {
            "summary": "[PERSON_1] hat gehandelt.",
            "aktennotiz": "Rückfrage an [PERSON_1] erforderlich.",
        }
        hits = _find_internal_export_placeholder_hits(payload)
        self.assertEqual(hits.count("[PERSON_1]"), 1)

    def test_incomplete_mapping_blocks_internal_export(self):
        """
        Simuliert unvollständiges Mapping im internen Export: Kanzleiakte
        enthält noch Platzhalter weil das Mapping einen Eintrag nicht abdeckt.
        Der Check muss diese erkennen, damit der interne Export blockiert wird.
        """
        internal_export_content = {
            "summary": "Zwischen [PERSON_2] und der Beklagten wurde ein Vertrag geschlossen.",
            "aktennotiz": "",
        }
        hits = _find_internal_export_placeholder_hits(internal_export_content)
        self.assertTrue(len(hits) > 0, "Unvollständiges Mapping muss erkannt werden")
        self.assertIn("[PERSON_2]", hits)

    def test_restored_payload_passes(self):
        """Nach vollständiger Restaurierung: keine Platzhalter im Ergebnis."""
        internal_export_content = {
            "summary": "Zwischen Anna Müller und der Beklagten wurde ein Vertrag geschlossen.",
            "aktennotiz": "",
        }
        hits = _find_internal_export_placeholder_hits(internal_export_content)
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
