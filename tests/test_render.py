"""Tests for Step 4 renderers (plan sec. 7) -- honest, deterministic JSON + HTML.

The JSON report must carry integer ``schema_version = 1``, render byte-identically
run to run, and NEVER show a fabricated 0 for a placeholder metric. The HTML report
must label placeholder/absent metrics "unavailable", show the real measured latency,
and state outcomes are UNJOINED -- all deterministically.
"""

from __future__ import annotations

import json
from pathlib import Path

from mesh_lens.analyze import analyze_report, build_skill_detail, build_skill_summaries
from mesh_lens.correlate import correlate, dispatch_ref
from mesh_lens.inventory import PRODUCER_SCHEMA_ID
from mesh_lens.render import (
    render_html,
    render_json,
    render_skill_browser_html,
    render_skill_browser_json,
    write_report,
    write_skill_browser,
)
from mesh_lens.store import Store

GOLDEN_RELPATH = "analyze_golden.jsonl"


def _report(golden: Path, tmp_path: Path):
    store = Store(tmp_path / "store")
    store.ingest_source(golden, source_relpath=GOLDEN_RELPATH)
    events = store.read_events()
    dispatches = [dispatch_ref(e) for e in events if e.producer_schema == PRODUCER_SCHEMA_ID]
    return analyze_report(events, correlate(dispatches, []))


# --------------------------------------------------------------------------- #
# JSON: versioned, honest, deterministic.
# --------------------------------------------------------------------------- #


def test_json_carries_integer_schema_version_1(analyze_golden_stream: Path, tmp_path: Path) -> None:
    report = _report(analyze_golden_stream, tmp_path)
    obj = json.loads(render_json(report))
    assert obj["schema_version"] == 1
    assert isinstance(obj["schema_version"], int)


def test_json_placeholder_metric_is_null_not_zero(
    analyze_golden_stream: Path, tmp_path: Path
) -> None:
    report = _report(analyze_golden_stream, tmp_path)
    obj = json.loads(render_json(report))
    cohort_a = next(
        c
        for c in obj["comparable_cohorts"]
        if c["key"]["skill"] == "plan-init" and c["key"]["model"] == "claude"
    )
    for field in ("tokens_in", "tokens_out", "cost_usd"):
        assert cohort_a[field]["status"] == "unavailable"
        assert cohort_a[field]["sum"] is None
        assert cohort_a[field]["mean"] is None
    # latency IS a real measured aggregate.
    assert cohort_a["latency_ms"]["status"] == "measured"
    assert cohort_a["latency_ms"]["sum"] == 300
    assert cohort_a["latency_ms"]["mean"] == 150.0


def test_json_every_measured_number_carries_source_refs(
    analyze_golden_stream: Path, tmp_path: Path
) -> None:
    report = _report(analyze_golden_stream, tmp_path)
    obj = json.loads(render_json(report))
    cohort_a = next(
        c
        for c in obj["comparable_cohorts"]
        if c["key"]["skill"] == "plan-init" and c["key"]["model"] == "claude"
    )
    assert cohort_a["latency_ms"]["measured_refs"] == [
        f"{GOLDEN_RELPATH}@2",
        f"{GOLDEN_RELPATH}@3",
    ]
    assert cohort_a["verdicts"]["refs"]["pass"] == [f"{GOLDEN_RELPATH}@2", f"{GOLDEN_RELPATH}@4"]


def test_json_is_byte_identical_run_to_run(analyze_golden_stream: Path, tmp_path: Path) -> None:
    report = _report(analyze_golden_stream, tmp_path)
    assert render_json(report) == render_json(report)


# --------------------------------------------------------------------------- #
# HTML: honest labels, real numbers, deterministic.
# --------------------------------------------------------------------------- #


def test_html_labels_placeholder_unavailable_and_shows_real_latency(
    analyze_golden_stream: Path, tmp_path: Path
) -> None:
    report = _report(analyze_golden_stream, tmp_path)
    out = render_html(report)
    assert out.startswith("<!doctype html>")
    assert "unavailable -- not measured" in out  # placeholder tokens/cost
    assert "sum=300" in out  # real measured latency aggregate
    assert "UNJOINED" in out  # outcomes stance
    assert "never a fabricated 0" in out
    # The unknown record's own measured token sum appears in its incomparable bucket.
    assert "sum=42" in out


def test_html_is_deterministic(analyze_golden_stream: Path, tmp_path: Path) -> None:
    report = _report(analyze_golden_stream, tmp_path)
    assert render_html(report) == render_html(report)


def test_html_escapes_values() -> None:
    # A skill name with HTML-special characters must be escaped, not injected.
    from mesh_lens.models import (
        CURRENT_SCHEMA_VERSION,
        Metric,
        MetricStatus,
        NormalizedInvocation,
        Provenance,
    )

    rec = NormalizedInvocation(
        schema_version=CURRENT_SCHEMA_VERSION,
        provenance=Provenance("x.jsonl", 1, "h"),
        producer_schema=PRODUCER_SCHEMA_ID,
        timestamp="t",
        skill="<script>alert(1)</script>",
        model="claude",
        latency_ms=1,
        verdict="stub",
        tokens_in=Metric(MetricStatus.PLACEHOLDER, 0),
        tokens_out=Metric(MetricStatus.PLACEHOLDER, 0),
        cost_usd=Metric(MetricStatus.PLACEHOLDER, 0),
        raw_field_names=(),
    )
    out = render_html(analyze_report([rec]))
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


# --------------------------------------------------------------------------- #
# Step 8 browser: navigation, sparse evidence, and escaped raw event fields.
# --------------------------------------------------------------------------- #


def _skill_details(golden: Path, tmp_path: Path):
    store = Store(tmp_path / "store")
    store.ingest_source(golden, source_relpath=GOLDEN_RELPATH)
    events = store.read_events()
    summaries = build_skill_summaries(events)
    return tuple(
        detail
        for summary in summaries
        if (detail := build_skill_detail(events, summary.skill)) is not None
    )


def test_skill_browser_renders_list_links_details_and_honest_states(
    analyze_golden_stream: Path, tmp_path: Path
) -> None:
    details = _skill_details(analyze_golden_stream, tmp_path)
    html = render_skill_browser_html(details)
    payload = json.loads(render_skill_browser_json(details))

    assert '<a href="#skill-1">plan-init</a>' in html
    assert 'id="skill-1"' in html
    assert "analyze_golden.jsonl@4" in html  # raw recent event source
    assert "placeholder: raw 0" in html
    assert "Outcome coverage unavailable" in html
    assert "not a zero-outcome claim" in html
    assert payload["schema_version"] == 1
    assert payload["skills"][0]["recent_events"][0]["source_ref"] == "analyze_golden.jsonl@4"


def test_skill_browser_empty_state_is_not_a_zero_activity_claim() -> None:
    html = render_skill_browser_html(())
    assert "No skill events are available" in html
    assert "not a zero-activity claim" in html


def test_skill_browser_escapes_untrusted_event_values() -> None:
    from mesh_lens.models import (
        CURRENT_SCHEMA_VERSION,
        Metric,
        MetricStatus,
        NormalizedInvocation,
        Provenance,
    )

    record = NormalizedInvocation(
        schema_version=CURRENT_SCHEMA_VERSION,
        provenance=Provenance("x.jsonl", 1, "h"),
        producer_schema=PRODUCER_SCHEMA_ID,
        timestamp="<script>timestamp</script>",
        skill="skill",
        model="<script>model</script>",
        latency_ms=0,
        verdict="<script>verdict</script>",
        tokens_in=Metric(MetricStatus.PLACEHOLDER, 0),
        tokens_out=Metric(MetricStatus.PLACEHOLDER, 0),
        cost_usd=Metric(MetricStatus.PLACEHOLDER, 0),
        raw_field_names=(),
    )
    detail = build_skill_detail([record], "skill")
    assert detail is not None
    html = render_skill_browser_html((detail,))
    assert "<script>model</script>" not in html
    assert "&lt;script&gt;model&lt;/script&gt;" in html


# --------------------------------------------------------------------------- #
# write_report file output.
# --------------------------------------------------------------------------- #


def test_write_report_writes_both_files(analyze_golden_stream: Path, tmp_path: Path) -> None:
    report = _report(analyze_golden_stream, tmp_path)
    out_dir = tmp_path / "out"
    written = write_report(report, out_dir, "both")
    assert [p.name for p in written] == ["report.json", "report.html"]
    assert (out_dir / "report.json").read_text(encoding="utf-8") == render_json(report)
    assert (out_dir / "report.html").read_text(encoding="utf-8") == render_html(report)


def test_write_report_respects_format(analyze_golden_stream: Path, tmp_path: Path) -> None:
    report = _report(analyze_golden_stream, tmp_path)
    written = write_report(report, tmp_path / "json-only", "json")
    assert [p.name for p in written] == ["report.json"]


def test_write_skill_browser_writes_both_files(
    analyze_golden_stream: Path, tmp_path: Path
) -> None:
    details = _skill_details(analyze_golden_stream, tmp_path)
    out_dir = tmp_path / "browser"
    written = write_skill_browser(details, out_dir)
    assert [path.name for path in written] == ["browser.json", "browser.html"]
    assert (out_dir / "browser.html").read_text(encoding="utf-8") == render_skill_browser_html(
        details
    )
