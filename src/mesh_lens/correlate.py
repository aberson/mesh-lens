"""Dispatch<->outcome correlation + diagnostics (plan sec. 7, Step 3).

This is the measurement-critical module: its cardinal sin is a BAD JOIN. It
attempts a dispatch<->outcome join ONLY on a PROVABLE stable run/session key and
classifies every candidate join, per the Step 1 inventory (single source of truth,
plan sec. 6), as:

  * ``JOINED``              -- a strong-keyed class whose outcome ``run_key`` matches
                              a dispatch ``run_key``; BOTH provenances are preserved.
  * ``UNJOINED_AMBIGUOUS``  -- only a skill-name- or timestamp-window relationship
                              exists; left unjoined (a lossy window is NEVER a join).
  * ``UNJOINED_ABSENT``     -- no shared dispatch-correlatable key exists at all.

The structural no-bad-join guarantee: a JOIN is emitted ONLY when (a) the inventory
classifies the outcome class ``STRONG_KEY`` AND (b) a non-``None`` ``run_key`` is
present on BOTH sides and matches. Timestamp and skill-name relationships never set
a ``run_key`` (the adapters refuse to; see :func:`mesh_lens.adapters.outcomes._join_key_for`
and :func:`dispatch_ref`), so they can never produce a join -- they only ever
EXPLAIN why a class stays unjoined. Under today's inventory no class is
strong-keyed, so real data resolves to all-unjoined, exactly as plan sec. 2/6
predicts.

Missing outcomes are REPORTED, never inferred: a class with zero ingested records
still gets a diagnostic stating the count is 0.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from mesh_lens.inventory import (
    Inventory,
    JoinStrength,
    OutcomeArtifactAudit,
    build_inventory,
    outcome_audit_by_class,
)
from mesh_lens.models import NormalizedInvocation, NormalizedOutcome, Provenance

_RUN_KEY_CANDIDATE = "run/session/record id"


class JoinStatus(StrEnum):
    """The correlation verdict for one outcome class."""

    JOINED = "joined"
    UNJOINED_AMBIGUOUS = "unjoined-ambiguous"
    UNJOINED_ABSENT = "unjoined-absent"


@dataclass(frozen=True)
class DispatchRef:
    """Join-relevant projection of a dispatch: its run/session key + provenance.

    ``run_key`` is ``None`` under the current id-less producer (plan sec. 6); a
    non-``None`` key is only ever supplied by a future/synthetic run-keyed producer.
    """

    run_key: str | None
    provenance: Provenance


@dataclass(frozen=True)
class JoinedPair:
    """A PROVABLE correlation. Both source provenances are preserved (plan sec. 7)."""

    run_key: str
    outcome_class: str
    dispatch_provenance: Provenance
    outcome_provenance: Provenance


@dataclass(frozen=True)
class ClassDiagnostic:
    """Per-class correlation diagnostic: join status + count + the inventory-sourced reason."""

    outcome_class: str
    join_status: JoinStatus
    outcome_count: int
    joined_count: int
    reason: str


@dataclass(frozen=True)
class CorrelationResult:
    """The full Step 3 correlation output: provable joins + per-class diagnostics."""

    joined: tuple[JoinedPair, ...]
    diagnostics: tuple[ClassDiagnostic, ...]

    @property
    def all_unjoined(self) -> bool:
        """True iff NO provable join was made (the expected result on real data)."""
        return len(self.joined) == 0

    def diagnostic_for(self, outcome_class: str) -> ClassDiagnostic | None:
        for diag in self.diagnostics:
            if diag.outcome_class == outcome_class:
                return diag
        return None


def _dispatch_run_key_present(inventory: Inventory) -> bool:
    """Whether the inventory says a strong run/session key exists on a dispatch row.

    Read from the Step 1 correlation-key audit (single source): today the
    ``run/session/record id`` candidate is ``ABSENT`` / ``NONE``, so this is False
    and every real dispatch projects ``run_key=None``.
    """
    for key in inventory.correlation_keys:
        if key.name == _RUN_KEY_CANDIDATE:
            return key.join_strength is JoinStrength.STRONG_KEY
    return False


def dispatch_ref(inv: NormalizedInvocation, inventory: Inventory | None = None) -> DispatchRef:
    """Project a normalized dispatch record to its join-relevant fields.

    ``run_key`` is ``None`` because the pinned eight-field contract carries no
    run/session id field to read (plan sec. 6). If the inventory ever classifies a
    strong dispatch run key as present, this FAILS LOUD rather than silently
    projecting ``None`` (which would make every join silently miss) -- the extractor
    for the new field must be wired here alongside that inventory change.
    """
    inv_audit = inventory if inventory is not None else build_inventory()
    if _dispatch_run_key_present(inv_audit):
        raise NotImplementedError(
            "inventory reports a strong dispatch run/session key but no extractor is "
            "wired; add the field extraction in dispatch_ref (plan sec. 6)"
        )
    return DispatchRef(run_key=None, provenance=inv.provenance)


def keyed_dispatch_refs(text: str, source_relpath: str) -> list[DispatchRef]:
    """Build dispatch refs from a FUTURE/synthetic run-keyed dispatch stream.

    Each JSONL row is ``{"run_id": "...", ...}``. Clearly SEPARATE from
    :func:`dispatch_ref` (which projects today's id-less records to ``run_key=None``):
    this constructor exists to exercise the strong-key join PATH with a synthetic
    fixture that actually carries a stable key.
    """
    refs: list[DispatchRef] = []
    for index, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            continue
        row = cast("dict[str, Any]", obj)
        run_id = row.get("run_id")
        run_key = run_id if isinstance(run_id, str) else None
        refs.append(
            DispatchRef(
                run_key=run_key,
                provenance=Provenance(
                    source_relpath=source_relpath,
                    line_number=index,
                    content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                ),
            )
        )
    return refs


def _reason(audit: OutcomeArtifactAudit | None, joined_count: int, outcome_count: int) -> str:
    if joined_count > 0:
        return (
            f"{joined_count} record(s) joined on a provable run/session key; "
            "both source provenances preserved"
        )
    strength = audit.join_strength if audit is not None else JoinStrength.NONE
    if strength is JoinStrength.STRONG_KEY:
        base = "class is strong-keyed but no outcome run key matched a dispatch run key"
    elif strength in (JoinStrength.SKILL_NAME_ONLY, JoinStrength.TIMESTAMP_WINDOW_ONLY):
        base = (
            f"only a {strength.value} relationship exists (ambiguous); a lossy window "
            "is never emitted as a join (plan sec. 6)"
        )
    else:  # NONE
        base = "no shared dispatch-correlatable key exists (plan sec. 6)"
    if outcome_count == 0:
        base += "; 0 outcome records ingested (reported missing, never inferred)"
    return base


def _status(audit: OutcomeArtifactAudit | None, joined_count: int) -> JoinStatus:
    if joined_count > 0:
        return JoinStatus.JOINED
    strength = audit.join_strength if audit is not None else JoinStrength.NONE
    if strength in (JoinStrength.SKILL_NAME_ONLY, JoinStrength.TIMESTAMP_WINDOW_ONLY):
        return JoinStatus.UNJOINED_AMBIGUOUS
    return JoinStatus.UNJOINED_ABSENT


def correlate(
    dispatches: Sequence[DispatchRef],
    outcomes: Sequence[NormalizedOutcome],
    inventory: Inventory | None = None,
) -> CorrelationResult:
    """Correlate outcomes to dispatches on a PROVABLE key only; diagnose every class.

    Emits a join ONLY for an inventory-``STRONG_KEY`` class whose outcome ``join_key``
    matches a dispatch ``run_key``. All other relationships stay unjoined and are
    diagnosed with the inventory-sourced reason. Classes with zero ingested records
    are still diagnosed (missing reported, not inferred).
    """
    inv = inventory if inventory is not None else build_inventory()
    audits = outcome_audit_by_class(inv)

    # Index dispatch run keys; only a non-None key is ever joinable.
    by_run_key: dict[str, list[DispatchRef]] = {}
    for ref in dispatches:
        if ref.run_key is not None:
            by_run_key.setdefault(ref.run_key, []).append(ref)

    by_class: dict[str, list[NormalizedOutcome]] = {}
    for outcome in outcomes:
        by_class.setdefault(outcome.outcome_class, []).append(outcome)

    joined: list[JoinedPair] = []
    diagnostics: list[ClassDiagnostic] = []

    # Diagnose the union of known inventory classes and any classes actually ingested,
    # so a class with zero records still reports (missing, not inferred).
    all_classes: Iterable[str] = sorted(set(audits) | set(by_class))
    for outcome_class in all_classes:
        audit = audits.get(outcome_class)
        strong = audit is not None and audit.join_strength is JoinStrength.STRONG_KEY
        records = by_class.get(outcome_class, [])
        class_joined = 0
        for outcome in records:
            # No-bad-join guard: join ONLY when the class is strong-keyed AND a
            # run_key is present on both sides and matches.
            if strong and outcome.join_key is not None and outcome.join_key in by_run_key:
                for dref in by_run_key[outcome.join_key]:
                    joined.append(
                        JoinedPair(
                            run_key=outcome.join_key,
                            outcome_class=outcome_class,
                            dispatch_provenance=dref.provenance,
                            outcome_provenance=outcome.provenance,
                        )
                    )
                    class_joined += 1
        diagnostics.append(
            ClassDiagnostic(
                outcome_class=outcome_class,
                join_status=_status(audit, class_joined),
                outcome_count=len(records),
                joined_count=class_joined,
                reason=_reason(audit, class_joined, len(records)),
            )
        )

    return CorrelationResult(joined=tuple(joined), diagnostics=tuple(diagnostics))
