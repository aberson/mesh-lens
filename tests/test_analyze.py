"""Tests for Step 4 aggregation (plan sec. 7) -- the measurement-honesty contract.

The golden fixture (``analyze_golden.jsonl``, ingested through the production store
path) pins EXACT expected aggregates. The suite hammers the measurement-validity
rules the code-review will check:

  * placeholder token/cost fields NEVER become a fabricated 0 sum/mean -- they report
    ``status="unavailable"`` with ``sum``/``mean`` = ``None`` (aggregated ONLY over
    measured values);
  * latency (the one metric with real signal) yields a genuine measured aggregate;
  * every displayed number resolves to its source record refs;
  * the ``unknown`` cohort is reported separately and NEVER merged into skillmesh-v1;
  * missingness (a null-latency record) is counted, not hidden;
  * outcomes/retries are reported UNJOINED, never attached to a dispatch;
  * the same input renders byte-identical (determinism).
"""

from __future__ import annotations

from pathlib import Path

from mesh_lens.analyze import (
    Cohort,
    MetricAggregate,
    Report,
    analyze_report,
)
from mesh_lens.correlate import correlate, dispatch_ref
from mesh_lens.inventory import PRODUCER_SCHEMA_ID
from mesh_lens.models import (
    CURRENT_SCHEMA_VERSION,
    Metric,
    MetricStatus,
    NormalizedInvocation,
    Provenance,
)
from mesh_lens.render import render_json
from mesh_lens.store import Store

GOLDEN_RELPATH = "analyze_golden.jsonl"


def _report_from_golden(golden: Path, tmp_path: Path) -> Report:
    store = Store(tmp_path / "store")
    store.ingest_source(golden, source_relpath=GOLDEN_RELPATH)
    events = store.read_events()
    dispatches = [dispatch_ref(e) for e in events if e.producer_schema == PRODUCER_SCHEMA_ID]
    correlation = correlate(dispatches, [])  # no outcomes -> all unjoined, honestly
    return analyze_report(events, correlation)


def _cohort_by_skill_model(cohorts: tuple[Cohort, ...], skill: str, model: str) -> Cohort:
    for c in cohorts:
        if c.key.skill == skill and c.key.model == model:
            return c
    raise AssertionError(f"no cohort for {skill}/{model}")


# --------------------------------------------------------------------------- #
# Golden aggregates reproduce EXACT expected values (plan sec. 7 done-when).
# --------------------------------------------------------------------------- #


def test_golden_comparable_cohort_a_exact_aggregates(
    analyze_golden_stream: Path, tmp_path: Path
) -> None:
    report = _report_from_golden(analyze_golden_stream, tmp_path)
    # Two comparable cohorts, sorted: (plan-init, claude) then (repo-sync, gpt-5.5).
    assert [(c.key.skill, c.key.model) for c in report.comparable_cohorts] == [
        ("plan-init", "claude"),
        ("repo-sync", "gpt-5.5"),
    ]
    a = _cohort_by_skill_model(report.comparable_cohorts, "plan-init", "claude")

    # count = 3 (the unknown-schema record with the same skill/model is NOT here).
    assert a.count == 3
    assert a.record_refs == (
        f"{GOLDEN_RELPATH}@2",
        f"{GOLDEN_RELPATH}@3",
        f"{GOLDEN_RELPATH}@4",
    )

    # latency: measured over @2(100) and @3(200); @4 is null (unavailable).
    lat = a.latency
    assert lat.status == "measured"
    assert lat.measured_count == 2
    assert lat.unavailable_count == 1  # missingness counted, not hidden
    assert lat.sum == 300
    assert lat.mean == 150.0
    assert lat.minimum == 100
    assert lat.maximum == 200
    assert lat.measured_refs == (f"{GOLDEN_RELPATH}@2", f"{GOLDEN_RELPATH}@3")

    # verdicts: pass = @2,@4 ; fail = @3.
    assert a.verdicts.count("pass") == 2
    assert a.verdicts.count("fail") == 1
    assert dict(a.verdicts.entries)["pass"] == (f"{GOLDEN_RELPATH}@2", f"{GOLDEN_RELPATH}@4")


def test_golden_comparable_cohort_b_exact_aggregates(
    analyze_golden_stream: Path, tmp_path: Path
) -> None:
    report = _report_from_golden(analyze_golden_stream, tmp_path)
    b = _cohort_by_skill_model(report.comparable_cohorts, "repo-sync", "gpt-5.5")
    assert b.count == 1
    assert b.latency.sum == 50
    assert b.latency.mean == 50.0
    assert b.latency.measured_refs == (f"{GOLDEN_RELPATH}@5",)
    assert b.verdicts.count("stub") == 1


# --------------------------------------------------------------------------- #
# Placeholder token/cost NEVER becomes a fabricated 0 (the cardinal rule).
# --------------------------------------------------------------------------- #


def test_placeholder_tokens_and_cost_are_unavailable_not_zero(
    analyze_golden_stream: Path, tmp_path: Path
) -> None:
    report = _report_from_golden(analyze_golden_stream, tmp_path)
    a = _cohort_by_skill_model(report.comparable_cohorts, "plan-init", "claude")
    for agg in (a.tokens_in, a.tokens_out, a.cost_usd):
        assert agg.status == "unavailable"
        assert agg.measured_count == 0
        assert agg.placeholder_count == 3
        assert agg.sum is None  # never a fabricated 0
        assert agg.mean is None  # never a fabricated 0.0
        assert agg.minimum is None
        assert agg.maximum is None
        assert agg.measured_refs == ()
        assert "not measured" in agg.summary()


def test_report_json_never_emits_a_zero_for_a_placeholder_metric(
    analyze_golden_stream: Path, tmp_path: Path
) -> None:
    report = _report_from_golden(analyze_golden_stream, tmp_path)
    a = _cohort_by_skill_model(report.comparable_cohorts, "plan-init", "claude")
    payload = a.tokens_in.to_json()
    assert payload["status"] == "unavailable"
    assert payload["sum"] is None
    assert payload["mean"] is None


# --------------------------------------------------------------------------- #
# The unknown cohort is reported separately and NEVER merged into skillmesh-v1.
# --------------------------------------------------------------------------- #


def test_unknown_record_is_never_merged_into_skillmesh_v1(
    analyze_golden_stream: Path, tmp_path: Path
) -> None:
    report = _report_from_golden(analyze_golden_stream, tmp_path)
    # One incomparable cohort; it shares skill+model with cohort A but stays separate.
    assert len(report.incomparable_cohorts) == 1
    unknown = report.incomparable_cohorts[0]
    assert unknown.key.producer_schema == "unknown"
    assert unknown.key.is_comparable is False
    assert unknown.count == 1
    assert unknown.record_refs == (f"{GOLDEN_RELPATH}@1",)

    # The unknown record's ref appears in NO comparable cohort (no merge).
    for cohort in report.comparable_cohorts:
        assert f"{GOLDEN_RELPATH}@1" not in cohort.record_refs

    # Comparable event count excludes the unknown record.
    assert report.comparable_event_count == 4
    assert report.incomparable_event_count == 1
    assert report.total_events == 5


def test_unknown_cohort_reports_its_own_measured_metrics(
    analyze_golden_stream: Path, tmp_path: Path
) -> None:
    # A different producer that actually measures tokens is honest in ITS bucket --
    # proving the aggregation computes real sums when values ARE measured.
    report = _report_from_golden(analyze_golden_stream, tmp_path)
    unknown = report.incomparable_cohorts[0]
    assert unknown.tokens_in.status == "measured"
    assert unknown.tokens_in.sum == 42
    assert unknown.tokens_out.sum == 7
    assert unknown.cost_usd.sum == 0.5
    assert unknown.latency.sum == 99


# --------------------------------------------------------------------------- #
# Traceability: every displayed number resolves to source record refs.
# --------------------------------------------------------------------------- #


def test_every_aggregate_ref_traces_to_a_cohort_record(
    analyze_golden_stream: Path, tmp_path: Path
) -> None:
    report = _report_from_golden(analyze_golden_stream, tmp_path)
    all_cohorts = report.comparable_cohorts + report.incomparable_cohorts
    for cohort in all_cohorts:
        record_refs = set(cohort.record_refs)
        assert len(record_refs) == cohort.count
        # Every measured-metric ref is one of the cohort's own records.
        for agg in (cohort.latency, cohort.tokens_in, cohort.tokens_out, cohort.cost_usd):
            assert set(agg.measured_refs) <= record_refs
            assert agg.measured_count == len(agg.measured_refs)
        # Every verdict ref is one of the cohort's own records; counts reconcile.
        verdict_refs = {ref for _, refs in cohort.verdicts.entries for ref in refs}
        assert verdict_refs <= record_refs
        assert cohort.verdicts.total == cohort.count


# --------------------------------------------------------------------------- #
# Outcomes / retries: reported UNJOINED, never attached to a dispatch.
# --------------------------------------------------------------------------- #


def test_outcomes_are_reported_unjoined(analyze_golden_stream: Path, tmp_path: Path) -> None:
    report = _report_from_golden(analyze_golden_stream, tmp_path)
    assert report.outcomes.all_unjoined is True
    assert report.outcomes.joined_count == 0
    assert "UNJOINED" in report.outcomes.note
    payload = report.outcomes.to_json()
    assert payload["all_unjoined"] is True
    assert "unavailable" in payload["retries"]


def test_outcomes_default_to_unjoined_when_no_correlation() -> None:
    report = analyze_report([])
    assert report.outcomes.all_unjoined is True
    assert report.total_events == 0
    assert report.comparable_cohorts == ()


# --------------------------------------------------------------------------- #
# Honesty facts sourced from the inventory (single source of truth).
# --------------------------------------------------------------------------- #


def test_absent_dimensions_and_placeholder_fields_are_sourced(
    analyze_golden_stream: Path, tmp_path: Path
) -> None:
    report = _report_from_golden(analyze_golden_stream, tmp_path)
    assert set(report.absent_dimensions) == {"project", "task_type"}
    assert set(report.placeholder_fields) == {"tokens_in", "tokens_out", "cost_usd"}


# --------------------------------------------------------------------------- #
# Determinism: same input -> byte-identical rendered report (plan sec. 6).
# --------------------------------------------------------------------------- #


def test_report_is_byte_identical_across_independent_ingests(
    analyze_golden_stream: Path, tmp_path: Path
) -> None:
    report_a = _report_from_golden(analyze_golden_stream, tmp_path / "a")
    report_b = _report_from_golden(analyze_golden_stream, tmp_path / "b")
    assert render_json(report_a) == render_json(report_b)


# --------------------------------------------------------------------------- #
# Measured-path unit: sum/mean/min/max over multiple genuine measured values.
# --------------------------------------------------------------------------- #


def _measured_invocation(ref_line: int, latency: int, tok_in: int) -> NormalizedInvocation:
    prov = Provenance("m.jsonl", ref_line, "h")
    return NormalizedInvocation(
        schema_version=CURRENT_SCHEMA_VERSION,
        provenance=prov,
        producer_schema="future-measured",  # a hypothetical producer that measures
        timestamp="2026-07-24T00:00:00Z",
        skill="s",
        model="m",
        latency_ms=latency,
        verdict="pass",
        tokens_in=Metric(MetricStatus.MEASURED, tok_in),
        tokens_out=Metric(MetricStatus.MEASURED, 0),
        cost_usd=Metric(MetricStatus.MEASURED, 0.0),
        raw_field_names=(),
    )


def test_measured_metric_aggregate_computes_real_stats() -> None:
    records = [
        _measured_invocation(1, latency=10, tok_in=5),
        _measured_invocation(2, latency=30, tok_in=15),
        _measured_invocation(3, latency=20, tok_in=10),
    ]
    report = analyze_report(records)
    # 'future-measured' is not skillmesh-v1, so it lands in the incomparable bucket.
    cohort = report.incomparable_cohorts[0]
    lat: MetricAggregate = cohort.latency
    assert lat.measured_count == 3
    assert lat.sum == 60
    assert lat.mean == 20.0
    assert lat.minimum == 10
    assert lat.maximum == 30
    assert cohort.tokens_in.sum == 30
    assert cohort.tokens_in.mean == 10.0
