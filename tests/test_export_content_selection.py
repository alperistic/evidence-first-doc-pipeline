import unittest

from core.export_content import build_export_content


class ExportContentSelectionTests(unittest.TestCase):
    def test_summary_selection_excludes_aktennotiz(self):
        payload = {
            "summary": "Zusammenfassungstext",
            "aktennotiz": "Interne Aktennotiz",
            "evidence": [{"field": "betraege", "value": "1.000 EUR"}],
        }

        selected = build_export_content(payload, "Zusammenfassung")
        self.assertEqual(selected["summary"], "Zusammenfassungstext")
        self.assertEqual(selected["aktennotiz"], "")
        self.assertEqual(selected["evidence"], payload["evidence"])

    def test_external_mode_drops_aktennotiz_even_for_beides(self):
        payload = {
            "summary": "ZUSAMMENFASSUNG (NEUTRAL)\n- Punkt A",
            "aktennotiz": "AKTENNOTIZ (INTERN)\n- Interner Punkt",
            "evidence": [{"field": "parteien", "value": "A gegen B"}],
        }

        selected = build_export_content(payload, "Beides", mode="external")
        self.assertEqual(selected["summary"], payload["summary"])
        self.assertNotIn("aktennotiz", selected)

        exported_text = "\n\n".join(
            part
            for part in [
                str(selected.get("summary", "") or ""),
                str(selected.get("aktennotiz", "") or ""),
            ]
            if part.strip()
        )
        self.assertNotIn("Aktennotiz", exported_text)
        self.assertNotIn("AKTENNOTIZ", exported_text)


if __name__ == "__main__":
    unittest.main()
