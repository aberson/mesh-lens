"""Guarded pairwise cohort comparison (plan sec. 6, sec. 7 Step 5, sec. 8).

This is the sharpest MEASUREMENT surface in mesh-lens: it is the one place that emits
a *directional* read ("cohort A has lower latency than B"). The cardinal discipline is
REFUSAL -- it never emits a directional verdict the evidence cannot support. Every
guard below turns an unsupportable comparison into an explicit, stated refusal with a
reason, never a fabricated winner.

Four guards, each of which independently REFUSES a directional verdict:

  * **Sample size** (:data:`MIN_DIRECTIONAL_N`). A cohort with fewer records than the
    documented floor cannot back a direction; the whole comparison refuses. The same
    floor applies per-metric to the *measured* count, so a large cohort whose metric is
    mostly missing still refuses on that metric. This is a REPORTING floor, not a
    significance test -- mesh-lens is correlation-only (plan sec. 6); no p-value is
    computed and no stats dependency is used.

  * **Incomparable schema.** ``producer_schema`` (and ``schema_version``) must match and
    must be the pinned ``skillmesh-v1``. Records are NEVER compared across
    ``producer_schema``, and an ``unknown`` cohort is never merged or compared (plan
    sec. 6). Either condition refuses the whole comparison.

  * **Confound.** A pairwise comparison must vary exactly ONE stratification dimension.
    If the two cohorts differ on two or more of (skill, model, project, task_type) the
    delta confounds them and the comparison refuses; if they differ on none there is no
    contrast to draw (plan sec. 8).

  * **Placeholder / unavailable metric.** ``tokens_in`` / ``tokens_out`` / ``cost_usd``
    are PLACEHOLDER for every ``skillmesh-v1`` record (Step-1 inventory): they are
    *unavailable*, not "equal" or "0". A metric that is not MEASURED in BOTH cohorts
    yields no verdict for that metric, stated as such -- never treated as 0.

Even a valid delta is presented CORRELATION-ONLY, with an explicit confound warning:
``project`` and ``task_type`` are ABSENT from the producer contract, so the two cohorts
may silently differ on them and an unobserved mix can fully explain any delta. A delta is
never phrased as "X causes better Y".

The pre-declared decision metric (measurement-validity "defined before looking", plan
sec. 6) is a required input and is stated up front; only it gates a decision. Any other
metric delta shown is exploratory disclosure, not a decision driver.

Output is deterministic: the same events + selectors + decision metric render a
byte-identical comparison (a golden pins it).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mesh_lens.analyze import Cohort, CohortKey, MetricAggregate, Report

#: The documented minimum cohort N below which NO directional verdict is emitted.
#: A reporting floor, not a significance threshold: below five paired observations a
#: direction is indistinguishable from noise. mesh-lens stays correlation-only (plan
#: sec. 6) -- this constant does not compute or imply statistical significance.
MIN_DIRECTIONAL_N = 5

#: Stratification dimensions a pairwise comparison may vary (producer_schema and
#: schema_version are handled by the incomparable-schema guard, not here).
_DIMENSIONS: tuple[str, ...] = ("skill", "model", "project", "task_type")

#: Decision-metric name -> the Cohort attribute holding its MEASURED-only aggregate.
_METRIC_ATTRS: dict[str, str] = {
    "latency_ms": "latency",
    "tokens_in": "tokens_in",
    "tokens_out": "tokens_out",
    "cost_usd": "cost_usd",
}

#: Stable display/serialization order for the per-metric disclosure table.
_METRIC_ORDER: tuple[str, ...] = ("latency_ms", "tokens_in", "tokens_out", "cost_usd")

#: Valid decision-metric names (the keys of :data:`_METRIC_ATTRS`, in stable order).
COMPARABLE_METRICS: tuple[str, ...] = _METRIC_ORDER


class CohortSelectionError(ValueError):
    """A selector matched zero or more than one cohort (cannot pin a cohort)."""


# --------------------------------------------------------------------------- #
# Selector -- pins ONE cohort by its stratification key.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CohortSelector:
    """Selects a cohort by its stratification key; a ``None`` field is a wildcard.

    ``producer_schema`` is part of the selector so an operator can explicitly pin the
    ``unknown`` cohort (to prove the incomparable-refusal path); left unset it is a
    wildcard, and an ambiguous match is a hard :class:`CohortSelectionError` rather than
    a silent pick.
    """

    skill: str | None = None
    model: str | None = None
    project: str | None = None
    task_type: str | None = None
    producer_schema: str | None = None

    def matches(self, key: CohortKey) -> bool:
        return (
            (self.skill is None or self.skill == key.skill)
            and (self.model is None or self.model == key.model)
            and (self.project is None or self.project == key.project)
            and (self.task_type is None or self.task_type == key.task_type)
            and (self.producer_schema is None or self.producer_schema == key.producer_schema)
        )

    def describe(self) -> str:
        parts = [
            f"{name}={value}"
            for name, value in (
                ("skill", self.skill),
                ("model", self.model),
                ("project", self.project),
                ("task_type", self.task_type),
                ("producer_schema", self.producer_schema),
            )
            if value is not None
        ]
        return ", ".join(parts) if parts else "(any cohort)"


_SELECTOR_FIELDS = frozenset({"skill", "model", "project", "task_type", "producer_schema"})


def parse_selector(spec: str) -> CohortSelector:
    """Parse ``"skill=repo-sync,model=claude"`` into a :class:`CohortSelector`.

    Keys must be stratification-key fields; an unknown key or a malformed pair raises
    ``ValueError`` so a typo never silently widens the selection.
    """
    fields: dict[str, str] = {}
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        if "=" not in token:
            raise ValueError(f"selector term {token!r} is not key=value")
        name, _, value = token.partition("=")
        name = name.strip()
        value = value.strip()
        if name not in _SELECTOR_FIELDS:
            raise ValueError(
                f"unknown selector key {name!r}; valid keys: {', '.join(sorted(_SELECTOR_FIELDS))}"
            )
        if not value:
            raise ValueError(f"selector key {name!r} has an empty value")
        fields[name] = value
    if not fields:
        raise ValueError("selector is empty; specify at least one key=value")
    return CohortSelector(**fields)


def _all_cohorts(report: Report) -> tuple[Cohort, ...]:
    return report.comparable_cohorts + report.incomparable_cohorts


def select_cohort(report: Report, selector: CohortSelector, label: str) -> Cohort:
    """Return the single cohort matching ``selector`` or raise :class:`CohortSelectionError`."""
    matches = [c for c in _all_cohorts(report) if selector.matches(c.key)]
    if not matches:
        raise CohortSelectionError(
            f"cohort {label} selector [{selector.describe()}] matched no cohort"
        )
    if len(matches) > 1:
        keys = "; ".join(f"({c.key.skill}/{c.key.model}/{c.key.producer_schema})" for c in matches)
        raise CohortSelectionError(
            f"cohort {label} selector [{selector.describe()}] is ambiguous "
            f"({len(matches)} cohorts: {keys}); add producer_schema or another key"
        )
    return matches[0]


def _differing_dimensions(a: CohortKey, b: CohortKey) -> tuple[str, ...]:
    return tuple(d for d in _DIMENSIONS if getattr(a, d) != getattr(b, d))


# --------------------------------------------------------------------------- #
# Per-metric comparison -- descriptive disclosure ALWAYS; a delta only when allowed.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MetricComparison:
    """One metric's comparison: honest disclosure, plus a delta ONLY when supported.

    ``delta``/``direction`` are populated ONLY when the metric is MEASURED in both
    cohorts with at least :data:`MIN_DIRECTIONAL_N` measured values each AND the overall
    comparison is not refused. Otherwise both are ``None`` and ``refusal_reasons`` states
    exactly why -- an unavailable (placeholder/absent) metric is NEVER a fabricated 0 or
    an "equal" verdict.
    """

    metric: str
    is_decision_metric: bool
    a_status: str
    a_measured_count: int
    a_total: int
    a_mean: float | None
    a_refs: tuple[str, ...]
    b_status: str
    b_measured_count: int
    b_total: int
    b_mean: float | None
    b_refs: tuple[str, ...]
    comparable: bool
    refusal_reasons: tuple[str, ...]
    delta: float | None
    direction: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "is_decision_metric": self.is_decision_metric,
            "comparable": self.comparable,
            "refusal_reasons": list(self.refusal_reasons),
            "delta_a_minus_b": self.delta,
            "direction": self.direction,
            "cohort_a": {
                "status": self.a_status,
                "measured_count": self.a_measured_count,
                "total": self.a_total,
                "mean": self.a_mean,
                "measured_refs": list(self.a_refs),
            },
            "cohort_b": {
                "status": self.b_status,
                "measured_count": self.b_measured_count,
                "total": self.b_total,
                "mean": self.b_mean,
                "measured_refs": list(self.b_refs),
            },
        }


def _phrase_direction(metric: str, a_mean: float, b_mean: float, delta: float) -> str:
    if delta == 0:
        return (
            f"no measured difference in {metric}: cohort A mean == cohort B mean == "
            f"{a_mean} (observed, correlation only)"
        )
    lower = "A" if a_mean < b_mean else "B"
    return (
        f"cohort {lower} has the lower measured {metric} "
        f"(A mean={a_mean}, B mean={b_mean}, delta A-B={delta}); "
        "this is an OBSERVED correlation, never evidence of causation"
    )


def _compare_metric(
    metric: str,
    is_decision: bool,
    a: Cohort,
    b: Cohort,
    overall_comparable: bool,
    min_n: int,
) -> MetricComparison:
    agg_a: MetricAggregate = getattr(a, _METRIC_ATTRS[metric])
    agg_b: MetricAggregate = getattr(b, _METRIC_ATTRS[metric])

    reasons: list[str] = []
    if not overall_comparable:
        reasons.append(
            "comparison refused at the cohort level (see refusals); no per-metric verdict"
        )
    for label, agg in (("A", agg_a), ("B", agg_b)):
        if agg.status != "measured":
            reasons.append(
                f"{metric} is UNAVAILABLE in cohort {label} "
                f"({agg.placeholder_count} placeholder, {agg.unavailable_count} absent of "
                f"{agg.total}); an unavailable metric is never a directional verdict "
                "(never read as 0 or 'equal')"
            )
        elif agg.measured_count < min_n:
            reasons.append(
                f"{metric} is measured in only {agg.measured_count} of {agg.total} cohort-{label} "
                f"records (< threshold N={min_n}); too few measured values for a direction"
            )

    a_mean = agg_a.mean
    b_mean = agg_b.mean
    comparable = not reasons
    delta: float | None = None
    direction: str | None = None
    if comparable and a_mean is not None and b_mean is not None:
        delta = float(a_mean) - float(b_mean)
        direction = _phrase_direction(metric, float(a_mean), float(b_mean), delta)

    return MetricComparison(
        metric=metric,
        is_decision_metric=is_decision,
        a_status=agg_a.status,
        a_measured_count=agg_a.measured_count,
        a_total=agg_a.total,
        a_mean=agg_a.mean,
        a_refs=agg_a.measured_refs,
        b_status=agg_b.status,
        b_measured_count=agg_b.measured_count,
        b_total=agg_b.total,
        b_mean=agg_b.mean,
        b_refs=agg_b.measured_refs,
        comparable=comparable,
        refusal_reasons=tuple(reasons),
        delta=delta,
        direction=direction,
    )


# --------------------------------------------------------------------------- #
# Comparison -- the full guarded pairwise result.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CohortFacts:
    """Disclosed identity + sample facts of one compared cohort (missingness lives in
    each :class:`MetricComparison`)."""

    key: CohortKey
    count: int
    record_refs: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key.to_json(),
            "count": self.count,
            "record_refs": list(self.record_refs),
        }


@dataclass(frozen=True)
class Comparison:
    """A guarded pairwise comparison. ``directional_verdict`` is ``None`` when refused.

    ``refusals`` holds every cohort-level reason the comparison declined a directional
    verdict (incomparable schema, unknown cohort, confound, undersized). When it is
    non-empty NO metric carries a delta. ``caveats`` always states the pre-declared
    decision metric and, for a valid delta, the correlation-not-causation + confound
    warnings.
    """

    schema_version: int
    decision_metric: str
    min_n: int
    cohort_a: CohortFacts
    cohort_b: CohortFacts
    comparable: bool
    refusals: tuple[str, ...]
    contrast_dimension: str | None
    held_constant: tuple[str, ...]
    absent_confounds: tuple[str, ...]
    metrics: tuple[MetricComparison, ...]
    directional_verdict: str | None
    caveats: tuple[str, ...]

    @property
    def refused(self) -> bool:
        """True iff no directional verdict was emitted for the decision metric."""
        return self.directional_verdict is None

    def decision(self) -> MetricComparison:
        """The :class:`MetricComparison` for the pre-declared decision metric."""
        return next(m for m in self.metrics if m.metric == self.decision_metric)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "report": "mesh-lens guarded pairwise comparison (plan sec. 7 Step 5)",
            "decision_metric": self.decision_metric,
            "min_directional_n": self.min_n,
            "comparable": self.comparable,
            "refused": self.refused,
            "refusals": list(self.refusals),
            "directional_verdict": self.directional_verdict,
            "contrast_dimension": self.contrast_dimension,
            "held_constant": list(self.held_constant),
            "absent_confounds": list(self.absent_confounds),
            "cohort_a": self.cohort_a.to_json(),
            "cohort_b": self.cohort_b.to_json(),
            "metrics": [m.to_json() for m in self.metrics],
            "caveats": list(self.caveats),
            "notes": {
                "refusal": (
                    "A refused comparison computes NO winner. Undersized, incomparable "
                    "(cross-schema/unknown), or confounded cohorts never yield a directional "
                    "verdict (plan sec. 6, sec. 8)."
                ),
                "placeholder": (
                    "tokens_in/tokens_out/cost_usd are placeholder for skillmesh-v1 and are "
                    "reported UNAVAILABLE, never 0 or 'equal'; only a metric measured in BOTH "
                    "cohorts can carry a delta."
                ),
                "correlation_only": (
                    "Any delta is an OBSERVED correlation, never causation; project/task_type "
                    "are absent from the producer so an unobserved mix may confound it."
                ),
            },
        }


def _build_caveats(
    comparable: bool,
    decision_metric: str,
    contrast_dimension: str | None,
    held_constant: tuple[str, ...],
    absent_confounds: tuple[str, ...],
) -> tuple[str, ...]:
    caveats = [
        f"Pre-declared decision metric (defined before looking, plan sec. 6): {decision_metric}. "
        "Only this metric gates a decision."
    ]
    if not comparable:
        caveats.append(
            "This comparison REFUSES a directional verdict (see refusals); no winner is computed."
        )
        return tuple(caveats)
    caveats.append(
        f"CORRELATION, NOT CAUSATION: any delta is an observed association between the "
        f"'{contrast_dimension}' stratification and the metric, never evidence that "
        f"'{contrast_dimension}' CAUSES it."
    )
    if held_constant:
        caveats.append(f"Held constant across both cohorts: {', '.join(held_constant)}.")
    if absent_confounds:
        caveats.append(
            "UNOBSERVED CONFOUNDS: "
            + ", ".join(absent_confounds)
            + " are ABSENT from the producer contract, so the two cohorts may differ on them; "
            "an unobserved project/task mix can fully explain any delta (plan sec. 8)."
        )
    caveats.append(
        "Any metric delta other than the decision metric is exploratory disclosure, not a "
        "decision driver."
    )
    return tuple(caveats)


def compare_cohorts(
    report: Report,
    selector_a: CohortSelector,
    selector_b: CohortSelector,
    decision_metric: str = "latency_ms",
    min_n: int = MIN_DIRECTIONAL_N,
) -> Comparison:
    """Compare two pinned cohorts under the four guards (plan sec. 7 Step 5).

    ``decision_metric`` is the pre-declared metric that gates the decision (measurement
    validity "defined before looking", plan sec. 6). The comparison REFUSES a directional
    verdict -- ``directional_verdict is None`` -- whenever the cohorts are incomparable
    (cross-schema / unknown), differ on more than one stratification dimension (confound)
    or none (no contrast), or either falls below ``min_n``. Descriptive stats and
    missingness are disclosed either way.
    """
    if decision_metric not in _METRIC_ATTRS:
        raise ValueError(
            f"unknown decision metric {decision_metric!r}; valid: {', '.join(COMPARABLE_METRICS)}"
        )

    a = select_cohort(report, selector_a, "A")
    b = select_cohort(report, selector_b, "B")

    refusals: list[str] = []

    schema_differs = (
        a.key.producer_schema != b.key.producer_schema
        or a.key.schema_version != b.key.schema_version
    )
    if schema_differs:
        refusals.append(
            "incomparable: producer schema differs "
            f"(A={a.key.producer_schema} v{a.key.schema_version} vs "
            f"B={b.key.producer_schema} v{b.key.schema_version}); records are NEVER compared "
            "across producer_schema (plan sec. 6)"
        )
    if not a.key.is_comparable or not b.key.is_comparable:
        refusals.append(
            "incomparable: an 'unknown'-schema cohort is never merged or compared (plan sec. 6); "
            "only pinned skillmesh-v1 cohorts are comparable"
        )

    differing = _differing_dimensions(a.key, b.key)
    if not differing:
        refusals.append(
            "no contrast: the two cohorts differ on no stratification dimension (identical "
            "strata); there is nothing to compare"
        )
    elif len(differing) >= 2:
        refusals.append(
            f"incomparable: cohorts differ on multiple stratification dimensions "
            f"({', '.join(differing)}); the delta would confound them (plan sec. 8) -- hold all "
            "but one dimension constant to compare"
        )

    undersized = [f"{lbl}(N={c.count})" for lbl, c in (("A", a), ("B", b)) if c.count < min_n]
    if undersized:
        refusals.append(
            f"insufficient sample: {', '.join(undersized)} < threshold N={min_n}; no directional "
            "verdict (plan sec. 8). This floor is a reporting floor, not a significance test."
        )

    comparable = not refusals
    contrast_dimension = differing[0] if len(differing) == 1 else None
    held_constant = tuple(
        d
        for d in _DIMENSIONS
        if getattr(a.key, d) == getattr(b.key, d) and getattr(a.key, d) is not None
    )
    absent_confounds = tuple(
        d for d in _DIMENSIONS if getattr(a.key, d) is None and getattr(b.key, d) is None
    )

    metrics = tuple(
        _compare_metric(name, name == decision_metric, a, b, comparable, min_n)
        for name in _METRIC_ORDER
    )
    decision = next(m for m in metrics if m.metric == decision_metric)
    directional_verdict = decision.direction if decision.comparable else None

    caveats = _build_caveats(
        comparable, decision_metric, contrast_dimension, held_constant, absent_confounds
    )

    return Comparison(
        schema_version=report.schema_version,
        decision_metric=decision_metric,
        min_n=min_n,
        cohort_a=CohortFacts(a.key, a.count, a.record_refs),
        cohort_b=CohortFacts(b.key, b.count, b.record_refs),
        comparable=comparable,
        refusals=tuple(refusals),
        contrast_dimension=contrast_dimension,
        held_constant=held_constant,
        absent_confounds=absent_confounds,
        metrics=metrics,
        directional_verdict=directional_verdict,
        caveats=caveats,
    )
