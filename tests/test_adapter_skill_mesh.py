"""Tests for the Skill Mesh dispatch adapter (plan sec. 6).

Pins the two producer-honesty rules:
  * producer_schema == "skillmesh-v1" ONLY on an exact eight-field set; a 9-field,
    a 7-field, and a renamed-field row each -> "unknown" (never skillmesh-v1).
  * token/cost metrics stay honest: a field the producer hardcodes (the
    ALWAYS_ZERO set) is PLACEHOLDER on EVERY verdict (pass/fail/stub) so its
    measured_value() is None; a genuinely-measured field (e.g. latency_ms) is
    MEASURED; an absent field is UNAVAILABLE -- no unavailable value is fabricated.
"""

from __future__ import annotations

from typing import Any

from mesh_lens.adapters.skill_mesh import (
    PRODUCER_SCHEMA_UNKNOWN,
    infer_producer_schema,
    normalize_row,
)
from mesh_lens.inventory import PINNED_PRODUCER_FIELDS, PRODUCER_SCHEMA_ID
from mesh_lens.models import MetricStatus, Provenance


def _prov() -> Provenance:
    return Provenance(source_relpath="invocations.jsonl", line_number=1, content_hash="h")


def _exact_eight(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "timestamp": "2026-07-24T15:13:27Z",
        "skill": "plan-init",
        "model": "claude",
        "tokens_in": 0,
        "tokens_out": 0,
        "latency_ms": 4,
        "cost_usd": 0,
        "verdict": "stub",
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------- #
# producer_schema inference: exact-8 -> skillmesh-v1; anything else -> unknown
# --------------------------------------------------------------------------- #


def test_exact_eight_field_set_is_skillmesh_v1() -> None:
    assert infer_producer_schema(PINNED_PRODUCER_FIELDS) == PRODUCER_SCHEMA_ID
    record = normalize_row(_exact_eight(), _prov())
    assert record.producer_schema == PRODUCER_SCHEMA_ID


def test_nine_field_row_is_unknown_not_skillmesh_v1() -> None:
    row = _exact_eight(schema_version=2)  # one extra field
    record = normalize_row(row, _prov())
    assert record.producer_schema == PRODUCER_SCHEMA_UNKNOWN
    assert record.producer_schema != PRODUCER_SCHEMA_ID


def test_seven_field_row_is_unknown() -> None:
    row = _exact_eight()
    del row["cost_usd"]  # one missing field
    record = normalize_row(row, _prov())
    assert record.producer_schema == PRODUCER_SCHEMA_UNKNOWN


def test_renamed_field_row_is_unknown() -> None:
    row = _exact_eight()
    row["cost"] = row.pop("cost_usd")  # renamed field; count still 8
    assert len(row) == 8
    record = normalize_row(row, _prov())
    assert record.producer_schema == PRODUCER_SCHEMA_UNKNOWN


# --------------------------------------------------------------------------- #
# Tri-state metric derivation -- no fabrication
# --------------------------------------------------------------------------- #


def test_stub_row_tokens_and_cost_are_placeholders_latency_is_real() -> None:
    record = normalize_row(_exact_eight(verdict="stub"), _prov())
    for metric in (record.tokens_in, record.tokens_out, record.cost_usd):
        assert metric.status is MetricStatus.PLACEHOLDER
        assert metric.is_measured is False  # producer never measures these
        assert metric.measured_value() is None
    assert record.latency_ms == 4  # latency carries real signal even in stub


def test_pass_row_tokens_and_cost_are_placeholders_not_measured() -> None:
    """The fix: producer knowledge, not verdict, drives placeholder status.

    Step 1 verified the producer hardcodes tokens/cost to 0 on EVERY write path, so
    even a verdict=pass record's 0 is a placeholder -- never a measured 0 (that
    would fabricate a '0 tokens measured' the producer never measured). Only
    latency_ms is genuinely measured today.
    """
    record = normalize_row(
        _exact_eight(verdict="pass", tokens_in=0, tokens_out=0, cost_usd=0), _prov()
    )
    for metric in (record.tokens_in, record.tokens_out, record.cost_usd):
        assert metric.status is MetricStatus.PLACEHOLDER
        assert metric.is_measured is False
        assert metric.measured_value() is None  # NOT reported as a measured 0
    assert record.latency_ms == 4  # latency IS the one genuine measurement


def test_measured_status_is_reachable_for_a_non_placeholder_field() -> None:
    """MEASURED stays reachable: an unknown-cohort producer whose field the pinned
    inventory does not classify as always-zero yields a genuine measured value."""
    row = _exact_eight(tokens_in=120)
    row["extra"] = 1  # 9 fields -> unknown cohort; no verified always-zero knowledge
    record = normalize_row(row, _prov())
    assert record.producer_schema == PRODUCER_SCHEMA_UNKNOWN
    assert record.tokens_in.status is MetricStatus.MEASURED
    assert record.tokens_in.measured_value() == 120


def test_absent_metric_field_is_unavailable_not_defaulted() -> None:
    row = _exact_eight(verdict="pass")
    del row["tokens_in"]  # field genuinely absent (unknown cohort)
    record = normalize_row(row, _prov())
    assert record.tokens_in.status is MetricStatus.UNAVAILABLE
    assert record.tokens_in.raw_value is None  # never coerced to 0


def test_normalize_preserves_provenance() -> None:
    prov = Provenance(source_relpath="invocations.jsonl", line_number=17, content_hash="abc")
    record = normalize_row(_exact_eight(), prov)
    assert record.provenance.ref == "invocations.jsonl@17"
    assert record.identity == ("invocations.jsonl@17", "abc")
