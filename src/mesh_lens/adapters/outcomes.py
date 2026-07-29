"""Outcome-artifact adapters (plan sec. 7, Step 3).

Each adapter INGESTS one candidate outcome-artifact class (plan sec. 2) into
standalone :class:`NormalizedOutcome` records that carry full source PROVENANCE
(``<source-relpath>@<locator>`` + a SHA-256 ``content_hash`` of the raw record
span) and ``schema_version=1``. No outcome value is invented: an adapter reports
only what its source actually contains, and a class whose source is absent yields
zero records (reported missing, NEVER inferred).

Crucially, an adapter NEVER decides a join on its own. A record's ``join_key`` is
set ONLY through :func:`_join_key_for`, which returns a key ONLY when the Step 1
inventory classifies the class as ``STRONG_KEY`` (single source of truth, plan
sec. 6). Under today's inventory NO class is strong-keyed, so every real outcome
record here is emitted with ``join_key=None`` -- even when a key-shaped value (a
commit sha) exists, it is refused as a dispatch join key because the inventory
proves it is not dispatch-correlatable. Correlation itself lives in
:mod:`mesh_lens.correlate`; this module only ingests + provenances.

The five real adapters:
  * ``build-step-report``   -- reviewer verdict + finding counts (skill-name-only)
  * ``gh-issue-state``      -- issue number + state (no shared key)
  * ``git-log``             -- commit sha + author-date + subject (timestamp-window-only)
  * ``plan-status``         -- ``**Status:**`` disposition per step (no shared key)
  * ``skill-iterate-log``   -- eval-run commit + score + status (skill-name-only)

:func:`parse_keyed_outcomes` is a SEPARATE future-shaped adapter for the day a
producer emits a stable run/session key; it is exercised only by the synthetic
keyed fixture to prove the strong-key join PATH preserves provenance.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from mesh_lens.inventory import (
    Inventory,
    JoinStrength,
    OutcomeClass,
    build_inventory,
    outcome_audit_by_class,
)
from mesh_lens.models import CURRENT_SCHEMA_VERSION, NormalizedOutcome, Provenance
from mesh_lens.store import IngestDiagnostic


@dataclass(frozen=True)
class OutcomeParse:
    """A parser's output: the records it recovered + a diagnostic per malformed unit.

    Parsers NEVER raise on bad input (graceful degradation, plan sec. 6): a broken
    record is diagnosed and skipped so its siblings still parse, mirroring the Step 2
    dispatch scan's diagnose-not-abort contract.
    """

    records: list[NormalizedOutcome] = field(default_factory=list)
    malformed: list[IngestDiagnostic] = field(default_factory=list)


_Parser = Callable[[str, str, Inventory], OutcomeParse]


@dataclass(frozen=True)
class OutcomeIngestReport:
    """Result of ingesting one outcome-artifact source -- honest counts, never fabricated."""

    outcome_class: str
    source_relpath: str
    source_present: bool
    records: tuple[NormalizedOutcome, ...]
    malformed: tuple[IngestDiagnostic, ...]
    note: str

    @property
    def count(self) -> int:
        return len(self.records)


def _content_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _join_key_for(outcome_class: str, inventory: Inventory, candidate: str | None) -> str | None:
    """Return ``candidate`` as a join key ONLY if the inventory blesses the class STRONG_KEY.

    This is the adapter-level no-bad-join guard, sourced from the Step 1 inventory
    (plan sec. 6). Because no real class is ``STRONG_KEY`` today, every real record
    gets ``None`` here -- a key-shaped value (e.g. a commit sha) is refused because
    the inventory proves it is not dispatch-correlatable.
    """
    audit = outcome_audit_by_class(inventory).get(outcome_class)
    if audit is not None and audit.join_strength is JoinStrength.STRONG_KEY:
        return candidate
    return None


def _outcome(
    outcome_class: str,
    provenance: Provenance,
    fields: dict[str, str],
    inventory: Inventory,
    join_candidate: str | None = None,
) -> NormalizedOutcome:
    return NormalizedOutcome(
        schema_version=CURRENT_SCHEMA_VERSION,
        provenance=provenance,
        outcome_class=outcome_class,
        fields=tuple(sorted(fields.items())),
        join_key=_join_key_for(outcome_class, inventory, join_candidate),
    )


# --------------------------------------------------------------------------- #
# Class 1: .build-step/<role>-report.md dev/reviewer reports (skill-name-only).
# One record per report file; the file's raw text is the hashed span.
# --------------------------------------------------------------------------- #

_VERDICT_RE = re.compile(r"^\*\*Verdict:\*\*\s*(.+?)\s*$", re.MULTILINE)
_FINDINGS_RE = re.compile(r"^\*\*Total findings:\*\*\s*(\d+)", re.MULTILINE)
_STEP_RE = re.compile(r"Step\s+(\d+)")


def parse_build_step_report(text: str, source_relpath: str, inventory: Inventory) -> OutcomeParse:
    verdict_match = _VERDICT_RE.search(text)
    if verdict_match is None:
        return OutcomeParse()  # not a reviewer report -> nothing to ingest (reported missing)
    outcome_fields: dict[str, str] = {"verdict": verdict_match.group(1)}
    findings_match = _FINDINGS_RE.search(text)
    if findings_match is not None:
        outcome_fields["total_findings"] = findings_match.group(1)
    step_match = _STEP_RE.search(text)
    if step_match is not None:
        outcome_fields["reviewed_step"] = step_match.group(1)
    # Locator = the verdict line; content hash covers the whole raw report span.
    verdict_line = text[: verdict_match.start()].count("\n") + 1
    provenance = Provenance(
        source_relpath=source_relpath, line_number=verdict_line, content_hash=_content_hash(text)
    )
    record = _outcome(OutcomeClass.BUILD_STEP_REPORT.value, provenance, outcome_fields, inventory)
    return OutcomeParse(records=[record])


# --------------------------------------------------------------------------- #
# Class 2: GitHub issue states (no shared key with a dispatch row).
# One record per issue; locator = the stable issue number.
# --------------------------------------------------------------------------- #


def parse_gh_issues(text: str, source_relpath: str, inventory: Inventory) -> OutcomeParse:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        # A truncated/binary/broken `gh --json` dump -> diagnose, never raise.
        diag = IngestDiagnostic(f"{source_relpath}@1", 1, f"invalid issue JSON: {exc.msg}")
        return OutcomeParse(malformed=[diag])
    if not isinstance(parsed, list):
        diag = IngestDiagnostic(
            f"{source_relpath}@1",
            1,
            f"expected a JSON array of issues, got {type(parsed).__name__}",
        )
        return OutcomeParse(malformed=[diag])
    records: list[NormalizedOutcome] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        issue = cast("dict[str, Any]", item)
        number = issue.get("number")
        if not isinstance(number, int):
            continue
        fields = {
            "number": str(number),
            "state": str(issue.get("state", "")),
            "title": str(issue.get("title", "")),
        }
        canonical = json.dumps(issue, sort_keys=True, ensure_ascii=True)
        provenance = Provenance(
            source_relpath=source_relpath,
            line_number=number,  # issue number is the record's stable locator index
            content_hash=_content_hash(canonical),
        )
        records.append(_outcome(OutcomeClass.GH_ISSUE_STATE.value, provenance, fields, inventory))
    return OutcomeParse(records=records)


# --------------------------------------------------------------------------- #
# Class 3: git log of the target repo (timestamp-window-only -> ambiguous).
# TSV `sha\tauthor-date\tsubject`, one record per line.
# --------------------------------------------------------------------------- #


def parse_git_log(text: str, source_relpath: str, inventory: Inventory) -> OutcomeParse:
    records: list[NormalizedOutcome] = []
    for index, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if len(parts) < 3:
            continue
        commit, author_date, subject = parts[0], parts[1], "\t".join(parts[2:])
        fields = {"commit": commit, "author_date": author_date, "subject": subject}
        provenance = Provenance(
            source_relpath=source_relpath, line_number=index, content_hash=_content_hash(raw)
        )
        # commit sha is a real key but NOT dispatch-correlatable (timestamp-window
        # only per inventory); passed as a candidate and refused by _join_key_for.
        records.append(
            _outcome(
                OutcomeClass.GIT_LOG.value, provenance, fields, inventory, join_candidate=commit
            )
        )
    return OutcomeParse(records=records)


# --------------------------------------------------------------------------- #
# Class 4: Plan **Status:** disposition markers (no shared key with a dispatch).
# One record per marker, tied to the nearest preceding `### Step N:` header. A step
# with NO marker yields NO record -- its disposition is reported missing, never
# inferred.
# --------------------------------------------------------------------------- #

_PLAN_STEP_RE = re.compile(r"^###\s+Step\s+(\d+)\b")
_PLAN_STATUS_RE = re.compile(r"^\s*(?:[-*]\s*)?\*\*Status:\*\*\s*(\S+)\s*(.*?)\s*$")


def parse_plan_status(text: str, source_relpath: str, inventory: Inventory) -> OutcomeParse:
    records: list[NormalizedOutcome] = []
    current_step: str | None = None
    for index, raw in enumerate(text.splitlines(), start=1):
        step_match = _PLAN_STEP_RE.match(raw)
        if step_match is not None:
            current_step = step_match.group(1)
            continue
        status_match = _PLAN_STATUS_RE.match(raw)
        if status_match is None:
            continue
        fields = {"status": status_match.group(1)}
        detail = status_match.group(2)
        if detail:
            fields["detail"] = detail
        if current_step is not None:
            fields["step"] = current_step
        provenance = Provenance(
            source_relpath=source_relpath, line_number=index, content_hash=_content_hash(raw)
        )
        records.append(_outcome(OutcomeClass.PLAN_STATUS.value, provenance, fields, inventory))
    return OutcomeParse(records=records)


# --------------------------------------------------------------------------- #
# Class 5: skill-iterate run-logs (skill-name-only -> ambiguous).
# TSV with a header row `commit\tscore\tstatus\tdescription\twall_seconds`.
# --------------------------------------------------------------------------- #

_SKILL_ITERATE_HEADER = ("commit", "score", "status", "description", "wall_seconds")


def parse_skill_iterate(text: str, source_relpath: str, inventory: Inventory) -> OutcomeParse:
    records: list[NormalizedOutcome] = []
    for index, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if tuple(parts[: len(_SKILL_ITERATE_HEADER)]) == _SKILL_ITERATE_HEADER:
            continue  # header row, not a result
        if len(parts) < len(_SKILL_ITERATE_HEADER):
            continue
        fields = dict(zip(_SKILL_ITERATE_HEADER, parts, strict=False))
        provenance = Provenance(
            source_relpath=source_relpath, line_number=index, content_hash=_content_hash(raw)
        )
        # commit sha is the row key but not dispatch-correlatable (skill-name only).
        records.append(
            _outcome(
                OutcomeClass.SKILL_ITERATE_LOG.value,
                provenance,
                fields,
                inventory,
                join_candidate=parts[0],
            )
        )
    return OutcomeParse(records=records)


_PARSERS: dict[str, _Parser] = {
    OutcomeClass.BUILD_STEP_REPORT.value: parse_build_step_report,
    OutcomeClass.GH_ISSUE_STATE.value: parse_gh_issues,
    OutcomeClass.GIT_LOG.value: parse_git_log,
    OutcomeClass.PLAN_STATUS.value: parse_plan_status,
    OutcomeClass.SKILL_ITERATE_LOG.value: parse_skill_iterate,
}


def ingest_outcome_source(
    path: Path,
    outcome_class: str,
    inventory: Inventory | None = None,
    source_relpath: str | None = None,
) -> OutcomeIngestReport:
    """Ingest one outcome-artifact source into standalone :class:`NormalizedOutcome` records.

    Degrades gracefully like the dispatch store (plan sec. 6): an absent source
    yields zero records with an explicit missing note; a present-but-empty source
    yields zero records too. Nothing is inferred to fill a gap.
    """
    inv = inventory if inventory is not None else build_inventory()
    parser = _PARSERS.get(outcome_class)
    if parser is None:
        raise ValueError(f"no outcome adapter for class {outcome_class!r}")
    relpath = source_relpath if source_relpath is not None else path.name

    if not path.exists():
        return OutcomeIngestReport(
            outcome_class=outcome_class,
            source_relpath=relpath,
            source_present=False,
            records=(),
            malformed=(),
            note="source artifact absent; 0 outcome records (reported missing, never inferred)",
        )

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # A non-UTF8 / binary artifact must NOT abort this class (and never a sibling
        # class): diagnose the whole source and return 0 records (plan sec. 6).
        diag = IngestDiagnostic(f"{relpath}@1", 1, f"source is not valid UTF-8: {exc.reason}")
        return OutcomeIngestReport(
            outcome_class=outcome_class,
            source_relpath=relpath,
            source_present=True,
            records=(),
            malformed=(diag,),
            note="source present but not UTF-8 decodable; 0 outcome records "
            "(reported missing, never inferred)",
        )

    parsed = parser(text, relpath, inv)
    records = tuple(parsed.records)
    malformed = tuple(parsed.malformed)
    if records:
        note = f"{len(records)} standalone outcome record(s) ingested; join decided by correlate()"
    elif malformed:
        note = (
            f"source present but 0 parseable outcome records; {len(malformed)} malformed "
            "diagnosed (reported missing, never inferred)"
        )
    else:
        note = (
            "source present but carries no parseable outcome record "
            "(reported missing, never inferred)"
        )
    return OutcomeIngestReport(
        outcome_class=outcome_class,
        source_relpath=relpath,
        source_present=True,
        records=records,
        malformed=malformed,
        note=note,
    )


# --------------------------------------------------------------------------- #
# Future / synthetic strong-key adapter -- clearly SEPARATE from the real ones.
# --------------------------------------------------------------------------- #


def parse_keyed_outcomes(
    text: str, source_relpath: str, outcome_class: str, inventory: Inventory
) -> OutcomeParse:
    """Adapter for a FUTURE outcome artifact that carries a stable ``run_id`` key.

    Each JSONL row is ``{"run_id": "...", <string fields>}``. The ``run_id`` becomes
    the record's ``join_key`` ONLY when the inventory classifies ``outcome_class`` as
    ``STRONG_KEY`` (via :func:`_join_key_for`). Under today's real inventory no class
    is strong-keyed, so the key is refused and the record stays unjoined -- IDENTICAL
    honesty to the real adapters. The synthetic keyed fixture pairs this with a
    synthetic STRONG_KEY inventory to prove the join PATH preserves provenance the
    day a real run/session key lands.

    Never-raise (plan sec. 6): a malformed JSONL line is diagnosed and skipped so its
    siblings in the same file still parse (mirrors the Step 2 dispatch scan).
    """
    records: list[NormalizedOutcome] = []
    malformed: list[IngestDiagnostic] = []
    for index, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            malformed.append(
                IngestDiagnostic(f"{source_relpath}@{index}", index, f"invalid JSON: {exc.msg}")
            )
            continue
        if not isinstance(obj, dict):
            malformed.append(
                IngestDiagnostic(
                    f"{source_relpath}@{index}",
                    index,
                    f"expected a JSON object, got {type(obj).__name__}",
                )
            )
            continue
        row = cast("dict[str, Any]", obj)
        run_id = row.get("run_id")
        run_key = run_id if isinstance(run_id, str) else None
        fields = {k: str(v) for k, v in row.items() if k != "run_id"}
        provenance = Provenance(
            source_relpath=source_relpath, line_number=index, content_hash=_content_hash(raw)
        )
        records.append(
            _outcome(outcome_class, provenance, fields, inventory, join_candidate=run_key)
        )
    return OutcomeParse(records=records, malformed=malformed)
