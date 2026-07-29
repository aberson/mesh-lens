"""Availability audit for the Skill Mesh producer + candidate outcome artifacts.

This module is the honest, verified inventory that Step 1 of the mesh-lens plan
requires. Every classification here was verified against a *primary source*:

  * The producing code:
      - ``.claude/lib/telemetry/telemetry-writer.ps1`` (the record writer)
      - ``.claude/lib/skill-router.ps1`` (the dispatch code that calls the writer)
  * The real telemetry stream: ``.claude/lib/telemetry/invocations.jsonl``
    (frozen byte-identical at ``tests/fixtures/invocations.real.jsonl``).
  * The producer format contract:
      ``documentation/multi-model/telemetry-schema.md``.

Honesty rules (measurement-validity discipline):
  * A field/artifact that is absent or only timestamp-joinable is classified
    ABSENT / AMBIGUOUS. No derivation is invented to make the inventory look
    complete.
  * "Most outcome classes lack a dispatch-correlatable key and stay unjoined" is
    the expected, valid outcome (plan sec. 2) -- it is reported, not papered over.
  * This module only READS. It never modifies the Skill Mesh producer or the
    telemetry stream. V1 infers the producer schema (plan sec. 6).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# --------------------------------------------------------------------------- #
# Pinned producer contract (single source of truth for the data shape).
# Verified against telemetry-writer.ps1 lines 41-50 and both real records.
# --------------------------------------------------------------------------- #

#: The eight fields the current Skill Mesh producer writes, in producer order.
PINNED_PRODUCER_FIELDS: tuple[str, ...] = (
    "timestamp",
    "skill",
    "model",
    "tokens_in",
    "tokens_out",
    "latency_ms",
    "cost_usd",
    "verdict",
)

#: Cohort tag assigned by the Step 2 adapter when a record's field set exactly
#: matches ``PINNED_PRODUCER_FIELDS`` (plan sec. 6). Any other set -> "unknown".
PRODUCER_SCHEMA_ID = "skillmesh-v1"

#: Well-known path of the real stream, relative to the dev workspace root.
DEFAULT_TELEMETRY_RELPATH = ".claude/lib/telemetry/invocations.jsonl"


class Availability(StrEnum):
    """How available a proposed field/metric/artifact actually is."""

    PRESENT = "present"  # verified in the producer/artifact right now
    DERIVABLE = "derivable"  # not written, but computable from present data
    AMBIGUOUS = "ambiguous"  # only a lossy/non-unique key exists; stays caveated
    ABSENT = "absent"  # not present and not derivable from present data


class ValueSignal(StrEnum):
    """Whether a *present* field carries a real value today."""

    REAL = "real"  # field carries a genuine measured value
    ALWAYS_ZERO = "always-zero"  # field present but hardcoded/zeroed by producer
    NOT_APPLICABLE = "n/a"


class JoinStrength(StrEnum):
    """Strength of the key linking an artifact/candidate back to a dispatch row."""

    STRONG_KEY = "strong-key"  # a stable dispatch-correlatable id exists
    SKILL_NAME_ONLY = "skill-name-only"  # non-unique cohort key, not a row join
    TIMESTAMP_WINDOW_ONLY = "timestamp-window-only"  # lossy time-window join only
    NONE = "none"  # no shared key at all


class OutcomeClass(StrEnum):
    """Stable IDs for the five candidate outcome-artifact classes (plan sec. 2).

    ONE source of truth for the class names shared by the Step 3 outcome adapters
    and the correlation engine, so a class is spelled identically everywhere and a
    join decision is always looked up against its inventory audit (never
    re-derived).
    """

    BUILD_STEP_REPORT = "build-step-report"
    GH_ISSUE_STATE = "gh-issue-state"
    GIT_LOG = "git-log"
    PLAN_STATUS = "plan-status"
    SKILL_ITERATE_LOG = "skill-iterate-log"


@dataclass(frozen=True)
class FieldAudit:
    """One producer field or cohort dimension, classified with evidence."""

    name: str
    availability: Availability
    evidence: str
    value_signal: ValueSignal = ValueSignal.NOT_APPLICABLE
    note: str = ""


@dataclass(frozen=True)
class CorrelationKeyAudit:
    """A candidate key for joining outcomes back to dispatch rows."""

    name: str
    availability: Availability
    join_strength: JoinStrength
    evidence: str
    note: str = ""


@dataclass(frozen=True)
class OutcomeArtifactAudit:
    """One candidate outcome-artifact class (plan sec. 2, five classes)."""

    class_id: str  # stable :class:`OutcomeClass` value; adapters + correlation key on this
    name: str
    exists: bool
    location: str
    record_key: str
    join_strength: JoinStrength
    availability: Availability
    evidence: str


@dataclass(frozen=True)
class Inventory:
    """The full Step 1 availability audit."""

    producer_fields: tuple[FieldAudit, ...]
    correlation_keys: tuple[CorrelationKeyAudit, ...]
    cohort_dimensions: tuple[FieldAudit, ...]
    outcome_artifacts: tuple[OutcomeArtifactAudit, ...]


# --------------------------------------------------------------------------- #
# Producer field audit -- verified from telemetry-writer.ps1 + real records.
# --------------------------------------------------------------------------- #

_WRITER = "telemetry-writer.ps1"
_ROUTER = "skill-router.ps1"

# The router hardcodes token/cost to zero on EVERY telemetry write path, not
# only in stub mode. Verified at skill-router.ps1 lines 580, 587, 609, 613,
# 640, 644, 665, 671 (every Write-TelemetryInvocation call passes
# -TokensIn 0 -TokensOut 0 -CostUsd 0.0). The writer additionally zeroes them
# when OPENAI_API_KEY is absent (telemetry-writer.ps1 lines 34-39).
_TOKEN_COST_NOTE = (
    "Field is structurally present in every record but carries NO signal today: "
    f"{_ROUTER} passes 0 on every Write-TelemetryInvocation call path, and "
    f"{_WRITER}:34-39 zeroes it in stub mode. Both real records show 0. Any "
    "token/cost metric is therefore UNAVAILABLE from the current producer."
)


def audit_producer_fields() -> tuple[FieldAudit, ...]:
    """Classify each of the eight producer fields with primary-source evidence."""
    return (
        FieldAudit(
            name="timestamp",
            availability=Availability.PRESENT,
            value_signal=ValueSignal.REAL,
            evidence=f"{_WRITER}:42 writes DateTime.UtcNow ISO-8601; present in both real records.",
        ),
        FieldAudit(
            name="skill",
            availability=Availability.PRESENT,
            value_signal=ValueSignal.REAL,
            evidence=f"{_WRITER}:43; both real records = 'plan-init'.",
        ),
        FieldAudit(
            name="model",
            availability=Availability.PRESENT,
            value_signal=ValueSignal.REAL,
            evidence=f"{_WRITER}:44; real records = 'gpt-5.6-sol' and 'claude'.",
        ),
        FieldAudit(
            name="tokens_in",
            availability=Availability.PRESENT,
            value_signal=ValueSignal.ALWAYS_ZERO,
            evidence=f"{_WRITER}:45; both real records = 0.",
            note=_TOKEN_COST_NOTE,
        ),
        FieldAudit(
            name="tokens_out",
            availability=Availability.PRESENT,
            value_signal=ValueSignal.ALWAYS_ZERO,
            evidence=f"{_WRITER}:46; both real records = 0.",
            note=_TOKEN_COST_NOTE,
        ),
        FieldAudit(
            name="latency_ms",
            availability=Availability.PRESENT,
            value_signal=ValueSignal.REAL,
            evidence=(
                f"{_WRITER}:47 from Stopwatch.ElapsedMilliseconds; nonzero in both "
                "real records (1730, 4) even though both are stub. The one metric "
                "with real signal in a stub-only stream."
            ),
        ),
        FieldAudit(
            name="cost_usd",
            availability=Availability.PRESENT,
            value_signal=ValueSignal.ALWAYS_ZERO,
            evidence=f"{_WRITER}:48; both real records = 0.",
            note=_TOKEN_COST_NOTE,
        ),
        FieldAudit(
            name="verdict",
            availability=Availability.PRESENT,
            value_signal=ValueSignal.REAL,
            evidence=(
                f"{_WRITER}:22 ValidateSet pass|fail|stub, written at :49; both real "
                "records = 'stub'."
            ),
        ),
    )


#: Producer fields that are structurally present in every record but hardcoded to
#: 0 by the producer on EVERY write path (pass/fail/stub) -- Step 1 verified they
#: are NEVER a measurement. Single source of truth for "which fields the producer
#: never measures" (plan sec. 6); derived from the field audit so it cannot drift
#: from the classifications above. The Step 2 adapter must not report any of these
#: as a measured value regardless of verdict.
ALWAYS_ZERO_PRODUCER_FIELDS: frozenset[str] = frozenset(
    audit.name for audit in audit_producer_fields() if audit.value_signal is ValueSignal.ALWAYS_ZERO
)


# --------------------------------------------------------------------------- #
# Correlation-key candidates -- the crux of the honest inventory.
# --------------------------------------------------------------------------- #


def audit_correlation_keys() -> tuple[CorrelationKeyAudit, ...]:
    """Classify candidate keys for joining outcomes to dispatch rows.

    The pinned eight-field contract carries NO run/session/record id, so there is
    no strong dispatch-correlatable key. Timestamp- and skill-name-based joins are
    ambiguous and stay unjoined (plan sec. 6).
    """
    return (
        CorrelationKeyAudit(
            name="run/session/record id",
            availability=Availability.ABSENT,
            join_strength=JoinStrength.NONE,
            evidence=(
                f"{_WRITER}:41-50 constructs the record with exactly the eight pinned "
                "fields and no id. A SKILL_ROUTER_SESSION_ID exists in the router "
                f"runtime ({_ROUTER}:180-185) and in a SEPARATE spend-ledger file, but "
                "it is never persisted into invocations.jsonl. Neither real record "
                "carries any id column."
            ),
            note="No strong key exists; this is the reason most outcome classes stay unjoined.",
        ),
        CorrelationKeyAudit(
            name="timestamp window",
            availability=Availability.AMBIGUOUS,
            join_strength=JoinStrength.TIMESTAMP_WINDOW_ONLY,
            evidence=(
                "timestamp is the only per-record time key. Multiple dispatches can "
                "share the same ISO-8601 second and stub records are byte-identical "
                "(plan sec. 6), so a time-window join cannot uniquely attribute an "
                "outcome to a dispatch."
            ),
            note="Ambiguous by design -- stays unjoined per plan sec. 6.",
        ),
        CorrelationKeyAudit(
            name="skill name",
            availability=Availability.AMBIGUOUS,
            join_strength=JoinStrength.SKILL_NAME_ONLY,
            evidence=(
                "skill is present but non-unique (many dispatches share one skill "
                "name, e.g. both real records = 'plan-init'). Usable for cohorting, "
                "not for row-level outcome attribution."
            ),
            note="Cohort key only, not a row join.",
        ),
    )


# --------------------------------------------------------------------------- #
# Cohort dimensions the plan wants to stratify by (plan sec. 6).
# --------------------------------------------------------------------------- #


def audit_cohort_dimensions() -> tuple[FieldAudit, ...]:
    """Classify the cohort dimensions reports stratify by."""
    return (
        FieldAudit(
            name="skill",
            availability=Availability.PRESENT,
            evidence="= producer field 'skill' (present).",
        ),
        FieldAudit(
            name="model",
            availability=Availability.PRESENT,
            evidence="= producer field 'model' (present).",
        ),
        FieldAudit(
            name="project",
            availability=Availability.ABSENT,
            evidence=(
                "No project field in the eight-field contract and not derivable from "
                "any present field. Would require an outcome-artifact join, which has "
                "no strong key."
            ),
        ),
        FieldAudit(
            name="task_type",
            availability=Availability.ABSENT,
            evidence="No task_type field; not derivable from the eight fields.",
        ),
        FieldAudit(
            name="producer_schema",
            availability=Availability.DERIVABLE,
            evidence=(
                "Derived by field-set equality: 'skillmesh-v1' iff record fields == "
                "the pinned eight (plan sec. 6); any other set -> 'unknown' cohort, "
                "never merged."
            ),
        ),
        FieldAudit(
            name="schema_version",
            availability=Availability.DERIVABLE,
            evidence=(
                "Assigned by the mesh-lens store (integer 1), not produced by Skill "
                "Mesh (plan sec. 6). Readers tolerate older, refuse newer."
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# Outcome-artifact classes -- the five candidates pinned in plan sec. 2.
# Existence/location verified on-workspace; join analysis is deterministic from
# the id-less dispatch schema.
# --------------------------------------------------------------------------- #


def audit_outcome_artifacts() -> tuple[OutcomeArtifactAudit, ...]:
    """Classify each of the five candidate outcome-artifact classes.

    The dispatch row exposes only ``skill`` and ``timestamp`` as possible join
    columns and carries no id, so NONE of these five classes has a strong
    dispatch-correlatable key (verified on-workspace). Each is reported at its
    true join strength -- 3 ambiguous (skill-name- or timestamp-window-only),
    2 with no shared key at all -- and all five stay unjoined, exactly as plan
    sec. 2 predicts.
    """
    return (
        OutcomeArtifactAudit(
            class_id=OutcomeClass.BUILD_STEP_REPORT.value,
            name=".build-step/<role>-report.md dev/reviewer reports",
            exists=True,
            location="dev/.build-step/ (3 real files) + per-project .build-step/ dirs",
            record_key=(
                "review-LENS name in filename (review-tests, review-style) + iteration "
                "suffix; body carries verdict + reviewed step number + finding counts. "
                "No in-file timestamp, no dispatch id (only filesystem mtime)."
            ),
            join_strength=JoinStrength.SKILL_NAME_ONLY,
            availability=Availability.AMBIGUOUS,
            evidence=(
                "Verified present. The filename role is a review LENS, not a dispatch "
                "skill, so any role->skill match is fuzzy; mtime gives only a coarse "
                "time window. No reliable join -- stays unjoined."
            ),
        ),
        OutcomeArtifactAudit(
            class_id=OutcomeClass.GH_ISSUE_STATE.value,
            name="GitHub issue states",
            exists=True,
            location=(
                "per-project repo via `gh issue list --json number,title,state` "
                "(mesh-lens #1-#12 verified OPEN)"
            ),
            record_key="issue number + state; titles embed 'Step N', not a skill name",
            join_strength=JoinStrength.NONE,
            availability=Availability.ABSENT,
            evidence=(
                "Verified via authenticated gh. The pulled fields carry no skill name "
                "and no dispatch time; a title references a plan step, not a dispatch. "
                "No column overlaps the dispatch row."
            ),
        ),
        OutcomeArtifactAudit(
            class_id=OutcomeClass.GIT_LOG.value,
            name="git log of the target repo",
            exists=True,
            location="`git -C <repo> log` of each project repo (mesh-lens = 3 commits, verified)",
            record_key=(
                "commit sha + author-date + message (message sometimes prefixes the "
                "producing skill, e.g. 'repo-sync:')"
            ),
            join_strength=JoinStrength.TIMESTAMP_WINDOW_ONLY,
            availability=Availability.AMBIGUOUS,
            evidence=(
                "Verified. Author-date could be time-window joined to a dispatch "
                "timestamp, but the join is ambiguous (many dispatches per commit "
                "window, no dispatch id). Stays unjoined per plan sec. 6."
            ),
        ),
        OutcomeArtifactAudit(
            class_id=OutcomeClass.PLAN_STATUS.value,
            name="Plan **Status:** DONE / ### Step N markers",
            exists=True,
            location=(
                "canonical plan.md (6 '### Step N:' headers verified; zero real "
                "'**Status:** DONE' markers)"
            ),
            record_key="step number (+ Issue #7-#12); no skill, no timestamp",
            join_strength=JoinStrength.NONE,
            availability=Availability.ABSENT,
            evidence=(
                "Verified: the plan has six `### Step N:` headers and uses "
                "`**Done when:**`, not `**Status:** DONE` (the only DONE-string hit is "
                "meta-prose in sec. 2). A step has no dispatch-correlatable key."
            ),
        ),
        OutcomeArtifactAudit(
            class_id=OutcomeClass.SKILL_ITERATE_LOG.value,
            name="skill-iterate run-logs",
            exists=True,
            location=(
                ".claude/skills/<name>/evals/results.tsv (32 non-empty verified) "
                "+ skill-iterate/tmp/ scratch"
            ),
            record_key=(
                "TSV schema 'commit\\tscore\\tstatus\\tdescription\\twall_seconds'; row key "
                "is the git COMMIT SHA. Skill name is implicit in the dir path, not a "
                "row column. No timestamp column; iteration index only free-text in "
                "'description'."
            ),
            join_strength=JoinStrength.SKILL_NAME_ONLY,
            availability=Availability.AMBIGUOUS,
            evidence=(
                "Verified present. The only overlap with a dispatch row is the "
                "non-unique skill name (via the containing dir); the row's commit sha "
                "does not appear in the dispatch stream. Skill-name-only, so ambiguous "
                "and unjoined."
            ),
        ),
    )


def build_inventory() -> Inventory:
    """Assemble the full Step 1 availability audit."""
    return Inventory(
        producer_fields=audit_producer_fields(),
        correlation_keys=audit_correlation_keys(),
        cohort_dimensions=audit_cohort_dimensions(),
        outcome_artifacts=audit_outcome_artifacts(),
    )


def outcome_audit_by_class(inventory: Inventory) -> dict[str, OutcomeArtifactAudit]:
    """Index the outcome-artifact audits by their stable ``class_id``.

    Step 3's outcome adapters and correlation engine consult this map to decide
    whether a class carries a provable (``STRONG_KEY``) join, so the join policy is
    always read FROM the Step 1 inventory (single source of truth, plan sec. 6) and
    never re-derived.
    """
    return {audit.class_id: audit for audit in inventory.outcome_artifacts}


# --------------------------------------------------------------------------- #
# Runtime telemetry-stream audit -- confirms reality matches the contract and
# degrades gracefully on an absent/empty stream (plan sec. 6).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StreamAudit:
    """Result of reading a real telemetry stream and checking it against the contract."""

    path: str
    exists: bool
    record_count: int
    malformed_count: int
    observed_field_sets: tuple[tuple[str, ...], ...]
    matches_pinned_contract: bool
    note: str


def _iter_nonblank_lines(text: str) -> Iterable[str]:
    for line in text.splitlines():  # splitlines() handles the stream's CRLF endings
        stripped = line.strip()
        if stripped:
            yield stripped


def audit_telemetry_stream(path: Path) -> StreamAudit:
    """Read a telemetry stream and audit it against the pinned contract.

    Degrades gracefully: an absent or empty stream yields ``record_count == 0``
    with an informative note and ``matches_pinned_contract == False`` -- never an
    exception (plan sec. 6, "Independent by contract").
    """
    if not path.exists():
        return StreamAudit(
            path=str(path),
            exists=False,
            record_count=0,
            malformed_count=0,
            observed_field_sets=(),
            matches_pinned_contract=False,
            note="Stream absent; ingest would complete with zero records (graceful).",
        )

    text = path.read_text(encoding="utf-8")
    record_count = 0
    malformed_count = 0
    field_sets: set[tuple[str, ...]] = set()

    for line in _iter_nonblank_lines(text):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            malformed_count += 1
            continue
        if not isinstance(obj, dict):
            malformed_count += 1
            continue
        record_count += 1
        field_sets.add(tuple(sorted(obj.keys())))

    pinned_sorted = tuple(sorted(PINNED_PRODUCER_FIELDS))
    matches = record_count > 0 and field_sets == {pinned_sorted}

    if record_count == 0:
        note = "Stream present but empty; ingest would complete with zero records (graceful)."
    elif matches:
        note = f"All {record_count} record(s) match the pinned eight-field contract."
    else:
        note = (
            f"{record_count} record(s) present but field sets diverge from the pinned "
            "contract; divergent records land in the 'unknown' cohort (never merged)."
        )

    return StreamAudit(
        path=str(path),
        exists=True,
        record_count=record_count,
        malformed_count=malformed_count,
        observed_field_sets=tuple(sorted(field_sets)),
        matches_pinned_contract=matches,
        note=note,
    )
