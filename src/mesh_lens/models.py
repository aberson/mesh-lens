"""Versioned normalized record shapes for mesh-lens (plan sec. 5, sec. 6).

Every normalized record carries an integer ``schema_version`` (currently ``1``),
its source ``Provenance`` (``<source-relpath>@<line-number>`` plus a SHA-256
``content_hash`` of the raw source line), and -- for the metrics the producer may
leave unmeasured -- a tri-state :class:`Metric` that keeps three states honestly
distinct (measurement-validity discipline, plan sec. 6):

  * ``MEASURED``     -- a genuine provider-reported value.
  * ``PLACEHOLDER``  -- the producer structurally does not measure this field (Step
                        1 verified it is hardcoded to 0 on every write path -
                        pass/fail/stub); the raw ``0`` is a placeholder, NOT a
                        measurement. Producer-knowledge-driven, not verdict-driven.
  * ``UNAVAILABLE``  -- the field was absent from the raw record; never coerced to
                        a default.

A placeholder-zero (``PLACEHOLDER``, ``0``) therefore never collapses into a
measured zero (``MEASURED``, ``0``) or into a missing value (``UNAVAILABLE``,
``None``); :meth:`Metric.measured_value` returns a number ONLY when ``MEASURED``.

Readers TOLERATE an older ``schema_version`` and REFUSE a newer one with an
explicit :class:`SchemaVersionError` (plan sec. 6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

#: The normalized-store schema version this build reads and writes.
CURRENT_SCHEMA_VERSION = 1


class SchemaVersionError(ValueError):
    """Raised when a stored record/checkpoint is NEWER than this build understands."""


def check_schema_version(version: int) -> int:
    """Tolerate an older/equal ``schema_version``; refuse a newer one (plan sec. 6)."""
    if version > CURRENT_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"record schema_version {version} is newer than supported "
            f"{CURRENT_SCHEMA_VERSION}; refusing to read (plan sec. 6)"
        )
    return version


class MetricStatus(StrEnum):
    """Honest measurement state of a numeric metric (plan sec. 6)."""

    MEASURED = "measured"  # a genuine provider-reported value
    PLACEHOLDER = "placeholder"  # producer never measures this field; raw 0 is a placeholder
    UNAVAILABLE = "unavailable"  # field absent from the raw record


@dataclass(frozen=True)
class Metric:
    """A numeric metric plus its honest measurement status.

    ``raw_value`` is the value exactly as parsed from the source (or ``None`` when
    the field was absent). It is a MEASUREMENT only when ``status is MEASURED``;
    consumers that aggregate real numbers must filter on :meth:`is_measured`.
    """

    status: MetricStatus
    raw_value: int | float | None

    @property
    def is_measured(self) -> bool:
        return self.status is MetricStatus.MEASURED

    def measured_value(self) -> int | float | None:
        """The value ONLY if it is a genuine measurement; otherwise ``None``."""
        return self.raw_value if self.status is MetricStatus.MEASURED else None

    def to_json(self) -> dict[str, Any]:
        return {"status": self.status.value, "raw_value": self.raw_value}

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> Metric:
        return cls(status=MetricStatus(obj["status"]), raw_value=obj["raw_value"])


@dataclass(frozen=True)
class Provenance:
    """Where a normalized record came from -- its stable identity (plan sec. 6)."""

    source_relpath: str  # e.g. "invocations.jsonl"
    line_number: int  # 1-based line number in the source
    content_hash: str  # SHA-256 hex of the RAW source line (EOL stripped)

    @property
    def ref(self) -> str:
        """The ``<source-relpath>@<line-number>`` provenance ref (e.g. ``invocations.jsonl@17``)."""
        return f"{self.source_relpath}@{self.line_number}"

    def to_json(self) -> dict[str, Any]:
        return {
            "source_relpath": self.source_relpath,
            "line_number": self.line_number,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> Provenance:
        return cls(
            source_relpath=obj["source_relpath"],
            line_number=obj["line_number"],
            content_hash=obj["content_hash"],
        )


@dataclass(frozen=True)
class NormalizedInvocation:
    """One normalized Skill Mesh dispatch record.

    ``producer_schema`` is ``"skillmesh-v1"`` ONLY when the raw record's field set
    exactly matches the pinned eight-field contract; any other set is ``"unknown"``
    and is never merged with ``skillmesh-v1`` (plan sec. 6). Fields that may be
    absent from a non-conforming record are ``None`` (never defaulted).
    """

    schema_version: int
    provenance: Provenance
    producer_schema: str
    timestamp: str | None
    skill: str | None
    model: str | None
    latency_ms: int | None
    verdict: str | None
    tokens_in: Metric
    tokens_out: Metric
    cost_usd: Metric
    raw_field_names: tuple[str, ...]

    @property
    def identity(self) -> tuple[str, str]:
        """Record identity = provenance ref + content_hash (plan sec. 6)."""
        return (self.provenance.ref, self.provenance.content_hash)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "provenance": self.provenance.to_json(),
            "producer_schema": self.producer_schema,
            "timestamp": self.timestamp,
            "skill": self.skill,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "verdict": self.verdict,
            "tokens_in": self.tokens_in.to_json(),
            "tokens_out": self.tokens_out.to_json(),
            "cost_usd": self.cost_usd.to_json(),
            "raw_field_names": list(self.raw_field_names),
        }

    @classmethod
    def from_json_dict(cls, obj: dict[str, Any]) -> NormalizedInvocation:
        version = check_schema_version(int(obj["schema_version"]))
        return cls(
            schema_version=version,
            provenance=Provenance.from_json(obj["provenance"]),
            producer_schema=obj["producer_schema"],
            timestamp=obj["timestamp"],
            skill=obj["skill"],
            model=obj["model"],
            latency_ms=obj["latency_ms"],
            verdict=obj["verdict"],
            tokens_in=Metric.from_json(obj["tokens_in"]),
            tokens_out=Metric.from_json(obj["tokens_out"]),
            cost_usd=Metric.from_json(obj["cost_usd"]),
            raw_field_names=tuple(obj["raw_field_names"]),
        )


@dataclass(frozen=True)
class NormalizedOutcome:
    """Versioned normalized outcome record SHAPE (plan sec. 5).

    Step 2 defines the shape only. Outcome ADAPTERS and dispatch<->outcome
    correlation are Step 3, so ``join_key`` is set by Step 3 ONLY when a strong
    dispatch-correlatable key exists (plan sec. 6); it stays ``None`` here,
    encoding the inventory finding that today's artifacts carry no strong key and
    stay unjoined. No outcome value is invented.
    """

    schema_version: int
    provenance: Provenance
    outcome_class: str  # one of the five candidate classes (plan sec. 2)
    fields: tuple[tuple[str, str], ...]  # normalized string fields; no invented values
    join_key: str | None = field(default=None)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "provenance": self.provenance.to_json(),
            "outcome_class": self.outcome_class,
            "fields": dict(self.fields),
            "join_key": self.join_key,
        }

    @classmethod
    def from_json_dict(cls, obj: dict[str, Any]) -> NormalizedOutcome:
        version = check_schema_version(int(obj["schema_version"]))
        raw_fields: dict[str, str] = obj["fields"]
        return cls(
            schema_version=version,
            provenance=Provenance.from_json(obj["provenance"]),
            outcome_class=obj["outcome_class"],
            fields=tuple(sorted(raw_fields.items())),
            join_key=obj["join_key"],
        )
