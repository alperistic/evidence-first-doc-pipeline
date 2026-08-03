"""
test_redaction_external_postproc.py

Post-Processing-Regeln in apply_strict_external_mask():
1. Klarname direkt nach Platzhalter wird entfernt
2. Hausnummer direkt nach Platzhalter wird entfernt
3. Platzhalter ohne Nachfolger bleibt unverändert
"""

import unittest

from governance.redaction import apply_strict_external_mask


class NameAfterPlaceholderTests(unittest.TestCase):
    """[PERSON/PARTEI/ORG_x]. Klarname → nur Platzhalter"""

    def test_name_nach_person_platzhalter_mit_punkt(self):
        result = apply_strict_external_mask("[PERSON_3]. Anna Weiß")
        self.assertEqual(result, "[PERSON_3]")

    def test_name_nach_person_platzhalter_mit_leerzeichen(self):
        result = apply_strict_external_mask("[PERSON_1] Stefan Lorenz")
        self.assertEqual(result, "[PERSON_1]")

    def test_name_nach_partei_platzhalter(self):
        result = apply_strict_external_mask("[PARTEI_2] Maria Huber")
        self.assertEqual(result, "[PARTEI_2]")


class HausnummerAfterPlaceholderTests(unittest.TestCase):
    """[PERSON/PARTEI/ORG/ADDR_x] Hausnummer → nur Platzhalter"""

    def test_hausnummer_nach_person_platzhalter(self):
        result = apply_strict_external_mask("[PERSON_1] 77")
        self.assertEqual(result, "[PERSON_1]")

    def test_hausnummer_mit_buchstabe_nach_addr_platzhalter(self):
        result = apply_strict_external_mask("[ADDR_2] 4a")
        self.assertEqual(result, "[ADDR_2]")

    def test_hausnummer_nach_partei_platzhalter(self):
        result = apply_strict_external_mask("[PARTEI_1] 12b")
        self.assertEqual(result, "[PARTEI_1]")


class PlatzhalterOhneNachfolgerTests(unittest.TestCase):
    """Platzhalter ohne anschließenden Klartext bleibt unverändert"""

    def test_person_platzhalter_allein(self):
        result = apply_strict_external_mask("[PERSON_3]")
        self.assertEqual(result, "[PERSON_3]")

    def test_addr_platzhalter_allein(self):
        result = apply_strict_external_mask("[ADDR_1]")
        self.assertEqual(result, "[ADDR_1]")

    def test_partei_platzhalter_allein(self):
        result = apply_strict_external_mask("[PARTEI_2]")
        self.assertEqual(result, "[PARTEI_2]")

    def test_platzhalter_gefolgt_von_satzzeichen(self):
        result = apply_strict_external_mask("[PERSON_1], Kläger")
        self.assertEqual(result, "[PERSON_1], Kläger")


if __name__ == "__main__":
    unittest.main()
