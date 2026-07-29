"""Tests for Step 3 correlation (plan sec. 7) -- the module whose cardinal sin is a BAD JOIN.

Pins the measurement-validity contract:
  * On REAL data ALL five outcome classes resolve to UNJOINED (3 ambiguous, 2
    absent) with inventory-sourced reasons -- NO timestamp/skill-window join is ever
    emitted, even when a dispatch and an outcome share a skill name or a second.
  * Missing outcomes are diagnosed (count 0), never inferred.
  * The strong-key join PATH is real and tested: a SYNTHETIC fixture that carries a
    stable run key joins and preserves BOTH source provenances. Real data still
    resolves to all-unjoined under the same engine.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from conftest import SYNTHETIC_KEYED_CLASS, strong_key_inventory
from mesh_lens.adapters.outcomes import ingest_outcome_source, parse_keyed_outcomes
from mesh_lens.correlate import (
    JoinStatus,
    correlate,
    dispatch_ref,
    keyed_dispatch_refs,
)
from mesh_lens.inventory import (
    JoinStrength,
    OutcomeClass,
    build_inventory,
)
from mesh_lens.models import NormalizedOutcome, Provenance
from mesh_lens.store import Store

_REAL_CLASSES = {
    OutcomeClass.BUILD_STEP_REPORT.value: JoinStatus.UNJOINED_AMBIGUOUS,
    OutcomeClass.GH_ISSUE_STATE.value: JoinStatus.UNJOINED_ABSENT,
    OutcomeClass.GIT_LOG.value: JoinStatus.UNJOINED_AMBIGUOUS,
    OutcomeClass.PLAN_STATUS.value: JoinStatus.UNJOINED_ABSENT,
    OutcomeClass.SKILL_ITERATE_LOG.value: JoinStatus.UNJOINED_AMBIGUOUS,
}


def _real_dispatch_refs(real_stream: Path, tmp_path: Path) -> list:
    store = Store(tmp_path / "store")
    store.ingest_source(real_stream, source_relpath="invocations.jsonl")
    return [dispatch_ref(e) for e in store.read_events()]


def _all_real_outcomes(
    build_step_report_sample: Path,
    gh_issues_sample: Path,
    git_log_sample: Path,
    plan_status_sample: Path,
    skill_iterate_sample: Path,
) -> list[NormalizedOutcome]:
    sources = [
        (build_step_report_sample, OutcomeClass.BUILD_STEP_REPORT.value),
        (gh_issues_sample, OutcomeClass.GH_ISSUE_STATE.value),
        (git_log_sample, OutcomeClass.GIT_LOG.value),
        (plan_status_sample, OutcomeClass.PLAN_STATUS.value),
        (skill_iterate_sample, OutcomeClass.SKILL_ITERATE_LOG.value),
    ]
    records: list[NormalizedOutcome] = []
    for path, cls in sources:
        records.extend(ingest_outcome_source(path, cls).records)
    return records


# --------------------------------------------------------------------------- #
# REAL data: every class stays UNJOINED, with the right reason. No bad join.
# --------------------------------------------------------------------------- #


def test_real_data_resolves_to_all_unjoined(
    real_stream: Path,
    tmp_path: Path,
    build_step_report_sample: Path,
    gh_issues_sample: Path,
    git_log_sample: Path,
    plan_status_sample: Path,
    skill_iterate_sample: Path,
) -> None:
    dispatches = _real_dispatch_refs(real_stream, tmp_path)
    outcomes = _all_real_outcomes(
        build_step_report_sample,
        gh_issues_sample,
        git_log_sample,
        plan_status_sample,
        skill_iterate_sample,
    )
    result = correlate(dispatches, outcomes)

    assert result.all_unjoined is True
    assert result.joined == ()
    for cls, expected_status in _REAL_CLASSES.items():
        diag = result.diagnostic_for(cls)
        assert diag is not None
        assert diag.joined_count == 0
        assert diag.join_status is expected_status
        assert diag.join_status is not JoinStatus.JOINED


def test_no_diagnostic_claims_a_join_on_real_data(
    real_stream: Path,
    tmp_path: Path,
    build_step_report_sample: Path,
    gh_issues_sample: Path,
    git_log_sample: Path,
    plan_status_sample: Path,
    skill_iterate_sample: Path,
) -> None:
    dispatches = _real_dispatch_refs(real_stream, tmp_path)
    outcomes = _all_real_outcomes(
        build_step_report_sample,
        gh_issues_sample,
        git_log_sample,
        plan_status_sample,
        skill_iterate_sample,
    )
    result = correlate(dispatches, outcomes)
    for diag in result.diagnostics:
        assert diag.join_status is not JoinStatus.JOINED
        assert "provable run/session key" not in diag.reason


def test_shared_skill_and_timestamp_never_produce_a_join() -> None:
    """The cardinal-sin guard: identical skill AND second must NOT join."""
    # A dispatch and an outcome that share skill 'plan-init' and a timestamp window.
    dispatch = dispatch_ref(_fake_invocation("invocations.jsonl", 1))
    outcome = NormalizedOutcome(
        schema_version=1,
        provenance=Provenance("git_log.sample.tsv", 1, "h"),
        outcome_class=OutcomeClass.GIT_LOG.value,
        fields=(("skill", "plan-init"), ("author_date", "2026-07-24T15:13:27Z")),
        join_key=None,
    )
    result = correlate([dispatch], [outcome])
    assert result.all_unjoined is True
    diag = result.diagnostic_for(OutcomeClass.GIT_LOG.value)
    assert diag is not None
    assert diag.join_status is JoinStatus.UNJOINED_AMBIGUOUS


def test_missing_outcomes_are_diagnosed_not_inferred() -> None:
    """With no outcomes ingested, every class is still diagnosed (count 0)."""
    result = correlate([], [])
    for cls in _REAL_CLASSES:
        diag = result.diagnostic_for(cls)
        assert diag is not None
        assert diag.outcome_count == 0
        assert diag.joined_count == 0
        assert "reported missing, never inferred" in diag.reason


# --------------------------------------------------------------------------- #
# dispatch_ref honesty
# --------------------------------------------------------------------------- #


def test_dispatch_ref_has_no_run_key_under_real_inventory() -> None:
    ref = dispatch_ref(_fake_invocation("invocations.jsonl", 17))
    assert ref.run_key is None
    assert ref.provenance.ref == "invocations.jsonl@17"


def test_dispatch_ref_fails_loud_if_inventory_claims_a_strong_run_key() -> None:
    """A silent None here would make every join silently miss -- so it must fail loud."""
    inv = build_inventory()
    keys = tuple(
        replace(k, join_strength=JoinStrength.STRONG_KEY)
        if k.name == "run/session/record id"
        else k
        for k in inv.correlation_keys
    )
    strong = replace(inv, correlation_keys=keys)
    with pytest.raises(NotImplementedError):
        dispatch_ref(_fake_invocation("invocations.jsonl", 1), strong)


# --------------------------------------------------------------------------- #
# SYNTHETIC keyed join PATH: a stable key joins + preserves BOTH provenances
# --------------------------------------------------------------------------- #


def test_synthetic_keyed_fixture_joins_and_preserves_both_provenances(
    synthetic_keyed_dispatches: Path,
    synthetic_keyed_outcomes: Path,
) -> None:
    inv = strong_key_inventory()
    dispatches = keyed_dispatch_refs(
        synthetic_keyed_dispatches.read_text(encoding="utf-8"),
        "synthetic_keyed_dispatches.jsonl",
    )
    outcomes = parse_keyed_outcomes(
        synthetic_keyed_outcomes.read_text(encoding="utf-8"),
        "synthetic_keyed_outcomes.jsonl",
        SYNTHETIC_KEYED_CLASS,
        inv,
    ).records

    result = correlate(dispatches, outcomes, inv)

    # Exactly one provable join (run-7f3a2b); run-orphan has a key but no matching
    # dispatch, run-unmatched dispatch has no matching outcome -> neither joins.
    assert len(result.joined) == 1
    pair = result.joined[0]
    assert pair.run_key == "run-7f3a2b"
    assert pair.outcome_class == SYNTHETIC_KEYED_CLASS
    # BOTH source provenances preserved.
    assert pair.dispatch_provenance.ref == "synthetic_keyed_dispatches.jsonl@1"
    assert pair.outcome_provenance.ref == "synthetic_keyed_outcomes.jsonl@1"
    assert len(pair.dispatch_provenance.content_hash) == 64
    assert len(pair.outcome_provenance.content_hash) == 64

    diag = result.diagnostic_for(SYNTHETIC_KEYED_CLASS)
    assert diag is not None
    assert diag.join_status is JoinStatus.JOINED
    assert diag.joined_count == 1
    assert diag.outcome_count == 2  # both records diagnosed


def test_synthetic_inventory_leaves_real_classes_unjoined(
    synthetic_keyed_dispatches: Path,
    git_log_sample: Path,
) -> None:
    """Adding a strong-keyed synthetic class does NOT make real classes joinable."""
    inv = strong_key_inventory()
    dispatches = keyed_dispatch_refs(
        synthetic_keyed_dispatches.read_text(encoding="utf-8"),
        "synthetic_keyed_dispatches.jsonl",
    )
    real_outcomes = ingest_outcome_source(git_log_sample, OutcomeClass.GIT_LOG.value, inv).records
    result = correlate(dispatches, list(real_outcomes), inv)
    diag = result.diagnostic_for(OutcomeClass.GIT_LOG.value)
    assert diag is not None
    assert diag.join_status is JoinStatus.UNJOINED_AMBIGUOUS
    assert diag.joined_count == 0


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _fake_invocation(relpath: str, line: int):
    from mesh_lens.models import Metric, MetricStatus, NormalizedInvocation

    prov = Provenance(source_relpath=relpath, line_number=line, content_hash="h")
    placeholder = Metric(MetricStatus.PLACEHOLDER, 0)
    return NormalizedInvocation(
        schema_version=1,
        provenance=prov,
        producer_schema="skillmesh-v1",
        timestamp="2026-07-24T15:13:27Z",
        skill="plan-init",
        model="claude",
        latency_ms=4,
        verdict="stub",
        tokens_in=placeholder,
        tokens_out=placeholder,
        cost_usd=placeholder,
        raw_field_names=(),
    )
