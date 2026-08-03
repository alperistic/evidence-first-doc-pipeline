"""
tests/test_fix1_weiterbeschaeftigung_llm_path.py

FIX 1 (stubborn): Weiterbeschäftigung / Beschäftigungsbeginn darf NICHT als
Frist/Termin im LLM-Pfad erscheinen (build_legacy_deadlines /
_render_legacy_deadline_text).

Root cause: year-filter war < 2000, 2019 wurde durchgereicht.
Fix: year < 2024 + _NON_FRIST_ITEM_KEYWORDS-Kurzschluss.
"""

import unittest

from core.legacy_deadlines import build_legacy_deadlines, _render_legacy_deadline_text


def _item(text: str, label: str = "Frist/Termin") -> dict:
    return {"text": text, "label": label, "citations": []}


class TestWeiterbeschaeftigungFilter(unittest.TestCase):
    """Beschäftigungsbeginn-Daten werden aus build_legacy_deadlines herausgefiltert."""

    def test_weiterbeschaeftigung_2019_filtered(self):
        """'Weiterbeschäftigung … 01.04.2019' → NOT in output."""
        items = [_item("Weiterbeschäftigung an einem anderen Arbeitsplatz ist uns 01.04.2019")]
        result = build_legacy_deadlines(items)
        self.assertEqual(result, [], f"Employment date must be filtered. Got: {result}")

    def test_beschaeftigt_seit_2019_filtered(self):
        """'seit dem 01. April 2019 beschäftigt' → NOT in output."""
        items = [_item("seit dem 01. April 2019 beschäftigt")]
        result = build_legacy_deadlines(items)
        self.assertEqual(result, [], f"Employment date must be filtered. Got: {result}")

    def test_year_2019_no_context_filtered(self):
        """Year 2019 without deadline context → filtered (< 2024 rule)."""
        items = [_item("Übergabe der Unterlagen am 15.06.2019")]
        result = build_legacy_deadlines(items)
        self.assertEqual(result, [], f"Past reference date must be filtered. Got: {result}")

    def test_zahlungsfrist_2019_kept(self):
        """'Zahlungsfrist 15.03.2019 fällig' → kept (has deadline context)."""
        items = [_item("Zahlungsfrist 15.03.2019 fällig", label="Zahlungsfrist")]
        result = build_legacy_deadlines(items)
        self.assertNotEqual(result, [], f"Zahlungsfrist with context must be kept. Got: {result}")

    def test_future_frist_kept(self):
        """'Frist bis 01.04.2025' → kept."""
        items = [_item("Frist bis 01.04.2025")]
        result = build_legacy_deadlines(items)
        self.assertNotEqual(result, [], f"Future Frist must be kept. Got: {result}")

    def test_render_weiterbeschaeftigung_returns_empty(self):
        """_render_legacy_deadline_text returns '' for Weiterbeschäftigung item."""
        item = _item("Weiterbeschäftigung an einem anderen Arbeitsplatz ist uns 01.04.2019")
        self.assertEqual(_render_legacy_deadline_text(item), "")

    def test_render_weiterbeschaeftigung_ascii_returns_empty(self):
        """ASCII variant 'Weiterbeschaeftigung' also filtered."""
        item = _item("Weiterbeschaeftigung an einem anderen Arbeitsplatz 01.04.2019")
        self.assertEqual(_render_legacy_deadline_text(item), "")


if __name__ == "__main__":
    unittest.main()
