"""Aggregate dispatch telemetry into comparable cohorts (plan sec. 6, sec. 7 Step 4).

This is a MEASUREMENT module: every number it emits is honest or it is not emitted.
Three disciplines are load-bearing here:

  * **Aggregate a metric ONLY over its MEASURED values.** ``tokens_in`` /
    ``tokens_out`` / ``cost_usd`` are ``PLACEHOLDER`` for every ``skillmesh-v1``
    record (the producer hardcodes them to 0 - Step 1 inventory, single source of
    truth :data:`mesh_lens.inventory.ALWAYS_ZERO_PRODUCER_FIELDS`). A placeholder 0
    is NEVER summed or averaged: the aggregate reports ``status="unavailable"`` with
    ``sum``/``mean``/``min``/``max`` all ``None`` and the count of measured values
    (0 of N). Only ``latency_ms`` carries real signal today, so it is the one metric
    with a genuine measured aggregate. A metric aggregate never invents a 0.0 mean.

  * **Every displayed number resolves to its source record IDs.** A cohort carries
    the provenance refs (``<source-relpath>@<line>``) of every contributing record;
    each :class:`MetricAggregate` additionally carries the refs of exactly the
    records whose MEASURED value fed the sum/mean; each verdict count carries its
    refs. A number with no traceable source ref is a defect (plan sec. 7 done-when).

  * **The ``unknown`` cohort is never merged.** ``producer_schema`` is part of the
    cohort key, so an ``unknown``-schema record can never share a cohort with a
    ``skillmesh-v1`` record. Comparable (``skillmesh-v1``) and incomparable
    (``unknown``) cohorts are reported in separate buckets (plan sec. 6).

Outcomes and retries are NOT attached to dispatches: Step-3 correlation is
all-unjoined on real data (plan sec. 2/6), so the report states outcomes are
UNJOINED and computes no outcome or retry rate (see :class:`OutcomeSummary`).
Ordering is stable end-to-end so a golden report is byte-identical run to run.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from mesh_lens.correlate import CorrelationResult
from mesh_lens.inventory import (
    ALWAYS_ZERO_PRODUCER_FIELDS,
    PRODUCER_SCHEMA_ID,
    Availability,
    build_inventory,
)
from mesh_lens.models import (
    CURRENT_SCHEMA_VERSION,
    Metric,
    MetricStatus,
    NormalizedInvocation,
)

#: Label used when a record's ``verdict`` field is absent (never a fabricated value).
MISSING_VERDICT = "(unavailable)"


# --------------------------------------------------------------------------- #
# Cohort identity -- producer_schema is part of the key, so unknown never merges.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CohortKey:
    """The comparable-cohort identity (plan sec. 6): stratify by these dimensions.

    ``project`` and ``task_type`` are ``None`` today because the inventory classifies
    them ABSENT (not in the eight-field contract, not derivable); they are carried
    honestly as ``None`` rather than fabricated. ``producer_schema`` is part of the
    key so a ``skillmesh-v1`` cohort can never absorb an ``unknown`` record.
    """

    skill: str | None
    model: str | None
    project: str | None
    task_type: str | None
    producer_schema: str
    schema_version: int

    @property
    def is_comparable(self) -> bool:
        """True only for the pinned ``skillmesh-v1`` schema (plan sec. 6)."""
        return self.producer_schema == PRODUCER_SCHEMA_ID

    @property
    def sort_key(self) -> tuple[str, str, str, str, str, int]:
        """Deterministic ordering key (``None`` sorts as empty string)."""
        return (
            self.producer_schema,
            self.skill or "",
            self.model or "",
            self.project or "",
            self.task_type or "",
            self.schema_version,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "model": self.model,
            "project": self.project,
            "task_type": self.task_type,
            "producer_schema": self.producer_schema,
            "schema_version": self.schema_version,
            "comparable": self.is_comparable,
        }


def _key_for(inv: NormalizedInvocation) -> CohortKey:
    return CohortKey(
        skill=inv.skill,
        model=inv.model,
        project=None,  # ABSENT per inventory; never fabricated
        task_type=None,  # ABSENT per inventory; never fabricated
        producer_schema=inv.producer_schema,
        schema_version=inv.schema_version,
    )


# --------------------------------------------------------------------------- #
# Numeric aggregate -- MEASURED-only, with per-aggregate provenance refs.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Sample:
    status: MetricStatus
    value: int | float | None
    ref: str


@dataclass(frozen=True)
class MetricAggregate:
    """An honest numeric aggregate: a real number ONLY over MEASURED values.

    ``sum``/``mean``/``min``/``max`` are ``None`` whenever ``measured_count == 0`` --
    a placeholder or absent field NEVER yields a fabricated 0.0. ``measured_refs``
    lists exactly the record refs whose measured value fed the aggregate, so every
    displayed number traces to its sources (plan sec. 7 done-when).
    """

    field: str
    total: int  # records considered = measured + placeholder + unavailable
    measured_count: int
    placeholder_count: int
    unavailable_count: int
    sum: int | float | None
    mean: float | None
    minimum: int | float | None
    maximum: int | float | None
    measured_refs: tuple[str, ...]

    @property
    def status(self) -> str:
        """``"measured"`` iff at least one genuine value was aggregated."""
        return "measured" if self.measured_count > 0 else "unavailable"

    def summary(self) -> str:
        """One honest line describing the aggregate (measured stats, or why not)."""
        if self.measured_count > 0:
            return (
                f"measured over {self.measured_count} of {self.total}: "
                f"sum={self.sum}, mean={self.mean}, min={self.minimum}, max={self.maximum}"
            )
        return (
            f"unavailable -- not measured (0 of {self.total}; "
            f"{self.placeholder_count} placeholder, {self.unavailable_count} absent)"
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "status": self.status,
            "total": self.total,
            "measured_count": self.measured_count,
            "placeholder_count": self.placeholder_count,
            "unavailable_count": self.unavailable_count,
            "sum": self.sum,
            "mean": self.mean,
            "min": self.minimum,
            "max": self.maximum,
            "measured_refs": list(self.measured_refs),
        }


def _aggregate(field: str, samples: Sequence[_Sample]) -> MetricAggregate:
    """Aggregate a numeric field over its MEASURED values only.

    Measured values are summed in ref-sorted order so the result is deterministic.
    Placeholder and absent values are counted (for missingness) but NEVER summed.
    """
    measured = sorted(
        (
            (s.value, s.ref)
            for s in samples
            if s.status is MetricStatus.MEASURED and s.value is not None
        ),
        key=lambda pair: pair[1],
    )
    placeholder_count = sum(1 for s in samples if s.status is MetricStatus.PLACEHOLDER)
    unavailable_count = len(samples) - len(measured) - placeholder_count

    if not measured:
        return MetricAggregate(
            field=field,
            total=len(samples),
            measured_count=0,
            placeholder_count=placeholder_count,
            unavailable_count=unavailable_count,
            sum=None,
            mean=None,
            minimum=None,
            maximum=None,
            measured_refs=(),
        )

    values = [value for value, _ in measured]
    total_sum: int | float = sum(values)
    n = len(values)
    return MetricAggregate(
        field=field,
        total=len(samples),
        measured_count=n,
        placeholder_count=placeholder_count,
        unavailable_count=unavailable_count,
        sum=total_sum,
        mean=total_sum / n,
        minimum=min(values),
        maximum=max(values),
        measured_refs=tuple(ref for _, ref in measured),
    )


def _latency_samples(records: Sequence[NormalizedInvocation]) -> list[_Sample]:
    # latency_ms is a plain int: MEASURED when present, UNAVAILABLE (None) when absent.
    # It is never a placeholder (Step 1 inventory: real signal even in stub mode).
    return [
        _Sample(
            status=MetricStatus.MEASURED if r.latency_ms is not None else MetricStatus.UNAVAILABLE,
            value=r.latency_ms,
            ref=r.provenance.ref,
        )
        for r in records
    ]


def _metric_samples(records: Sequence[NormalizedInvocation], attr: str) -> list[_Sample]:
    samples: list[_Sample] = []
    for r in records:
        metric: Metric = getattr(r, attr)
        samples.append(_Sample(status=metric.status, value=metric.raw_value, ref=r.provenance.ref))
    return samples


# --------------------------------------------------------------------------- #
# Verdict breakdown -- real counts of a real field, each traceable to its refs.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VerdictBreakdown:
    """pass/fail/stub (and any other) counts, each carrying its source refs."""

    entries: tuple[tuple[str, tuple[str, ...]], ...]  # sorted (verdict, refs)

    @property
    def total(self) -> int:
        return sum(len(refs) for _, refs in self.entries)

    def count(self, verdict: str) -> int:
        for name, refs in self.entries:
            if name == verdict:
                return len(refs)
        return 0

    def to_json(self) -> dict[str, Any]:
        return {
            "counts": {verdict: len(refs) for verdict, refs in self.entries},
            "refs": {verdict: list(refs) for verdict, refs in self.entries},
        }


def _verdict_breakdown(records: Sequence[NormalizedInvocation]) -> VerdictBreakdown:
    by_verdict: dict[str, list[str]] = {}
    for r in records:
        label = r.verdict if r.verdict is not None else MISSING_VERDICT
        by_verdict.setdefault(label, []).append(r.provenance.ref)
    entries = tuple((verdict, tuple(sorted(refs))) for verdict, refs in sorted(by_verdict.items()))
    return VerdictBreakdown(entries=entries)


# --------------------------------------------------------------------------- #
# Cohort -- one comparable group with all its honest aggregates.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Cohort:
    """One comparable cohort's aggregates. Every metric traces to ``record_refs``."""

    key: CohortKey
    count: int
    record_refs: tuple[str, ...]
    latency: MetricAggregate
    tokens_in: MetricAggregate
    tokens_out: MetricAggregate
    cost_usd: MetricAggregate
    verdicts: VerdictBreakdown

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key.to_json(),
            "count": self.count,
            "record_refs": list(self.record_refs),
            "latency_ms": self.latency.to_json(),
            "tokens_in": self.tokens_in.to_json(),
            "tokens_out": self.tokens_out.to_json(),
            "cost_usd": self.cost_usd.to_json(),
            "verdicts": self.verdicts.to_json(),
        }


def _cohort(key: CohortKey, records: Sequence[NormalizedInvocation]) -> Cohort:
    return Cohort(
        key=key,
        count=len(records),
        record_refs=tuple(sorted(r.provenance.ref for r in records)),
        latency=_aggregate("latency_ms", _latency_samples(records)),
        tokens_in=_aggregate("tokens_in", _metric_samples(records, "tokens_in")),
        tokens_out=_aggregate("tokens_out", _metric_samples(records, "tokens_out")),
        cost_usd=_aggregate("cost_usd", _metric_samples(records, "cost_usd")),
        verdicts=_verdict_breakdown(records),
    )


# --------------------------------------------------------------------------- #
# Outcome summary -- outcomes are reported UNJOINED, never attached to a dispatch.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OutcomeSummary:
    """The report's honest outcome/retry stance: unjoined, so no rate is computed."""

    all_unjoined: bool
    total_outcome_records: int
    joined_count: int
    per_class: tuple[tuple[str, str, int, int], ...]  # (class, join_status, outcomes, joined)
    note: str

    def to_json(self) -> dict[str, Any]:
        return {
            "all_unjoined": self.all_unjoined,
            "total_outcome_records": self.total_outcome_records,
            "joined_count": self.joined_count,
            "retries": "unavailable -- no retry field joins to a dispatch (outcomes unjoined)",
            "per_class": [
                {
                    "outcome_class": cls,
                    "join_status": status,
                    "outcome_count": outcomes,
                    "joined_count": joined,
                }
                for cls, status, outcomes, joined in self.per_class
            ],
            "note": self.note,
        }


def _outcome_summary(correlation: CorrelationResult | None) -> OutcomeSummary:
    if correlation is None:
        return OutcomeSummary(
            all_unjoined=True,
            total_outcome_records=0,
            joined_count=0,
            per_class=(),
            note=(
                "No outcome correlation supplied; outcomes are UNJOINED and not attached to "
                "any dispatch (plan sec. 6). No outcome or retry rate is computed."
            ),
        )
    per_class = tuple(
        sorted(
            (d.outcome_class, d.join_status.value, d.outcome_count, d.joined_count)
            for d in correlation.diagnostics
        )
    )
    total = sum(d.outcome_count for d in correlation.diagnostics)
    joined = len(correlation.joined)
    if correlation.all_unjoined:
        note = (
            "Outcomes are UNJOINED: Step-3 correlation attaches no outcome to any dispatch "
            "under the current id-less producer (plan sec. 6). No outcome or retry rate is "
            "computed; unjoined outcome records are reported by class only."
        )
    else:
        note = (
            f"{joined} outcome record(s) joined on a provable run/session key (both provenances "
            "preserved); every unjoined class is reported separately, never attached to a dispatch."
        )
    return OutcomeSummary(
        all_unjoined=correlation.all_unjoined,
        total_outcome_records=total,
        joined_count=joined,
        per_class=per_class,
        note=note,
    )


# --------------------------------------------------------------------------- #
# Report -- the full single-view aggregation (Step 4; Step 5 adds compare).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Report:
    """The Step-4 aggregate report: comparable + incomparable cohorts + honesty facts."""

    schema_version: int
    total_events: int
    comparable_event_count: int
    incomparable_event_count: int
    comparable_cohorts: tuple[Cohort, ...]
    incomparable_cohorts: tuple[Cohort, ...]
    absent_dimensions: tuple[str, ...]
    placeholder_fields: tuple[str, ...]
    outcomes: OutcomeSummary

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "report": "mesh-lens aggregate report (single-view; plan sec. 7 Step 4)",
            "total_events": self.total_events,
            "comparable_event_count": self.comparable_event_count,
            "incomparable_event_count": self.incomparable_event_count,
            "comparable_cohorts": [c.to_json() for c in self.comparable_cohorts],
            "incomparable_cohorts": [c.to_json() for c in self.incomparable_cohorts],
            "absent_dimensions": list(self.absent_dimensions),
            "placeholder_fields": list(self.placeholder_fields),
            "outcomes": self.outcomes.to_json(),
            "notes": {
                "placeholder_fields": (
                    "These producer fields are hardcoded to 0 on every write path (Step 1 "
                    "inventory) and are NEVER measured for skillmesh-v1; their aggregates report "
                    "'unavailable', never a fabricated 0."
                ),
                "absent_dimensions": (
                    "These cohort dimensions are absent from the producer and not derivable, so "
                    "cohorts cannot stratify by them (plan sec. 6)."
                ),
                "incomparable": (
                    "unknown-producer-schema records are reported in their own bucket and are "
                    "never merged into a skillmesh-v1 aggregate (plan sec. 6)."
                ),
            },
        }


def analyze_report(
    events: Sequence[NormalizedInvocation],
    correlation: CorrelationResult | None = None,
) -> Report:
    """Aggregate ``events`` into comparable + incomparable cohorts (plan sec. 7 Step 4).

    Cohorts are keyed by (skill, model, project, task_type, producer_schema,
    schema_version); ``producer_schema`` in the key keeps the ``unknown`` cohort out
    of every ``skillmesh-v1`` aggregate. ``correlation`` (Step-3 output) drives the
    honest outcome section; when omitted the report states outcomes are unjoined.
    """
    inventory = build_inventory()

    groups: dict[CohortKey, list[NormalizedInvocation]] = {}
    for event in events:
        groups.setdefault(_key_for(event), []).append(event)

    cohorts = [_cohort(key, records) for key, records in groups.items()]
    comparable = tuple(
        sorted((c for c in cohorts if c.key.is_comparable), key=lambda c: c.key.sort_key)
    )
    incomparable = tuple(
        sorted((c for c in cohorts if not c.key.is_comparable), key=lambda c: c.key.sort_key)
    )

    absent_dimensions = tuple(
        d.name for d in inventory.cohort_dimensions if d.availability is Availability.ABSENT
    )

    return Report(
        schema_version=CURRENT_SCHEMA_VERSION,
        total_events=len(events),
        comparable_event_count=sum(c.count for c in comparable),
        incomparable_event_count=sum(c.count for c in incomparable),
        comparable_cohorts=comparable,
        incomparable_cohorts=incomparable,
        absent_dimensions=absent_dimensions,
        placeholder_fields=tuple(sorted(ALWAYS_ZERO_PRODUCER_FIELDS)),
        outcomes=_outcome_summary(correlation),
    )
