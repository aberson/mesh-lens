"""Tests for Step 5 guarded pairwise comparison (plan sec. 6, sec. 7 Step 5, sec. 8).

The cardinal discipline is REFUSAL: the comparison must never emit a directional verdict
the evidence cannot support. This suite hammers every guard the deep-review will check:

  * SAMPLE SIZE -- an undersized cohort (below the documented ``MIN_DIRECTIONAL_N``)
    refuses a directional verdict; the real 2-record stream refuses honestly.
  * INCOMPARABLE -- cross-schema / ``unknown`` cohorts are NEVER compared; a two-dimension
    (confounded) pick refuses; a zero-dimension (no-contrast) pick refuses.
  * PLACEHOLDER -- token/cost are unavailable for skillmesh-v1, so they carry NO verdict
    (never read as 0 or "equal"), even when both cohorts are sufficiently sized.
  * VALID PATH -- a comparable, sufficiently-sized, single-dimension contrast computes a
    REPRODUCIBLE delta, always presented WITH a correlation-not-causation caveat.
  * DISCLOSURE -- sample sizes, missingness, and comparable-vs-refused metrics are stated.
  * DETERMINISM -- the same input renders byte-identical JSON (a golden pins it).
"""

from __future__ import annotations

from pathlib import Path

from mesh_lens.analyze import Report, analyze_report
from mesh_lens.compare import (
    MIN_DIRECTIONAL_N,
    CohortSelectionError,
    CohortSelector,
    Comparison,
    compare_cohorts,
    parse_selector,
)
from mesh_lens.correlate import correlate, dispatch_ref
from mesh_lens.inventory import PRODUCER_SCHEMA_ID
from mesh_lens.render import render_comparison_json
from mesh_lens.store import Store

GOLDEN_JSON = Path(__file__).parent / "fixtures" / "compare_model_contrast.golden.json"


def _report(stream: Path, tmp_path: Path) -> Report:
    store = Store(tmp_path / "store")
    store.ingest_source(stream, source_relpath=stream.name)
    events = store.read_events()
    dispatches = [dispatch_ref(e) for e in events if e.producer_schema == PRODUCER_SCHEMA_ID]
    correlation = correlate(dispatches, [])
    return analyze_report(events, correlation)


def _compare(
    stream: Path,
    tmp_path: Path,
    a: CohortSelector,
    b: CohortSelector,
    metric: str = "latency_ms",
) -> Comparison:
    return compare_cohorts(_report(stream, tmp_path), a, b, decision_metric=metric)


# --------------------------------------------------------------------------- #
# VALID PATH: comparable + sufficiently-sized single-dimension contrast.
# --------------------------------------------------------------------------- #


def test_valid_model_contrast_computes_reproducible_delta(
    compare_cohorts_stream: Path, tmp_path: Path
) -> None:
    c = _compare(
        compare_cohorts_stream,
        tmp_path,
        CohortSelector(skill="repo-sync", model="claude"),
        CohortSelector(skill="repo-sync", model="gpt-5.5"),
    )
    assert c.comparable is True
    assert c.refused is False
    assert c.refusals == ()
    assert c.contrast_dimension == "model"
    decision = c.decision()
    assert decision.a_mean == 120.0  # (100+110+120+130+140)/5
    assert decision.b_mean == 220.0  # (200+210+220+230+240)/5
    assert decision.delta == -100.0  # A - B; cohort A has the lower latency
    assert decision.direction is not None
    assert "cohort A has the lower measured latency_ms" in decision.direction
    assert c.directional_verdict == decision.direction


def test_valid_delta_carries_correlation_not_causation_caveat(
    compare_cohorts_stream: Path, tmp_path: Path
) -> None:
    c = _compare(
        compare_cohorts_stream,
        tmp_path,
        CohortSelector(skill="repo-sync", model="claude"),
        CohortSelector(skill="repo-sync", model="gpt-5.5"),
    )
    joined = " ".join(c.caveats)
    assert "CORRELATION, NOT CAUSATION" in joined
    # Held-constant + absent-confound disclosure is present.
    assert "Held constant across both cohorts: skill" in joined
    assert "UNOBSERVED CONFOUNDS" in joined
    assert "project" in joined and "task_type" in joined
    # A valid delta is NEVER phrased as causing a better outcome.
    assert c.directional_verdict is not None
    lowered = c.directional_verdict.lower()
    assert "cause" not in lowered.replace("causation", "")
    assert "better" not in lowered
    assert "win" not in lowered


def test_valid_skill_contrast_holds_model_constant(
    compare_cohorts_stream: Path, tmp_path: Path
) -> None:
    c = _compare(
        compare_cohorts_stream,
        tmp_path,
        CohortSelector(skill="repo-sync", model="claude"),
        CohortSelector(skill="plan-init", model="claude"),
    )
    assert c.refused is False
    assert c.contrast_dimension == "skill"
    assert c.held_constant == ("model",)
    assert c.decision().delta == -200.0  # 120 - 320


def test_decision_metric_is_pre_declared_and_stated(
    compare_cohorts_stream: Path, tmp_path: Path
) -> None:
    c = _compare(
        compare_cohorts_stream,
        tmp_path,
        CohortSelector(skill="repo-sync", model="claude"),
        CohortSelector(skill="repo-sync", model="gpt-5.5"),
    )
    assert c.decision_metric == "latency_ms"
    assert c.decision().is_decision_metric is True
    # The metric is named up front in the caveats ("defined before looking").
    assert any("Pre-declared decision metric" in cav for cav in c.caveats)
    assert c.caveats[0].startswith("Pre-declared decision metric")


# --------------------------------------------------------------------------- #
# SAMPLE-SIZE guard: undersized cohorts REFUSE (the real 2-record stream refuses).
# --------------------------------------------------------------------------- #


def test_real_data_compare_refuses_as_undersized(real_stream: Path, tmp_path: Path) -> None:
    # The real stream is two stub records -> two N=1 cohorts. An honest compare REFUSES.
    c = _compare(
        real_stream,
        tmp_path,
        CohortSelector(model="gpt-5.6-sol"),
        CohortSelector(model="claude"),
    )
    assert c.refused is True
    assert c.directional_verdict is None
    assert any("insufficient sample" in r for r in c.refusals)
    assert f"N={MIN_DIRECTIONAL_N}" in " ".join(c.refusals)
    # No metric carries a delta when the comparison is refused.
    assert all(m.delta is None for m in c.metrics)


def test_undersized_still_discloses_sample_sizes(real_stream: Path, tmp_path: Path) -> None:
    c = _compare(
        real_stream,
        tmp_path,
        CohortSelector(model="gpt-5.6-sol"),
        CohortSelector(model="claude"),
    )
    # Missing-data disclosure survives refusal: sample sizes are stated.
    assert c.cohort_a.count == 1
    assert c.cohort_b.count == 1
    decision = c.decision()
    assert decision.a_total == 1
    assert decision.b_total == 1


def test_threshold_is_the_documented_constant(compare_cohorts_stream: Path, tmp_path: Path) -> None:
    # Exactly-at-threshold cohorts (N=5) are NOT undersized; below would refuse.
    assert MIN_DIRECTIONAL_N == 5
    c = _compare(
        compare_cohorts_stream,
        tmp_path,
        CohortSelector(skill="repo-sync", model="claude"),
        CohortSelector(skill="repo-sync", model="gpt-5.5"),
    )
    assert c.cohort_a.count == MIN_DIRECTIONAL_N
    assert c.refused is False  # exactly at the floor is allowed


# --------------------------------------------------------------------------- #
# INCOMPARABLE guard: cross-schema / unknown / confound / no-contrast REFUSE.
# --------------------------------------------------------------------------- #


def test_cross_schema_and_unknown_cohort_refuse(
    compare_incomparable_stream: Path, tmp_path: Path
) -> None:
    c = _compare(
        compare_incomparable_stream,
        tmp_path,
        CohortSelector(skill="repo-sync", model="claude"),  # skillmesh-v1
        CohortSelector(skill="repo-sync", model="gpt-5.5"),  # unknown schema
    )
    assert c.refused is True
    joined = " ".join(c.refusals)
    assert "producer schema differs" in joined
    assert "never merged or compared" in joined  # unknown cohort refusal
    assert "skillmesh-v1" in joined and "unknown" in joined
    assert all(m.delta is None for m in c.metrics)


def test_confounded_two_dimension_pick_refuses(
    compare_cohorts_stream: Path, tmp_path: Path
) -> None:
    c = _compare(
        compare_cohorts_stream,
        tmp_path,
        CohortSelector(skill="repo-sync", model="claude"),
        CohortSelector(skill="plan-init", model="gpt-5.5"),  # BOTH skill and model differ
    )
    assert c.refused is True
    assert c.contrast_dimension is None
    assert any("confound" in r for r in c.refusals)
    assert any("multiple stratification dimensions" in r for r in c.refusals)


def test_identical_strata_refuse_no_contrast(compare_cohorts_stream: Path, tmp_path: Path) -> None:
    # Both selectors resolve to the SAME cohort -> nothing to contrast.
    same = CohortSelector(skill="repo-sync", model="claude")
    c = _compare(compare_cohorts_stream, tmp_path, same, same)
    assert c.refused is True
    assert any("no contrast" in r for r in c.refusals)


# --------------------------------------------------------------------------- #
# PLACEHOLDER guard: token/cost carry NO directional verdict (never 0/"equal").
# --------------------------------------------------------------------------- #


def test_placeholder_metric_refuses_even_when_cohorts_are_sized(
    compare_cohorts_stream: Path, tmp_path: Path
) -> None:
    # Cohorts are sufficiently sized and comparable, but cost_usd is placeholder.
    c = _compare(
        compare_cohorts_stream,
        tmp_path,
        CohortSelector(skill="repo-sync", model="claude"),
        CohortSelector(skill="repo-sync", model="gpt-5.5"),
        metric="cost_usd",
    )
    decision = c.decision()
    assert decision.metric == "cost_usd"
    assert decision.comparable is False
    assert decision.delta is None
    assert decision.direction is None
    assert c.directional_verdict is None  # a placeholder metric never yields a verdict
    joined = " ".join(decision.refusal_reasons)
    assert "UNAVAILABLE" in joined
    assert "never read as 0 or 'equal'" in joined


def test_non_decision_placeholder_metrics_are_disclosed_as_refused(
    compare_cohorts_stream: Path, tmp_path: Path
) -> None:
    c = _compare(
        compare_cohorts_stream,
        tmp_path,
        CohortSelector(skill="repo-sync", model="claude"),
        CohortSelector(skill="repo-sync", model="gpt-5.5"),
    )
    by_metric = {m.metric: m for m in c.metrics}
    # latency (the one real metric) is comparable; token/cost are not.
    assert by_metric["latency_ms"].comparable is True
    for name in ("tokens_in", "tokens_out", "cost_usd"):
        assert by_metric[name].comparable is False
        assert by_metric[name].delta is None
        assert by_metric[name].a_status == "unavailable"


# --------------------------------------------------------------------------- #
# DETERMINISM: same input -> byte-identical comparison JSON (golden pins it).
# --------------------------------------------------------------------------- #


def test_comparison_json_matches_golden(compare_cohorts_stream: Path, tmp_path: Path) -> None:
    c = _compare(
        compare_cohorts_stream,
        tmp_path,
        CohortSelector(skill="repo-sync", model="claude"),
        CohortSelector(skill="repo-sync", model="gpt-5.5"),
    )
    # Only the golden's on-disk newlines are normalized (git autocrlf may check the
    # fixture out as CRLF on Windows); the renderer always emits LF, so a renderer CRLF
    # regression would still fail this assertion.
    golden = GOLDEN_JSON.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert render_comparison_json(c) == golden


def test_comparison_is_byte_identical_across_independent_ingests(
    compare_cohorts_stream: Path, tmp_path: Path
) -> None:
    a = _compare(
        compare_cohorts_stream,
        tmp_path / "a",
        CohortSelector(skill="repo-sync", model="claude"),
        CohortSelector(skill="repo-sync", model="gpt-5.5"),
    )
    b = _compare(
        compare_cohorts_stream,
        tmp_path / "b",
        CohortSelector(skill="repo-sync", model="claude"),
        CohortSelector(skill="repo-sync", model="gpt-5.5"),
    )
    assert render_comparison_json(a) == render_comparison_json(b)


def test_comparison_json_is_versioned_and_states_refusal(
    compare_incomparable_stream: Path, tmp_path: Path
) -> None:
    c = _compare(
        compare_incomparable_stream,
        tmp_path,
        CohortSelector(skill="repo-sync", model="claude"),
        CohortSelector(skill="repo-sync", model="gpt-5.5"),
    )
    payload = c.to_json()
    assert payload["schema_version"] == 1
    assert payload["refused"] is True
    assert payload["directional_verdict"] is None
    assert payload["refusals"]  # non-empty


# --------------------------------------------------------------------------- #
# Selector parsing + selection errors.
# --------------------------------------------------------------------------- #


def test_parse_selector_roundtrip() -> None:
    sel = parse_selector("skill=repo-sync, model=claude , producer_schema=skillmesh-v1")
    assert sel == CohortSelector(skill="repo-sync", model="claude", producer_schema="skillmesh-v1")


def test_parse_selector_rejects_unknown_key() -> None:
    try:
        parse_selector("bogus=x")
    except ValueError as exc:
        assert "unknown selector key" in str(exc)
    else:  # pragma: no cover - must raise
        raise AssertionError("expected ValueError")


def test_ambiguous_selector_is_a_hard_error(compare_cohorts_stream: Path, tmp_path: Path) -> None:
    report = _report(compare_cohorts_stream, tmp_path)
    # skill=repo-sync alone matches TWO cohorts (claude, gpt-5.5) -> ambiguous.
    try:
        compare_cohorts(
            report,
            CohortSelector(skill="repo-sync"),
            CohortSelector(skill="plan-init", model="claude"),
        )
    except CohortSelectionError as exc:
        assert "ambiguous" in str(exc)
    else:  # pragma: no cover - must raise
        raise AssertionError("expected CohortSelectionError")


def test_unknown_decision_metric_rejected(compare_cohorts_stream: Path, tmp_path: Path) -> None:
    report = _report(compare_cohorts_stream, tmp_path)
    try:
        compare_cohorts(
            report,
            CohortSelector(skill="repo-sync", model="claude"),
            CohortSelector(skill="repo-sync", model="gpt-5.5"),
            decision_metric="not_a_metric",
        )
    except ValueError as exc:
        assert "unknown decision metric" in str(exc)
    else:  # pragma: no cover - must raise
        raise AssertionError("expected ValueError")
