import unittest

from core.extractor import normalize_extracted_text


class ExtractorNormalizationTests(unittest.TestCase):
    def test_removes_hyphenation_at_line_end(self):
        raw = "Bewe-\nisführung im Dokument."
        self.assertEqual(normalize_extracted_text(raw), "Beweisführung im Dokument.")

    def test_normalizes_placeholder_linebreak(self):
        raw = "Vertreter: [PERSO\nN_C]"
        self.assertEqual(normalize_extracted_text(raw), "Vertreter: [PERSON_C]")

    def test_replaces_single_newlines_inside_paragraphs(self):
        raw = "Klägerin: [PARTEI_A]\nBeklagter: [PARTEI_B]\n\nAbsatz zwei\nmit Umbruch."
        self.assertEqual(
            normalize_extracted_text(raw),
            "Klägerin: [PARTEI_A] Beklagter: [PARTEI_B]\n\nAbsatz zwei mit Umbruch.",
        )

    def test_collapses_multiple_spaces(self):
        raw = "Wert:   12.600,00   EUR\n\n  Frist:   23.02.2026  "
        self.assertEqual(
            normalize_extracted_text(raw),
            "Wert: 12.600,00 EUR\n\nFrist: 23.02.2026",
        )


if __name__ == "__main__":
    unittest.main()

