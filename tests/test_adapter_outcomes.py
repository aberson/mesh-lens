"""Tests for the Step 3 outcome-artifact adapters (plan sec. 7).

Pins the honesty rules:
  * Every reachable class ingests standalone :class:`NormalizedOutcome` records that
    carry provenance (ref + content_hash) and ``schema_version == 1``.
  * NO real class gets a ``join_key`` -- even a key-shaped value (a commit sha) is
    refused because the Step 1 inventory proves it is not dispatch-correlatable
    (single source of truth). Only a ``STRONG_KEY`` class (synthetic) keeps its key.
  * Missing outcomes are REPORTED, never inferred: an absent source yields zero
    records; the plan adapter ingests only the 2 real ``**Status:** DONE`` markers
    and never fabricates a disposition for the 4 unmarked steps.
"""

from __future__ import annotations

from pathlib import Path

from conftest import SYNTHETIC_KEYED_CLASS, strong_key_inventory
from mesh_lens.adapters.outcomes import (
    _join_key_for,
    ingest_outcome_source,
    parse_keyed_outcomes,
)
from mesh_lens.inventory import OutcomeClass, build_inventory
from mesh_lens.models import CURRENT_SCHEMA_VERSION, NormalizedOutcome


def _fields(record: NormalizedOutcome) -> dict[str, str]:
    return dict(record.fields)


# --------------------------------------------------------------------------- #
# Every adapter: standalone records with provenance + schema_version, join_key None
# --------------------------------------------------------------------------- #


def test_build_step_report_ingests_one_record_with_provenance(
    build_step_report_sample: Path,
) -> None:
    report = ingest_outcome_source(build_step_report_sample, OutcomeClass.BUILD_STEP_REPORT.value)
    assert report.source_present is True
    assert report.count == 1
    rec = report.records[0]
    fields = _fields(rec)
    assert fields["verdict"] == "NEEDS-WORK"
    assert fields["total_findings"] == "7"
    assert fields["reviewed_step"] == "3"
    assert rec.join_key is None  # skill-name-only -> unjoined
    assert rec.schema_version == CURRENT_SCHEMA_VERSION
    assert rec.provenance.ref == "build_step_report.sample.md@3"  # verdict line
    assert len(rec.provenance.content_hash) == 64


def test_gh_issues_ingests_every_issue_no_join_key(gh_issues_sample: Path) -> None:
    report = ingest_outcome_source(gh_issues_sample, OutcomeClass.GH_ISSUE_STATE.value)
    assert report.count == 12
    by_number = {int(_fields(r)["number"]): r for r in report.records}
    assert by_number[9].join_key is None  # no shared key at all
    assert _fields(by_number[9])["state"] == "OPEN"
    assert "Step 3" in _fields(by_number[9])["title"]
    # locator index is the stable issue number
    assert by_number[9].provenance.ref == "gh_issues.sample.json@9"


def test_git_log_refuses_commit_sha_as_dispatch_join_key(git_log_sample: Path) -> None:
    report = ingest_outcome_source(git_log_sample, OutcomeClass.GIT_LOG.value)
    assert report.count == 3
    for rec in report.records:
        # commit sha IS a real key but NOT dispatch-correlatable (timestamp-window
        # only per inventory) -> refused; the record stays unjoined.
        assert rec.join_key is None
        assert len(_fields(rec)["commit"]) > 0
        assert len(rec.provenance.content_hash) == 64


def test_skill_iterate_skips_header_and_refuses_commit_key(skill_iterate_sample: Path) -> None:
    report = ingest_outcome_source(skill_iterate_sample, OutcomeClass.SKILL_ITERATE_LOG.value)
    assert report.count == 3  # header row skipped; baseline + 2 commits
    commits = {_fields(r)["commit"] for r in report.records}
    assert "commit" not in commits  # header not ingested as a record
    assert "6f28e59" in commits
    for rec in report.records:
        assert rec.join_key is None  # skill-name-only -> unjoined


# --------------------------------------------------------------------------- #
# Plan disposition: missing reported, NEVER inferred
# --------------------------------------------------------------------------- #


def test_plan_status_ingests_only_present_markers_not_inferred(plan_status_sample: Path) -> None:
    report = ingest_outcome_source(plan_status_sample, OutcomeClass.PLAN_STATUS.value)
    # Only Steps 1 and 2 carry a **Status:** marker; Steps 3-6 have none.
    assert report.count == 2
    steps = {_fields(r)["step"] for r in report.records}
    assert steps == {"1", "2"}
    # Steps 3-6 disposition is MISSING -> no record fabricated for them.
    assert steps.isdisjoint({"3", "4", "5", "6"})
    for rec in report.records:
        assert _fields(rec)["status"] == "DONE"
        assert rec.join_key is None  # plan markers have no shared dispatch key


def test_absent_source_reports_missing_zero_records(tmp_path: Path) -> None:
    report = ingest_outcome_source(tmp_path / "nope.tsv", OutcomeClass.GIT_LOG.value)
    assert report.source_present is False
    assert report.count == 0
    assert "missing" in report.note and "inferred" in report.note


def test_present_but_unparseable_source_reports_missing(tmp_path: Path) -> None:
    empty = tmp_path / "empty.md"
    empty.write_text("no verdict here\n", encoding="utf-8")
    report = ingest_outcome_source(empty, OutcomeClass.BUILD_STEP_REPORT.value)
    assert report.source_present is True
    assert report.count == 0
    assert "missing" in report.note


# --------------------------------------------------------------------------- #
# The no-bad-join guard is sourced from the inventory (single source of truth)
# --------------------------------------------------------------------------- #


def test_join_key_guard_refuses_candidate_for_ambiguous_class() -> None:
    inv = build_inventory()
    # git-log is TIMESTAMP_WINDOW_ONLY (ambiguous) -> candidate refused.
    assert _join_key_for(OutcomeClass.GIT_LOG.value, inv, "deadbeef") is None
    # skill-iterate is SKILL_NAME_ONLY (ambiguous) -> candidate refused.
    assert _join_key_for(OutcomeClass.SKILL_ITERATE_LOG.value, inv, "cafef00d") is None


def test_join_key_guard_keeps_candidate_only_for_strong_key_class() -> None:
    inv = strong_key_inventory()
    assert _join_key_for(SYNTHETIC_KEYED_CLASS, inv, "run-7f3a2b") == "run-7f3a2b"
    # A real class stays refused even under the synthetic inventory.
    assert _join_key_for(OutcomeClass.GIT_LOG.value, inv, "run-7f3a2b") is None


# --------------------------------------------------------------------------- #
# Keyed (future) adapter: key kept only when the inventory blesses the class
# --------------------------------------------------------------------------- #


def test_keyed_adapter_drops_run_id_under_real_inventory(
    synthetic_keyed_outcomes: Path,
) -> None:
    text = synthetic_keyed_outcomes.read_text(encoding="utf-8")
    # Parsed as a REAL (ambiguous) class -> the run_id is refused, record unjoined.
    records = parse_keyed_outcomes(
        text, "synthetic_keyed_outcomes.jsonl", OutcomeClass.GIT_LOG.value, build_inventory()
    ).records
    assert len(records) == 2
    assert all(r.join_key is None for r in records)


def test_keyed_adapter_preserves_run_id_under_strong_key_inventory(
    synthetic_keyed_outcomes: Path,
) -> None:
    text = synthetic_keyed_outcomes.read_text(encoding="utf-8")
    records = parse_keyed_outcomes(
        text, "synthetic_keyed_outcomes.jsonl", SYNTHETIC_KEYED_CLASS, strong_key_inventory()
    ).records
    keys = {r.join_key for r in records}
    assert keys == {"run-7f3a2b", "run-orphan"}
    for rec in records:
        assert rec.schema_version == CURRENT_SCHEMA_VERSION
        assert len(rec.provenance.content_hash) == 64


# --------------------------------------------------------------------------- #
# Never-raise / graceful degradation: a malformed or non-UTF8 artifact diagnoses
# 0 records (or skips the bad record) and NEVER aborts the class or its siblings.
# --------------------------------------------------------------------------- #


def test_broken_issue_json_diagnoses_zero_records_no_crash(tmp_path: Path) -> None:
    bad = tmp_path / "bad_issues.json"
    bad.write_text('[{"number":1,"state":"OPEN" <<< truncated', encoding="utf-8")
    report = ingest_outcome_source(bad, OutcomeClass.GH_ISSUE_STATE.value)
    assert report.source_present is True
    assert report.count == 0
    assert len(report.malformed) == 1
    assert "invalid issue JSON" in report.malformed[0].reason
    assert "missing" in report.note


def test_non_utf8_artifact_diagnoses_zero_records_no_crash(tmp_path: Path) -> None:
    bad = tmp_path / "bad.tsv"
    # 0xFF is never a valid UTF-8 lead byte -> read raises UnicodeDecodeError, which
    # the shared read must turn into a diagnostic (covers EVERY class).
    bad.write_bytes(b"\xff\xfe binary junk not utf-8\n")
    report = ingest_outcome_source(bad, OutcomeClass.GIT_LOG.value)
    assert report.source_present is True
    assert report.count == 0
    assert len(report.malformed) == 1
    assert "UTF-8" in report.malformed[0].reason
    assert "missing" in report.note


def test_keyed_adapter_skips_bad_line_keeps_good_ones(tmp_path: Path) -> None:
    mixed = tmp_path / "mixed.jsonl"
    mixed.write_text(
        '{"run_id": "run-a", "verdict": "pass"}\n'
        "NOT JSON AT ALL\n"
        '{"run_id": "run-b", "verdict": "fail"}\n',
        encoding="utf-8",
    )
    parsed = parse_keyed_outcomes(
        mixed.read_text(encoding="utf-8"),
        "mixed.jsonl",
        SYNTHETIC_KEYED_CLASS,
        strong_key_inventory(),
    )
    # The two good lines still parse; only the middle line is diagnosed.
    assert {r.join_key for r in parsed.records} == {"run-a", "run-b"}
    assert len(parsed.malformed) == 1
    assert parsed.malformed[0].line_number == 2


def test_one_malformed_class_does_not_abort_the_others(
    tmp_path: Path, git_log_sample: Path
) -> None:
    """A single bad file yields 0 + diagnostic and does NOT prevent sibling classes."""
    bad_issues = tmp_path / "bad_issues.json"
    bad_issues.write_text("this is not json", encoding="utf-8")
    reports = [
        ingest_outcome_source(bad_issues, OutcomeClass.GH_ISSUE_STATE.value),
        ingest_outcome_source(git_log_sample, OutcomeClass.GIT_LOG.value),
    ]
    gh_report, git_report = reports
    assert gh_report.count == 0 and len(gh_report.malformed) == 1  # the bad file
    assert git_report.count == 3 and git_report.malformed == ()  # sibling unaffected
