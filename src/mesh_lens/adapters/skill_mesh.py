"""Skill Mesh dispatch-telemetry adapter (plan sec. 6).

Maps one raw JSONL dispatch row into a :class:`NormalizedInvocation`, WITHOUT
fabricating any unavailable value:

  * ``producer_schema`` is ``"skillmesh-v1"`` ONLY when the row's field set exactly
    matches the pinned eight-field contract; ANY other set (extra field, missing
    field, renamed field) lands in the ``"unknown"`` cohort, which is never merged
    with ``skillmesh-v1`` -- it is reported incomparable.
  * ``tokens_in`` / ``tokens_out`` / ``cost_usd`` become tri-state :class:`Metric`
    values. For a ``skillmesh-v1`` record they are ALWAYS ``PLACEHOLDER`` (the
    Step 1 inventory verified the producer hardcodes them to 0 on every write path
    - pass/fail/stub - so they are never a measurement, regardless of verdict).
    An absent field is ``UNAVAILABLE``. The "never measured" set comes from the
    inventory's ``ALWAYS_ZERO_PRODUCER_FIELDS`` (single source of truth), so when
    the producer one day starts measuring tokens, reclassifying it in the
    inventory flips these to ``MEASURED`` automatically.
  * ``latency_ms`` carries real signal even in stub mode (Step 1 inventory), so it
    is a plain measured integer when present, ``None`` when absent.

This adapter is pure: it reads a parsed row + a caller-supplied
:class:`Provenance` and returns a record. All file IO, byte offsets, hashing, and
checkpointing live in :mod:`mesh_lens.store`.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from mesh_lens.inventory import (
    ALWAYS_ZERO_PRODUCER_FIELDS,
    PINNED_PRODUCER_FIELDS,
    PRODUCER_SCHEMA_ID,
)
from mesh_lens.models import (
    CURRENT_SCHEMA_VERSION,
    Metric,
    MetricStatus,
    NormalizedInvocation,
    Provenance,
)

#: Cohort tag for any record whose field set is NOT the pinned eight (plan sec. 6).
PRODUCER_SCHEMA_UNKNOWN = "unknown"

_PINNED_FIELD_SET = frozenset(PINNED_PRODUCER_FIELDS)


def infer_producer_schema(field_names: Collection[str]) -> str:
    """Return ``skillmesh-v1`` iff ``field_names`` == the pinned eight, else ``unknown``."""
    return (
        PRODUCER_SCHEMA_ID
        if frozenset(field_names) == _PINNED_FIELD_SET
        else PRODUCER_SCHEMA_UNKNOWN
    )


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _int_or_none(value: Any) -> int | None:
    # bool is an int subclass; a boolean is never a valid latency measurement.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _metric(raw: Mapping[str, Any], field_name: str, producer_schema: str) -> Metric:
    if field_name not in raw:
        return Metric(MetricStatus.UNAVAILABLE, None)
    value = raw[field_name]
    if producer_schema == PRODUCER_SCHEMA_ID and field_name in ALWAYS_ZERO_PRODUCER_FIELDS:
        # Producer knowledge (Step 1, verified): this field is hardcoded to 0 on
        # every write path and is NEVER measured, whatever the verdict. The raw 0
        # is a placeholder, not a measured value -- so measured_value() stays None.
        return Metric(MetricStatus.PLACEHOLDER, value)
    return Metric(MetricStatus.MEASURED, value)


def normalize_row(raw: Mapping[str, Any], provenance: Provenance) -> NormalizedInvocation:
    """Normalize one parsed dispatch row into a versioned :class:`NormalizedInvocation`."""
    field_names = tuple(sorted(raw.keys()))
    producer_schema = infer_producer_schema(field_names)
    return NormalizedInvocation(
        schema_version=CURRENT_SCHEMA_VERSION,
        provenance=provenance,
        producer_schema=producer_schema,
        timestamp=_str_or_none(raw.get("timestamp")),
        skill=_str_or_none(raw.get("skill")),
        model=_str_or_none(raw.get("model")),
        latency_ms=_int_or_none(raw.get("latency_ms")),
        verdict=_str_or_none(raw.get("verdict")),
        tokens_in=_metric(raw, "tokens_in", producer_schema),
        tokens_out=_metric(raw, "tokens_out", producer_schema),
        cost_usd=_metric(raw, "cost_usd", producer_schema),
        raw_field_names=field_names,
    )
