"""Append-only normalized event store + per-source ingest checkpoint (plan sec. 6).

The store owns all durable state under a local ``root`` directory:

  * ``events.jsonl``   -- append-only normalized :class:`NormalizedInvocation`
                          records, one JSON object per line, each carrying
                          ``schema_version``.
  * ``checkpoint.json`` -- per-source ingest checkpoint: the byte offset already
                          consumed, the last line number reached, and a SHA-256
                          ``overlap_hash`` of the consumed prefix.

Idempotency (plan sec. 6). Re-ingesting a source only reads bytes PAST the
checkpoint offset, so an unchanged source adds nothing and appending one line adds
exactly one record. Before trusting the checkpoint, the store hash-verifies the
overlap window (the consumed prefix): a shrink or a hash mismatch means the source
was truncated or rotated, which triggers a REBUILD of that source's derived
records (drop them, re-ingest from offset 0) rather than silent corruption or
duplicates.

A malformed (non-JSON or non-object) row is DIAGNOSED and skipped; it never aborts
ingest of its sibling rows. An absent or empty source completes with zero records
and no error (graceful degradation, plan sec. 6).

Only complete newline-terminated lines are consumed; a trailing partial line (a
half-flushed append) waits for its terminator, so a record is never ingested
twice.

Durability backstop. The append precedes the checkpoint write, so a crash BETWEEN
them could otherwise re-append a record on the next ingest. Append therefore
dedups by provenance ref (`<relpath>@<line>`, unique per source line): a re-scan of
already-stored bytes contributes no new refs, so no duplicate is written even if a
checkpoint write was lost. (The store is single-writer; concurrent writers are out
of scope.)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from mesh_lens.adapters.skill_mesh import PRODUCER_SCHEMA_UNKNOWN, normalize_row
from mesh_lens.inventory import PRODUCER_SCHEMA_ID
from mesh_lens.models import (
    CURRENT_SCHEMA_VERSION,
    NormalizedInvocation,
    Provenance,
    check_schema_version,
)


@dataclass(frozen=True)
class IngestDiagnostic:
    """One malformed source row, reported instead of aborting ingest (plan sec. 6)."""

    provenance_ref: str  # "<source-relpath>@<line-number>"
    line_number: int
    reason: str


@dataclass(frozen=True)
class IngestReport:
    """The result of ingesting one source -- honest counts, never fabricated."""

    source_relpath: str
    source_present: bool
    ingested: int  # new normalized records appended this run
    skipped_blank: int
    malformed: tuple[IngestDiagnostic, ...]
    rebuilt: bool  # True iff an overlap mismatch forced a rebuild of this source
    by_producer_schema: dict[str, int]  # split of records ingested this run
    total_events: int  # total events in the store after this ingest


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class _SourceCheckpoint:
    byte_offset: int
    line_number: int
    overlap_hash: str

    def to_json(self) -> dict[str, Any]:
        return {
            "byte_offset": self.byte_offset,
            "line_number": self.line_number,
            "overlap_hash": self.overlap_hash,
        }

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> _SourceCheckpoint:
        return cls(
            byte_offset=int(obj["byte_offset"]),
            line_number=int(obj["line_number"]),
            overlap_hash=obj["overlap_hash"],
        )


@dataclass
class _ScanResult:
    events: list[NormalizedInvocation] = field(default_factory=list)
    malformed: list[IngestDiagnostic] = field(default_factory=list)
    skipped_blank: int = 0
    end_offset: int = 0
    end_line: int = 0


class Store:
    """A local append-only normalized event store rooted at ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.events_path = root / "events.jsonl"
        self.checkpoint_path = root / "checkpoint.json"

    # -- public API ------------------------------------------------------- #

    def ingest_source(self, source_path: Path, source_relpath: str | None = None) -> IngestReport:
        """Ingest new rows from ``source_path`` idempotently (plan sec. 6)."""
        relpath = source_relpath if source_relpath is not None else source_path.name

        if not source_path.exists():
            # Absent stream: complete with zero records, never an error.
            return IngestReport(
                source_relpath=relpath,
                source_present=False,
                ingested=0,
                skipped_blank=0,
                malformed=(),
                rebuilt=False,
                by_producer_schema={},
                total_events=self._count_events(),
            )

        data = source_path.read_bytes()
        checkpoint = self._read_checkpoint()
        prior = checkpoint.get(relpath)

        start_offset = 0
        start_line = 0
        rebuilt = False
        if prior is not None:
            if self._overlap_ok(data, prior):
                start_offset = prior.byte_offset
                start_line = prior.line_number
            else:
                # Truncation / rotation: rebuild this source's derived records.
                self._drop_source_events(relpath)
                rebuilt = True

        scan = self._scan(data, relpath, start_offset, start_line)

        # Dedup by provenance ref (`<relpath>@<line>`), computed AFTER any rebuild
        # drop. Provenance refs are unique per (source, line), so a genuinely new
        # line always has a new ref; a re-scan of already-stored bytes never
        # appends a duplicate. This closes the append-then-checkpoint crash window:
        # if a crash lands the append but loses the checkpoint write, the next
        # ingest re-scans the same bytes but the dedup drops them (durability
        # backstop, plan sec. 6).
        existing_refs = self._existing_refs()
        new_events = [e for e in scan.events if e.provenance.ref not in existing_refs]
        self._append_events(new_events)

        by_schema: dict[str, int] = {}
        for event in new_events:
            by_schema[event.producer_schema] = by_schema.get(event.producer_schema, 0) + 1

        checkpoint[relpath] = _SourceCheckpoint(
            byte_offset=scan.end_offset,
            line_number=scan.end_line,
            overlap_hash=_sha256_hex(data[: scan.end_offset]),
        )
        self._write_checkpoint(checkpoint)

        return IngestReport(
            source_relpath=relpath,
            source_present=True,
            ingested=len(new_events),
            skipped_blank=scan.skipped_blank,
            malformed=tuple(scan.malformed),
            rebuilt=rebuilt,
            by_producer_schema=by_schema,
            total_events=self._count_events(),
        )

    def read_events(self) -> list[NormalizedInvocation]:
        """Read every stored record; refuses a NEWER schema_version (plan sec. 6)."""
        if not self.events_path.exists():
            return []
        events: list[NormalizedInvocation] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                obj = cast("dict[str, Any]", json.loads(line))
                events.append(NormalizedInvocation.from_json_dict(obj))
        return events

    def comparable_events(self) -> list[NormalizedInvocation]:
        """Only ``skillmesh-v1`` records; the ``unknown`` cohort is never merged in."""
        return [e for e in self.read_events() if e.producer_schema == PRODUCER_SCHEMA_ID]

    def unknown_events(self) -> list[NormalizedInvocation]:
        """The incomparable ``unknown`` cohort -- reported separately, never merged."""
        return [e for e in self.read_events() if e.producer_schema == PRODUCER_SCHEMA_UNKNOWN]

    # -- scanning --------------------------------------------------------- #

    def _scan(self, data: bytes, relpath: str, start_offset: int, start_line: int) -> _ScanResult:
        result = _ScanResult(end_offset=start_offset, end_line=start_line)
        offset = start_offset
        line_number = start_line
        n = len(data)

        while offset < n:
            nl = data.find(b"\n", offset)
            if nl == -1:
                break  # trailing partial line -- wait for its terminator
            raw_line = data[offset:nl]
            if raw_line.endswith(b"\r"):
                raw_line = raw_line[:-1]
            line_number += 1
            offset = nl + 1

            text = raw_line.decode("utf-8", errors="replace")
            if not text.strip():
                result.skipped_blank += 1
                continue

            content_hash = _sha256_hex(raw_line)
            provenance = Provenance(
                source_relpath=relpath, line_number=line_number, content_hash=content_hash
            )
            try:
                obj = json.loads(text)
            except json.JSONDecodeError as exc:
                result.malformed.append(
                    IngestDiagnostic(provenance.ref, line_number, f"invalid JSON: {exc.msg}")
                )
                continue
            if not isinstance(obj, dict):
                result.malformed.append(
                    IngestDiagnostic(
                        provenance.ref,
                        line_number,
                        f"expected a JSON object, got {type(obj).__name__}",
                    )
                )
                continue

            result.events.append(normalize_row(cast("dict[str, Any]", obj), provenance))

        # offset sits on a consumed line boundary; any trailing partial line stays
        # unconsumed (offset points at its start), so a record is never double-read.
        result.end_offset = min(offset, n)
        result.end_line = line_number
        return result

    # -- events file ------------------------------------------------------ #

    def _append_events(self, events: list[NormalizedInvocation]) -> None:
        if not events:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event.to_json_dict(), ensure_ascii=True) + "\n")

    def _existing_refs(self) -> set[str]:
        """Provenance refs already stored (raw parse; the dedup/crash-window guard)."""
        if not self.events_path.exists():
            return set()
        refs: set[str] = set()
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            provenance = cast("dict[str, Any]", json.loads(line)).get("provenance", {})
            refs.add(f"{provenance.get('source_relpath')}@{provenance.get('line_number')}")
        return refs

    def _drop_source_events(self, relpath: str) -> None:
        """Remove events derived from ``relpath`` (the rebuild path, plan sec. 6)."""
        if not self.events_path.exists():
            return
        kept: list[str] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = cast("dict[str, Any]", json.loads(line))
            if obj.get("provenance", {}).get("source_relpath") != relpath:
                kept.append(line)
        self.events_path.write_text("".join(entry + "\n" for entry in kept), encoding="utf-8")

    def _count_events(self) -> int:
        if not self.events_path.exists():
            return 0
        return sum(
            1 for line in self.events_path.read_text(encoding="utf-8").splitlines() if line.strip()
        )

    # -- checkpoint ------------------------------------------------------- #

    def _overlap_ok(self, data: bytes, prior: _SourceCheckpoint) -> bool:
        if len(data) < prior.byte_offset:
            return False  # source shrank -> truncation
        return _sha256_hex(data[: prior.byte_offset]) == prior.overlap_hash

    def _read_checkpoint(self) -> dict[str, _SourceCheckpoint]:
        if not self.checkpoint_path.exists():
            return {}
        obj = cast("dict[str, Any]", json.loads(self.checkpoint_path.read_text(encoding="utf-8")))
        check_schema_version(int(obj.get("schema_version", CURRENT_SCHEMA_VERSION)))
        sources = cast("dict[str, Any]", obj.get("sources", {}))
        return {relpath: _SourceCheckpoint.from_json(entry) for relpath, entry in sources.items()}

    def _write_checkpoint(self, checkpoint: dict[str, _SourceCheckpoint]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "sources": {relpath: cp.to_json() for relpath, cp in checkpoint.items()},
        }
        self.checkpoint_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
        )
