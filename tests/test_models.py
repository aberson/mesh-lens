"""Tests for the versioned normalized shapes (plan sec. 5, sec. 6).

The load-bearing measurement-validity property is that a stub-zero, a measured
zero, and a missing value stay three DISTINCT states -- none collapses into
another. Plus: readers tolerate an older schema_version and refuse a newer one.
"""

from __future__ import annotations

import pytest

from mesh_lens.models import (
    CURRENT_SCHEMA_VERSION,
    Metric,
    MetricStatus,
    NormalizedInvocation,
    NormalizedOutcome,
    Provenance,
    SchemaVersionError,
    check_schema_version,
)


def _prov() -> Provenance:
    return Provenance(source_relpath="invocations.jsonl", line_number=17, content_hash="deadbeef")


# --------------------------------------------------------------------------- #
# Metric tri-state -- the honesty crux
# --------------------------------------------------------------------------- #


def test_placeholder_zero_measured_zero_and_missing_are_all_distinct() -> None:
    placeholder_zero = Metric(MetricStatus.PLACEHOLDER, 0)
    measured_zero = Metric(MetricStatus.MEASURED, 0)
    missing = Metric(MetricStatus.UNAVAILABLE, None)

    # All three are unequal objects (no state collapses into another).
    assert placeholder_zero != measured_zero
    assert placeholder_zero != missing
    assert measured_zero != missing

    # Only the measured one exposes a measurement; the placeholder-zero does NOT.
    assert measured_zero.is_measured is True
    assert measured_zero.measured_value() == 0
    assert placeholder_zero.is_measured is False
    assert placeholder_zero.measured_value() is None
    assert missing.measured_value() is None

    # The placeholder still preserves its raw 0, distinct from a missing value.
    assert placeholder_zero.raw_value == 0
    assert missing.raw_value is None


def test_metric_round_trip() -> None:
    for metric in (
        Metric(MetricStatus.PLACEHOLDER, 0),
        Metric(MetricStatus.MEASURED, 1234),
        Metric(MetricStatus.MEASURED, 0.0021),
        Metric(MetricStatus.UNAVAILABLE, None),
    ):
        assert Metric.from_json(metric.to_json()) == metric


# --------------------------------------------------------------------------- #
# Provenance identity
# --------------------------------------------------------------------------- #


def test_provenance_ref_format() -> None:
    assert _prov().ref == "invocations.jsonl@17"


def test_provenance_round_trip() -> None:
    prov = _prov()
    assert Provenance.from_json(prov.to_json()) == prov


# --------------------------------------------------------------------------- #
# schema_version tolerate-older / refuse-newer
# --------------------------------------------------------------------------- #


def test_check_schema_version_tolerates_current_and_older() -> None:
    assert check_schema_version(CURRENT_SCHEMA_VERSION) == CURRENT_SCHEMA_VERSION
    assert check_schema_version(0) == 0  # older tolerated


def test_check_schema_version_refuses_newer() -> None:
    with pytest.raises(SchemaVersionError):
        check_schema_version(CURRENT_SCHEMA_VERSION + 1)


def test_invocation_from_json_refuses_newer_schema() -> None:
    record = _sample_invocation()
    payload = record.to_json_dict()
    payload["schema_version"] = CURRENT_SCHEMA_VERSION + 1
    with pytest.raises(SchemaVersionError):
        NormalizedInvocation.from_json_dict(payload)


# --------------------------------------------------------------------------- #
# Round-trips
# --------------------------------------------------------------------------- #


def _sample_invocation() -> NormalizedInvocation:
    return NormalizedInvocation(
        schema_version=CURRENT_SCHEMA_VERSION,
        provenance=_prov(),
        producer_schema="skillmesh-v1",
        timestamp="2026-07-24T15:13:27Z",
        skill="plan-init",
        model="claude",
        latency_ms=4,
        verdict="stub",
        tokens_in=Metric(MetricStatus.PLACEHOLDER, 0),
        tokens_out=Metric(MetricStatus.PLACEHOLDER, 0),
        cost_usd=Metric(MetricStatus.PLACEHOLDER, 0),
        raw_field_names=("cost_usd", "latency_ms", "model", "skill", "timestamp"),
    )


def test_invocation_round_trip() -> None:
    record = _sample_invocation()
    assert NormalizedInvocation.from_json_dict(record.to_json_dict()) == record


def _sample_outcome() -> NormalizedOutcome:
    return NormalizedOutcome(
        schema_version=CURRENT_SCHEMA_VERSION,
        provenance=_prov(),
        outcome_class="git log of the target repo",
        fields=(("sha", "abc123"), ("subject", "repo-sync: step 2")),
    )


def test_outcome_round_trip_and_stays_unjoined_by_default() -> None:
    outcome = _sample_outcome()
    assert outcome.join_key is None  # Step 2 shape only; no join invented
    assert NormalizedOutcome.from_json_dict(outcome.to_json_dict()) == outcome


def test_outcome_from_json_refuses_newer_schema() -> None:
    payload = _sample_outcome().to_json_dict()
    payload["schema_version"] = CURRENT_SCHEMA_VERSION + 1
    with pytest.raises(SchemaVersionError):
        NormalizedOutcome.from_json_dict(payload)
