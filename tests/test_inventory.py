"""Tests for the Step 1 availability audit.

These assert the *verified* classifications hold, and that the audit degrades
gracefully on an absent/empty telemetry stream. They intentionally pin the
honest facts (tokens/cost carry no signal today; no run/session key exists; no
outcome class has a strong dispatch key) so a future regression that quietly
"completes" the inventory fails CI.
"""

from __future__ import annotations

import json
from pathlib import Path

from mesh_lens.inventory import (
    PINNED_PRODUCER_FIELDS,
    Availability,
    JoinStrength,
    OutcomeClass,
    ValueSignal,
    audit_correlation_keys,
    audit_producer_fields,
    audit_telemetry_stream,
    build_inventory,
    outcome_audit_by_class,
)

# --------------------------------------------------------------------------- #
# Producer fields
# --------------------------------------------------------------------------- #


def test_all_eight_producer_fields_present() -> None:
    fields = audit_producer_fields()
    assert tuple(f.name for f in fields) == PINNED_PRODUCER_FIELDS
    assert all(f.availability is Availability.PRESENT for f in fields)


def test_tokens_and_cost_present_but_always_zero() -> None:
    """The honest crux: tokens/cost are structurally present but carry no signal."""
    by_name = {f.name: f for f in audit_producer_fields()}
    for name in ("tokens_in", "tokens_out", "cost_usd"):
        assert by_name[name].availability is Availability.PRESENT
        assert by_name[name].value_signal is ValueSignal.ALWAYS_ZERO


def test_latency_verdict_timestamp_carry_real_signal() -> None:
    by_name = {f.name: f for f in audit_producer_fields()}
    for name in ("timestamp", "skill", "model", "latency_ms", "verdict"):
        assert by_name[name].value_signal is ValueSignal.REAL


# --------------------------------------------------------------------------- #
# Correlation keys -- the no-run-key ambiguity
# --------------------------------------------------------------------------- #


def test_no_run_or_session_key_exists() -> None:
    keys = {k.name: k for k in audit_correlation_keys()}
    run_key = keys["run/session/record id"]
    assert run_key.availability is Availability.ABSENT
    assert run_key.join_strength is JoinStrength.NONE


def test_timestamp_and_skill_joins_are_ambiguous() -> None:
    keys = {k.name: k for k in audit_correlation_keys()}
    assert keys["timestamp window"].availability is Availability.AMBIGUOUS
    assert keys["timestamp window"].join_strength is JoinStrength.TIMESTAMP_WINDOW_ONLY
    assert keys["skill name"].availability is Availability.AMBIGUOUS
    assert keys["skill name"].join_strength is JoinStrength.SKILL_NAME_ONLY


# --------------------------------------------------------------------------- #
# Outcome-artifact classes
# --------------------------------------------------------------------------- #


def test_five_outcome_classes_audited() -> None:
    artifacts = build_inventory().outcome_artifacts
    assert len(artifacts) == 5


def test_no_outcome_class_has_a_strong_dispatch_key() -> None:
    """Plan sec. 2/6: no class carries a dispatch-correlatable key; all unjoined."""
    artifacts = build_inventory().outcome_artifacts
    assert all(a.join_strength is not JoinStrength.STRONG_KEY for a in artifacts)


def test_outcome_class_availability_matches_verified_reality() -> None:
    by_name = {a.name: a for a in build_inventory().outcome_artifacts}

    build_step = next(a for n, a in by_name.items() if n.startswith(".build-step"))
    assert build_step.exists is True
    assert build_step.availability is Availability.AMBIGUOUS
    assert build_step.join_strength is JoinStrength.SKILL_NAME_ONLY

    gh = by_name["GitHub issue states"]
    assert gh.exists is True
    assert gh.availability is Availability.ABSENT
    assert gh.join_strength is JoinStrength.NONE

    git = next(a for n, a in by_name.items() if n.startswith("git log"))
    assert git.availability is Availability.AMBIGUOUS
    assert git.join_strength is JoinStrength.TIMESTAMP_WINDOW_ONLY

    plan = next(a for n, a in by_name.items() if "Step N markers" in n)
    assert plan.availability is Availability.ABSENT
    assert plan.join_strength is JoinStrength.NONE

    skill_iter = by_name["skill-iterate run-logs"]
    assert skill_iter.exists is True  # verified present at audit time
    assert skill_iter.availability is Availability.AMBIGUOUS
    assert skill_iter.join_strength is JoinStrength.SKILL_NAME_ONLY


def test_outcome_class_split_is_three_ambiguous_two_absent() -> None:
    artifacts = build_inventory().outcome_artifacts
    ambiguous = [a for a in artifacts if a.availability is Availability.AMBIGUOUS]
    absent = [a for a in artifacts if a.availability is Availability.ABSENT]
    assert len(ambiguous) == 3
    assert len(absent) == 2


def test_outcome_audit_by_class_indexes_all_five_stable_ids() -> None:
    """Step 3 keys its adapters/correlation on these stable ids (single source)."""
    by_class = outcome_audit_by_class(build_inventory())
    assert set(by_class) == {c.value for c in OutcomeClass}
    # No real class is strong-keyed -> Step 3 joins nothing on real data.
    assert all(a.join_strength is not JoinStrength.STRONG_KEY for a in by_class.values())


# --------------------------------------------------------------------------- #
# Live telemetry-stream audit against the frozen real fixture
# --------------------------------------------------------------------------- #


def test_real_fixture_matches_pinned_contract(real_stream: Path) -> None:
    audit = audit_telemetry_stream(real_stream)
    assert audit.exists is True
    assert audit.record_count == 2
    assert audit.malformed_count == 0
    assert audit.matches_pinned_contract is True
    assert audit.observed_field_sets == (tuple(sorted(PINNED_PRODUCER_FIELDS)),)


def test_real_fixture_is_genuine_stub_data_not_fabricated(real_stream: Path) -> None:
    """Prove the frozen fixture is the real stub stream: 2 records, all zeroed."""
    lines = [ln for ln in real_stream.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2
    for line in lines:
        rec = json.loads(line)
        assert set(rec) == set(PINNED_PRODUCER_FIELDS)
        assert rec["verdict"] == "stub"
        assert rec["tokens_in"] == 0
        assert rec["tokens_out"] == 0
        assert rec["cost_usd"] == 0
        assert rec["latency_ms"] > 0  # latency is the one real signal even in stub


# --------------------------------------------------------------------------- #
# Graceful degradation (plan sec. 6, "Independent by contract")
# --------------------------------------------------------------------------- #


def test_empty_stream_degrades_gracefully(empty_stream: Path) -> None:
    audit = audit_telemetry_stream(empty_stream)
    assert audit.exists is True
    assert audit.record_count == 0
    assert audit.matches_pinned_contract is False
    assert "zero records" in audit.note


def test_absent_stream_degrades_gracefully(absent_stream: Path) -> None:
    audit = audit_telemetry_stream(absent_stream)
    assert audit.exists is False
    assert audit.record_count == 0
    assert audit.matches_pinned_contract is False
    assert "absent" in audit.note


def test_divergent_field_set_is_not_merged(tmp_path: Path) -> None:
    """A record with an unexpected field set must not count as contract-matching."""
    stream = tmp_path / "mixed.jsonl"
    stream.write_text(
        json.dumps(
            {
                "timestamp": "2026-07-24T15:13:27Z",
                "skill": "plan-init",
                "model": "claude",
                "tokens_in": 0,
                "tokens_out": 0,
                "latency_ms": 4,
                "cost_usd": 0,
                "verdict": "stub",
                "schema_version": 2,  # extra field -> unknown cohort
            }
        )
        + "\n",
        encoding="utf-8",
    )
    audit = audit_telemetry_stream(stream)
    assert audit.record_count == 1
    assert audit.matches_pinned_contract is False


def test_malformed_line_counted_without_aborting(tmp_path: Path) -> None:
    stream = tmp_path / "malformed.jsonl"
    good = json.dumps(dict.fromkeys(PINNED_PRODUCER_FIELDS, 0))
    stream.write_text(good + "\nnot json at all\n", encoding="utf-8")
    audit = audit_telemetry_stream(stream)
    assert audit.record_count == 1
    assert audit.malformed_count == 1
