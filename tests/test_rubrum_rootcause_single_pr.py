from pathlib import Path
import re
import unittest

from core.export_render import collect_structured_parties_for_render
from core.extractor import extract_text_with_meta
from core.analyzer import apply_rubrum_parties_override
from core.evidence import _searchable_pages_without_repeating_edges
from governance.redaction import prepare_for_llm
from core.segmenter import segment_pages


def _page_no(page: dict) -> int:
    return int(page.get("page_number") or page.get("page") or 0)


def _segment_text_for_analysis(pages: list[dict]) -> str:
    parts: list[str] = []
    for idx, page in enumerate(pages, start=1):
        page_no = _page_no(page) or idx
        page_text = str(page.get("text", "") or "").strip()
        if page_text:
            parts.append(f"[Seite {page_no}]\n{page_text}")
        else:
            parts.append(f"[Seite {page_no}]")
    return "\n\n".join(parts).strip()


def _build_reverse_mapping(mapping: dict[str, str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for placeholder, original in (mapping or {}).items():
        if not placeholder or not original:
            continue
        pairs.append((str(original), str(placeholder)))
    return sorted(pairs, key=lambda item: len(item[0]), reverse=True)


def _apply_reverse_mapping(text: str, reverse_mapping: list[tuple[str, str]]) -> str:
    out = str(text or "")
    for original, placeholder in reverse_mapping:
        out = out.replace(original, placeholder)
    return out


def _load_mixed_fixture_pages(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"\[\[PAGE:(\d+)\]\]\n", raw))
    pages: list[dict] = []
    for idx, match in enumerate(matches):
        page_no = int(match.group(1))
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        pages.append(
            {
                "page": page_no,
                "page_number": page_no,
                "text": raw[start:end].strip(),
            }
        )
    return pages


class RubrumRootcauseSinglePRTests(unittest.TestCase):
    def _debug_klageschrift_redacted_head(self) -> dict:
        pdf_path = Path("pdfs/KlageschriftPDF9Test.pdf")
        self.assertTrue(pdf_path.exists(), "Fixture fehlt: pdfs/KlageschriftPDF9Test.pdf")
        extraction = extract_text_with_meta(pdf_path.read_bytes(), pdf_path.name)
        stripped_pages = _searchable_pages_without_repeating_edges(extraction.pages)
        stripped_text = "\n\n".join(str(page.get("text", "") or "") for page in stripped_pages)

        context = prepare_for_llm(
            stripped_text,
            {
                "dictionary_entities": [],
                "dictionary_fuzzy": False,
                "dictionary_threshold": 92,
                "pseudonymize_people": True,
            },
        )
        redacted_text = str(context.redacted_text or "")
        first_120 = redacted_text.splitlines()[:120]

        marker_keys = ("Klägerin:", "vertreten durch:", "Beklagter:")
        return {
            "first_120_lines": first_120,
            "pre_markers": {key: (key in stripped_text) for key in marker_keys},
            "post_markers": {key: (key in redacted_text) for key in marker_keys},
        }

    def test_debug_klageschrift_markers_after_strip_and_redaction(self):
        debug = self._debug_klageschrift_redacted_head()
        self.assertTrue(debug["first_120_lines"], "Debug-Head ist leer.")
        self.assertTrue(debug["pre_markers"]["Klägerin:"])
        self.assertTrue(debug["pre_markers"]["vertreten durch:"])
        self.assertTrue(debug["pre_markers"]["Beklagter:"])
        self.assertFalse(debug["post_markers"]["Klägerin:"])
        self.assertFalse(debug["post_markers"]["vertreten durch:"])
        self.assertTrue(debug["post_markers"]["Beklagter:"])

    def test_integration_like_klageschrift_pdf_rubrum_override(self):
        pdf_path = Path("pdfs/KlageschriftPDF9Test.pdf")
        self.assertTrue(pdf_path.exists(), "Fixture fehlt: pdfs/KlageschriftPDF9Test.pdf")
        extraction = extract_text_with_meta(pdf_path.read_bytes(), pdf_path.name)
        source_pages = [dict(page) for page in extraction.pages]
        source_pages_by_no = {_page_no(page): page for page in source_pages if _page_no(page)}

        full_source_text = "\n\n".join(str(page.get("text", "") or "") for page in source_pages)
        context = prepare_for_llm(
            full_source_text,
            {
                "dictionary_entities": [],
                "dictionary_fuzzy": False,
                "dictionary_threshold": 92,
                "pseudonymize_people": True,
            },
        )
        reverse_mapping = _build_reverse_mapping(dict(context.mapping or {}))
        redacted_pages = []
        for page in source_pages:
            redacted_page = dict(page)
            redacted_page["text"] = _apply_reverse_mapping(str(page.get("text", "") or ""), reverse_mapping)
            redacted_pages.append(redacted_page)

        segments = segment_pages(redacted_pages)
        self.assertTrue(segments, "Segmentierung lieferte keine Segmente.")
        segment = segments[0]
        seg_pages_redacted = list(segment.get("pages", []) or [])
        seg_pages_source = [
            dict(source_pages_by_no.get(_page_no(page), page))
            for page in seg_pages_redacted
        ]

        rubrum_source_text = _segment_text_for_analysis(seg_pages_source)
        simulated_bug_payload = {
            "document_type": "Klageschrift",
            "parties": [
                {"text": "Klägerin: Rechtsanwältin Dr. Anna Weiß", "citations": []},
                {"text": "Beklagter: [PERSON_99]", "citations": []},
            ],
        }

        fixed = apply_rubrum_parties_override(
            simulated_bug_payload,
            rubrum_source_text,
            reverse_mapping=reverse_mapping,
        )
        structured = collect_structured_parties_for_render(fixed, max_items=6)
        self.assertTrue(structured, "Keine strukturierten Parteien nach Rubrum-Override.")

        by_role = {str(item.get("role", "")): str(item.get("value", "")) for item in structured}
        claimant_items = [(role, value) for role, value in by_role.items() if "Kläger" in role]
        defendant_items = [(role, value) for role, value in by_role.items() if "Beklag" in role]
        self.assertTrue(claimant_items, "Kläger(in)-Rolle fehlt.")
        self.assertTrue(defendant_items, "Beklagte(r)-Rolle fehlt.")

        claimant_value = claimant_items[0][1]
        defendant_value = defendant_items[0][1]
        self.assertNotRegex(claimant_value, r"Rechtsanw[aä]lt|RAin|RA\b")
        self.assertRegex(claimant_value, r"\[[A-Z]+_[0-9A-Z]+\]")
        self.assertRegex(defendant_value, r"\[[A-Z]+_[0-9A-Z]+\]")

    def test_mixed_regression_guard_segment_count_and_non_klage_roles(self):
        fixture_path = Path(__file__).parent / "fixtures/segmenter/mixed_oversegmentation_24_pages.txt"
        self.assertTrue(fixture_path.exists(), "Fixture fehlt: tests/fixtures/segmenter/mixed_oversegmentation_24_pages.txt")
        pages = _load_mixed_fixture_pages(fixture_path)
        segments = segment_pages(pages)
        self.assertEqual(len(segments), 6, "Mixed-Segmentierung hat sich unerwartet verändert.")

        role_cases = [
            (
                "Mietvertrag",
                segments[0],
                [
                    {"text": "Vermieter: [PARTEI_A]", "citations": []},
                    {"text": "Mieter: [PARTEI_B]", "citations": []},
                ],
                ("Vermieter", "Mieter"),
            ),
            (
                "Arbeitsvertrag",
                segments[1],
                [
                    {"text": "Arbeitgeber: [PARTEI_C]", "citations": []},
                    {"text": "Arbeitnehmer: [PARTEI_D]", "citations": []},
                ],
                ("Arbeitgeber", "Arbeitnehmer"),
            ),
            (
                "Mahnung",
                segments[2],
                [
                    {"text": "Gläubiger: [PARTEI_E]", "citations": []},
                    {"text": "Schuldner: [PARTEI_F]", "citations": []},
                ],
                ("Gläubiger", "Schuldner"),
            ),
        ]

        for document_type, segment, seed_parties, expected_roles in role_cases:
            parsed = {"document_type": document_type, "parties": list(seed_parties)}
            text = _segment_text_for_analysis(list(segment.get("pages", []) or []))
            out = apply_rubrum_parties_override(parsed, text)
            self.assertEqual(out.get("parties"), seed_parties)
            rendered = collect_structured_parties_for_render(out, max_items=6)
            rendered_roles = {str(item.get("role", "")) for item in rendered}
            self.assertTrue(expected_roles[0] in rendered_roles)
            self.assertTrue(expected_roles[1] in rendered_roles)


if __name__ == "__main__":
    unittest.main()
