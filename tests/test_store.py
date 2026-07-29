"""Tests for the append-only store + per-source byte-offset checkpoint (plan sec. 6).

Covers the measurement-validity invariants the deep review hammers:
  * idempotent re-ingest (twice -> no duplicates; append -> only the new record;
    truncate/rotate -> rebuild, never silent duplicates or corruption);
  * malformed rows diagnose without aborting siblings;
  * absent/empty stream completes with zero records, never an error;
  * provenance ref + content_hash are stored on every record;
  * the ``unknown`` cohort is never merged with ``skillmesh-v1``;
  * a stored record newer than this build is refused on read.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mesh_lens.inventory import PRODUCER_SCHEMA_ID
from mesh_lens.models import MetricStatus, SchemaVersionError
from mesh_lens.store import Store

_PINNED_ROW = {
    "timestamp": "2026-07-24T15:13:27Z",
    "skill": "plan-init",
    "model": "claude",
    "tokens_in": 0,
    "tokens_out": 0,
    "latency_ms": 4,
    "cost_usd": 0,
    "verdict": "stub",
}


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Real fixture round-trip (DONE-when: real + fixture rows round-trip)
# --------------------------------------------------------------------------- #


def test_real_fixture_round_trips_through_the_store(real_stream: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    report = store.ingest_source(real_stream)
    assert report.source_present is True
    assert report.ingested == 2
    assert report.by_producer_schema == {PRODUCER_SCHEMA_ID: 2}

    events = store.read_events()
    assert len(events) == 2
    for event in events:
        assert event.producer_schema == PRODUCER_SCHEMA_ID
        assert event.verdict == "stub"
        assert event.latency_ms is not None and event.latency_ms > 0
        # Producer never measures tokens/cost -> PLACEHOLDER (not measured, not missing).
        assert event.tokens_in.status is MetricStatus.PLACEHOLDER
        assert event.cost_usd.status is MetricStatus.PLACEHOLDER
        assert event.tokens_in.is_measured is False
        assert event.tokens_in.measured_value() is None


def test_stored_records_carry_provenance_ref_and_content_hash(
    real_stream: Path, tmp_path: Path
) -> None:
    store = Store(tmp_path / "store")
    store.ingest_source(real_stream)  # default relpath == source filename
    events = store.read_events()
    refs = [e.provenance.ref for e in events]
    assert refs == ["invocations.real.jsonl@1", "invocations.real.jsonl@2"]

    raw_lines = [
        ln.rstrip("\r") for ln in real_stream.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    for event, raw in zip(events, raw_lines, strict=True):
        assert len(event.provenance.content_hash) == 64
        assert event.provenance.content_hash == hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Idempotency: twice / append / truncate / rotate
# --------------------------------------------------------------------------- #


def test_ingest_twice_produces_no_duplicates(real_stream: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    first = store.ingest_source(real_stream)
    second = store.ingest_source(real_stream)
    assert first.ingested == 2
    assert second.ingested == 0  # checkpoint skips already-consumed bytes
    assert second.rebuilt is False
    assert store._count_events() == 2  # noqa: SLF001 - store internal count is the invariant
    assert len(store.read_events()) == 2


def test_append_new_line_adds_only_the_new_record(real_stream: Path, tmp_path: Path) -> None:
    src = tmp_path / "invocations.jsonl"
    src.write_bytes(real_stream.read_bytes())  # preserve the real CRLF bytes
    store = Store(tmp_path / "store")
    assert store.ingest_source(src).ingested == 2

    with src.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({**_PINNED_ROW, "model": "gpt-5.6-sol"}) + "\n")

    report = store.ingest_source(src)
    assert report.ingested == 1  # ONLY the appended row
    assert report.rebuilt is False
    events = store.read_events()
    assert len(events) == 3
    assert events[-1].provenance.ref == "invocations.jsonl@3"  # line numbering continues
    assert events[-1].model == "gpt-5.6-sol"


def test_truncation_triggers_rebuild(real_stream: Path, tmp_path: Path) -> None:
    src = tmp_path / "invocations.jsonl"
    src.write_bytes(real_stream.read_bytes())
    store = Store(tmp_path / "store")
    store.ingest_source(src)
    assert store._count_events() == 2  # noqa: SLF001

    # Rotate: replace with a shorter, different single-record file.
    _write_jsonl(src, [{**_PINNED_ROW, "skill": "repo-sync"}])
    report = store.ingest_source(src)
    assert report.rebuilt is True
    assert report.ingested == 1
    events = store.read_events()
    assert len(events) == 1  # old 2 dropped, no duplicates, no corruption
    assert events[0].skill == "repo-sync"
    assert events[0].provenance.ref == "invocations.jsonl@1"  # numbering reset on rebuild


def test_content_change_in_overlap_triggers_rebuild(tmp_path: Path) -> None:
    src = tmp_path / "invocations.jsonl"
    _write_jsonl(src, [{**_PINNED_ROW, "skill": "a"}, {**_PINNED_ROW, "skill": "b"}])
    store = Store(tmp_path / "store")
    store.ingest_source(src)

    # Same-or-longer file but the FIRST (already-consumed) line changed -> hash
    # mismatch on the overlap window, even though nothing was truncated.
    _write_jsonl(
        src,
        [
            {**_PINNED_ROW, "skill": "CHANGED"},
            {**_PINNED_ROW, "skill": "b"},
            {**_PINNED_ROW, "skill": "c"},
        ],
    )
    report = store.ingest_source(src)
    assert report.rebuilt is True
    skills = sorted(e.skill for e in store.read_events() if e.skill is not None)
    assert skills == ["CHANGED", "b", "c"]  # rebuilt from the new file, not merged


# --------------------------------------------------------------------------- #
# Malformed rows diagnose without aborting siblings
# --------------------------------------------------------------------------- #


def test_malformed_rows_diagnose_without_aborting_siblings(tmp_path: Path) -> None:
    src = tmp_path / "invocations.jsonl"
    src.write_text(
        json.dumps({**_PINNED_ROW, "skill": "first"})
        + "\n"
        + "this is not json at all\n"
        + json.dumps([1, 2, 3])  # valid JSON but not an object
        + "\n"
        + json.dumps({**_PINNED_ROW, "skill": "last"})
        + "\n",
        encoding="utf-8",
    )
    report = Store(tmp_path / "store").ingest_source(src)
    assert report.ingested == 2  # both good siblings survive
    assert len(report.malformed) == 2
    reasons = " ".join(d.reason for d in report.malformed)
    assert "invalid JSON" in reasons
    assert "expected a JSON object" in reasons
    # Diagnostics carry a provenance ref pointing at the offending line.
    assert report.malformed[0].provenance_ref == "invocations.jsonl@2"


# --------------------------------------------------------------------------- #
# Graceful degradation on absent / empty streams
# --------------------------------------------------------------------------- #


def test_empty_stream_ingests_zero_records(empty_stream: Path, tmp_path: Path) -> None:
    report = Store(tmp_path / "store").ingest_source(empty_stream)
    assert report.source_present is True
    assert report.ingested == 0
    assert report.total_events == 0
    assert report.malformed == ()


def test_absent_stream_ingests_zero_records_without_error(
    absent_stream: Path, tmp_path: Path
) -> None:
    report = Store(tmp_path / "store").ingest_source(absent_stream)
    assert report.source_present is False
    assert report.ingested == 0
    assert report.total_events == 0


def test_blank_lines_are_skipped_not_malformed(tmp_path: Path) -> None:
    src = tmp_path / "invocations.jsonl"
    src.write_text(
        json.dumps(_PINNED_ROW) + "\n\n   \n" + json.dumps({**_PINNED_ROW, "skill": "x"}) + "\n",
        encoding="utf-8",
    )
    report = Store(tmp_path / "store").ingest_source(src)
    assert report.ingested == 2
    assert report.skipped_blank == 2
    assert report.malformed == ()


# --------------------------------------------------------------------------- #
# Cohort separation: unknown is never merged with skillmesh-v1
# --------------------------------------------------------------------------- #


def test_unknown_cohort_is_never_merged_with_skillmesh_v1(tmp_path: Path) -> None:
    src = tmp_path / "invocations.jsonl"
    _write_jsonl(
        src,
        [
            dict(_PINNED_ROW),  # exact 8 -> skillmesh-v1
            {**_PINNED_ROW, "schema_version": 2},  # 9 fields -> unknown
        ],
    )
    store = Store(tmp_path / "store")
    report = store.ingest_source(src)
    assert report.by_producer_schema == {PRODUCER_SCHEMA_ID: 1, "unknown": 1}

    comparable = store.comparable_events()
    assert len(comparable) == 1
    assert all(e.producer_schema == PRODUCER_SCHEMA_ID for e in comparable)
    assert len(store.unknown_events()) == 1
    assert len(store.read_events()) == 2  # both stored, but reported separately


# --------------------------------------------------------------------------- #
# Refuse a stored record newer than this build (plan sec. 6)
# --------------------------------------------------------------------------- #


def test_read_events_refuses_newer_schema_version(real_stream: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    store.ingest_source(real_stream)
    # Corrupt one stored record to a future schema version.
    lines = store.events_path.read_text(encoding="utf-8").splitlines()
    poisoned = json.loads(lines[0])
    poisoned["schema_version"] = 999
    lines[0] = json.dumps(poisoned)
    store.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(SchemaVersionError):
        store.read_events()


def test_checkpoint_reader_refuses_newer_schema_version(real_stream: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    store.ingest_source(real_stream)  # writes a v1 checkpoint
    # Poison the checkpoint to a future schema version.
    checkpoint = json.loads(store.checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["schema_version"] = 999
    store.checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    # ingest_source reads the checkpoint first -> must refuse, not silently accept.
    with pytest.raises(SchemaVersionError):
        store.ingest_source(real_stream)


# --------------------------------------------------------------------------- #
# Durability backstop: crash between append and checkpoint write -> no duplicate
# --------------------------------------------------------------------------- #


def test_lost_checkpoint_after_append_does_not_duplicate(real_stream: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    store.ingest_source(real_stream)
    assert store._count_events() == 2  # noqa: SLF001

    # Simulate a crash that landed the append but lost the checkpoint write: roll
    # the checkpoint back to empty so the next ingest re-scans the same bytes.
    store.checkpoint_path.write_text(
        json.dumps({"schema_version": 1, "sources": {}}), encoding="utf-8"
    )
    report = store.ingest_source(real_stream)
    assert report.ingested == 0  # dedup by provenance ref drops the re-scanned rows
    assert store._count_events() == 2  # no duplicate written
    assert len(store.read_events()) == 2
