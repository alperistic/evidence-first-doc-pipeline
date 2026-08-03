"""
tests/test_fix2_mietbeginn_zusammenfassung.py

FIX 2: "Mietbeginn" darf im Zusammenfassung-Pfad (_merge_llm_regex_eckdaten)
nur für Miet-/Räumungsdokumente erscheinen. Für alle anderen Doktypen wird
"Mietbeginn" → "Beginn" umgeschrieben.
"""

import unittest

from core.export_render import _merge_llm_regex_eckdaten


class TestMietbeginnZusammenfassung(unittest.TestCase):

    def _llm(self, label: str, wert: str = "01.04.2019") -> dict:
        return {"label": label, "wert": wert, "quelle": "S. 1"}

    def test_mietbeginn_kept_for_mietvertrag(self):
        """Mietvertrag: LLM 'Mietbeginn' bleibt 'Mietbeginn'."""
        merged = _merge_llm_regex_eckdaten([self._llm("Mietbeginn")], [], "mietvertrag")
        labels = [i["label"] for i in merged]
        self.assertIn("Mietbeginn", labels)

    def test_mietbeginn_relabeled_for_scheidungsfolgenvereinbarung(self):
        """Scheidungsfolgenvereinbarung: LLM 'Mietbeginn' → 'Beginn'."""
        merged = _merge_llm_regex_eckdaten(
            [self._llm("Mietbeginn")], [], "Scheidungsfolgenvereinbarung"
        )
        labels = [i["label"] for i in merged]
        self.assertNotIn("Mietbeginn", labels, f"Mietbeginn must be relabeled. Got: {labels}")
        self.assertIn("Beginn", labels, f"Should become 'Beginn'. Got: {labels}")

    def test_mietbeginn_relabeled_for_arbeitsvertrag(self):
        """Arbeitsvertrag: LLM 'Mietbeginn' → 'Beginn'."""
        merged = _merge_llm_regex_eckdaten([self._llm("Mietbeginn")], [], "Arbeitsvertrag")
        labels = [i["label"] for i in merged]
        self.assertNotIn("Mietbeginn", labels)
        self.assertIn("Beginn", labels)

    def test_mietbeginn_relabeled_for_kaufvertrag(self):
        """Kaufvertrag: LLM 'Mietbeginn' → 'Beginn'."""
        merged = _merge_llm_regex_eckdaten([self._llm("Mietbeginn")], [], "kaufvertrag")
        labels = [i["label"] for i in merged]
        self.assertNotIn("Mietbeginn", labels)
        self.assertIn("Beginn", labels)

    def test_mietbeginn_kept_for_raeumungsklage(self):
        """Räumungsklage: LLM 'Mietbeginn' bleibt 'Mietbeginn'."""
        merged = _merge_llm_regex_eckdaten([self._llm("Mietbeginn")], [], "Räumungsklage")
        labels = [i["label"] for i in merged]
        self.assertIn("Mietbeginn", labels)

    def test_other_llm_labels_unaffected(self):
        """Kaution, Miete etc. werden nicht umbenannt."""
        merged = _merge_llm_regex_eckdaten(
            [self._llm("Kaution", "EUR 2.500,00"), self._llm("Miete", "EUR 900,00")],
            [],
            "kaufvertrag",
        )
        labels = [i["label"] for i in merged]
        self.assertIn("Kaution", labels)
        self.assertIn("Miete", labels)


if __name__ == "__main__":
    unittest.main()
