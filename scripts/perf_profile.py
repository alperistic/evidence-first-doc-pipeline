"""scripts/perf_profile.py – Timing profile for the full analysis path.

Measures each phase of the pipeline individually with perf_counter and
surfaces the bottleneck. No Streamlit dependency.

Usage:
    venv/bin/python scripts/perf_profile.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from time import perf_counter

# Add the repo root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

PDF_PATH = Path("samples/03_klageschrift_synthetic.pdf")

_SEP = "=" * 64


def _header(title: str) -> None:
    print(f"\n{_SEP}")
    print(f"  {title}")
    print(_SEP)


def _row(label: str, ms: float, note: str = "") -> None:
    suffix = f"  ({note})" if note else ""
    print(f"  {label:<44} {ms:>8.1f} ms{suffix}")


def _sub(label: str, ms: float) -> None:
    print(f"    ├─ {label:<40} {ms:>8.1f} ms")


# ---------------------------------------------------------------------------
# 0) Load PDF
# ---------------------------------------------------------------------------
_header("Phase 0 – Load PDF")
t0 = perf_counter()
file_bytes = PDF_PATH.read_bytes()
load_ms = (perf_counter() - t0) * 1000
_row("read_bytes", load_ms, f"{len(file_bytes):,} bytes")

# ---------------------------------------------------------------------------
# 1) Text extraction
# ---------------------------------------------------------------------------
_header("Phase 1 – Text extraction")
from core.extractor import extract_text_with_meta  # noqa: E402

t0 = perf_counter()
extraction = extract_text_with_meta(file_bytes, PDF_PATH.name)
extract_ms = (perf_counter() - t0) * 1000

pages = extraction.pages
raw_text = extraction.text
_row("extract_text_with_meta", extract_ms, f"{len(pages)} pages, OCR={extraction.ocr_used}")
print(f"  Total characters: {len(raw_text):,}")

# ---------------------------------------------------------------------------
# 2) Redaction / pseudonymization (on full text)
# ---------------------------------------------------------------------------
_header("Phase 2 – Redaction / pseudonymization (prepare_for_llm)")
from governance.redaction import prepare_for_llm  # noqa: E402

t0 = perf_counter()
context = prepare_for_llm(raw_text, {"pseudonymize_people": True, "dictionary_entities": []})
redact_ms = (perf_counter() - t0) * 1000

context_mapping = dict(context.mapping or {})
stats = context.stats or {}
_row("prepare_for_llm", redact_ms)
print(f"  Placeholders created: {len(context_mapping)}")
print(f"  redaction_applied:    {stats.get('redaction_applied')}")
pseudo_stats = stats.get("pseudonymization") or {}
print(f"  Pseudonymization:     {pseudo_stats.get('entity_count', 0)} entities")

# ---------------------------------------------------------------------------
# 3) Build reverse_mapping
# ---------------------------------------------------------------------------
_header("Phase 3 – Build reverse_mapping")
from core.pipeline import _build_reverse_mapping  # noqa: E402

t0 = perf_counter()
reverse_mapping = _build_reverse_mapping(context_mapping)
reverse_ms = (perf_counter() - t0) * 1000
_row("_build_reverse_mapping", reverse_ms, f"{len(reverse_mapping)} entries")

# ---------------------------------------------------------------------------
# 4) Page-level redaction (_prepare_pages_for_llm)
# ---------------------------------------------------------------------------
_header("Phase 4 – Page-level redaction (_prepare_pages_for_llm)")
from core.pipeline import _prepare_pages_for_llm  # noqa: E402

t0 = perf_counter()
redacted_pages = _prepare_pages_for_llm(pages, reverse_mapping, apply_redaction=True)
page_redact_ms = (perf_counter() - t0) * 1000
_row("_prepare_pages_for_llm", page_redact_ms, f"{len(redacted_pages)} pages")
# Per-page breakdown for the first 5
for i, (orig, red) in enumerate(zip(pages, redacted_pages), 1):
    orig_len = len(str(orig.get("text", "") or ""))
    red_len = len(str(red.get("text", "") or ""))
    if i <= 5:
        print(f"    Page {i}: {orig_len} → {red_len} characters")
if len(pages) > 5:
    print(f"    ... ({len(pages)-5} more pages)")

# ---------------------------------------------------------------------------
# 5) Segmentation
# ---------------------------------------------------------------------------
_header("Phase 5 – Segmentation (build_segments)")
from governance.policy import load_policy  # noqa: E402
from workflows.doc_summary import DocSummaryWorkflow  # noqa: E402

loaded_policy = load_policy(Path("policy.yaml"))
runtime_config = DocSummaryWorkflow.build_runtime_config(
    loaded_policy=loaded_policy,
    auto_classify=False,
    dictionary_entities=[],
    dictionary_fuzzy=False,
    dictionary_threshold=92,
)

t0 = perf_counter()
segments = DocSummaryWorkflow.build_segments(redacted_pages, runtime_config=runtime_config)
segment_ms = (perf_counter() - t0) * 1000
_row("build_segments", segment_ms, f"{len(segments)} segments")
for seg in segments:
    pages_in_seg = seg.get("pages", [])
    char_count = sum(len(str(p.get("text", "") or "")) for p in pages_in_seg)
    print(
        f"    Segment {seg.get('segment_id')}: "
        f"p.{seg.get('page_start')}-{seg.get('page_end')} | "
        f"Type: {seg.get('doc_type_guess', '?')} | "
        f"{len(pages_in_seg)} pages | {char_count:,} characters"
    )

# ---------------------------------------------------------------------------
# 6) Per-segment analysis (sequential, to measure overhead cleanly)
# ---------------------------------------------------------------------------
_header("Phase 6 – Segment analysis (sequential)")
from core.pipeline import _process_segment  # noqa: E402

source_pages_by_no: dict[int, dict] = {}
for i, p in enumerate(pages):
    pno = p.get("page_number") or (i + 1)
    source_pages_by_no[int(pno)] = p

seg_totals: list[float] = []
seg_timings: list[dict] = []

for seg in segments:
    seg_id = seg.get("segment_id", "?")
    t0 = perf_counter()
    result = _process_segment(
        seg,
        reverse_mapping=reverse_mapping,
        context_mapping=context_mapping,
        auto_classify=runtime_config.auto_classify,
        citation_verify_enabled=runtime_config.citation_verify_enabled,
        citation_verify_mode=runtime_config.citation_verify_mode,
        source_pages_by_no=source_pages_by_no,
    )
    seg_total_ms = (perf_counter() - t0) * 1000
    seg_totals.append(seg_total_ms)

    timings = result.get("timings_ms", {})
    seg_timings.append(timings)

    llm_structure = timings.get("llm_structure", 0)
    llm_verify = timings.get("llm_verify", 0)
    llm_aktenvermerk = timings.get("llm_aktenvermerk", 0)
    # Both calls run in parallel: critical path = max(structure, aktenvermerk)
    critical_path_ms = max(llm_structure, llm_aktenvermerk) + llm_verify
    parallel_savings_ms = min(llm_structure, llm_aktenvermerk)

    print(f"\n  [Segment {seg_id}] Total: {seg_total_ms:.1f} ms  (critical path: {critical_path_ms} ms)")
    _sub("llm_structure (analyze_document)", llm_structure)
    _sub("llm_verify (citation_verify)", llm_verify)
    _sub("llm_aktenvermerk (generate_aktenvermerk, parallel)", llm_aktenvermerk)
    _sub("Savings from parallelization", parallel_savings_ms)
    print(
        f"    ╰─ Tokens: {result.get('token_count', 0)} "
        f"(prompt={result.get('prompt_tokens')}, completion={result.get('completion_tokens')})"
    )

total_analysis_ms = sum(seg_totals)

# ---------------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------------
_header("SUMMARY")

phases = [
    ("Load PDF",                       load_ms),
    ("Text extraction",                extract_ms),
    ("Redaction/pseudonymization",     redact_ms),
    ("Build reverse mapping",          reverse_ms),
    ("Page-level redaction",           page_redact_ms),
    ("Segmentation",                   segment_ms),
    ("Analysis total (sequential)",    total_analysis_ms),
]
grand_total = sum(ms for _, ms in phases)

for label, ms in phases:
    pct = (ms / grand_total * 100) if grand_total > 0 else 0.0
    _row(label, ms, f"{pct:.1f}%")

print(f"\n  {'─'*56}")
_row("TOTAL (sequential)", grand_total)
print(f"\n  Note: at max_concurrent={runtime_config.max_concurrent_segments},")
print(f"  {len(segments)} segments run in parallel.")

n = len(segments)
llm_calls_per_seg = 2  # analyze_document + generate_aktenvermerk
if runtime_config.citation_verify_enabled:
    print(f"  Citation verify: active (no extra LLM call, text matching only)")
print(f"\n  LLM calls per segment: {llm_calls_per_seg}")
print(f"  => {n} segments × {llm_calls_per_seg} = {n * llm_calls_per_seg} LLM calls total (sequential)")
print(f"\n  Measured LLM times per segment:")
for i, (seg, timings, total_ms) in enumerate(zip(segments, seg_timings, seg_totals), 1):
    s = timings.get("llm_structure", 0)
    a = timings.get("llm_aktenvermerk", 0)
    print(f"    Seg {i}: llm_structure={s} ms, llm_aktenvermerk={a} ms  → {s+a} ms LLM, {total_ms-s-a:.0f} ms overhead")

print()
print("  BOTTLENECK ANALYSIS:")
parallel_savings_total = sum(
    min(timings.get("llm_structure", 0), timings.get("llm_aktenvermerk", 0))
    for timings in seg_timings
)
critical_path_total = sum(
    max(timings.get("llm_structure", 0), timings.get("llm_aktenvermerk", 0))
    + timings.get("llm_verify", 0)
    for timings in seg_timings
)
print(f"  Total savings from internal parallelization: {parallel_savings_total:,.0f} ms")
print(f"  Critical path (sequential):                  {critical_path_total:,.0f} ms")
print()
wall_clock_est_ms = max(
    max(t.get("llm_structure", 0), t.get("llm_aktenvermerk", 0)) + t.get("llm_verify", 0)
    for t in seg_timings
)
print(f"  Estimated wall-clock time at max_concurrent={runtime_config.max_concurrent_segments}:")
print(f"    (all {n} segments in parallel)  ~{wall_clock_est_ms/1000:.0f}s")
