from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from mesh_lens.inventory import (
    Availability,
    Inventory,
    JoinStrength,
    OutcomeArtifactAudit,
    build_inventory,
)

FIXTURES = Path(__file__).parent / "fixtures"

#: Class id used by the synthetic keyed join-path proof (a hypothetical future
#: run-keyed producer). NOT one of the five real classes.
SYNTHETIC_KEYED_CLASS = "synthetic-keyed"


def strong_key_inventory(class_id: str = SYNTHETIC_KEYED_CLASS) -> Inventory:
    """A synthetic inventory adding ONE ``STRONG_KEY`` outcome class.

    Models the day a producer emits a stable run/session key: the added class is
    classified ``STRONG_KEY`` so the already-built join PATH activates. The real five
    classes are untouched (still no strong key), keeping real-vs-synthetic separate.
    """
    real = build_inventory()
    synthetic = OutcomeArtifactAudit(
        class_id=class_id,
        name="synthetic keyed outcome (future run-keyed producer)",
        exists=True,
        location="tests/fixtures/synthetic_keyed_outcomes.jsonl",
        record_key="run_id",
        join_strength=JoinStrength.STRONG_KEY,
        availability=Availability.PRESENT,
        evidence="synthetic fixture carrying a stable run/session key (join-path proof)",
    )
    return replace(real, outcome_artifacts=(*real.outcome_artifacts, synthetic))


@pytest.fixture
def real_stream() -> Path:
    """Byte-identical frozen copy of the real invocations.jsonl (2 stub records)."""
    return FIXTURES / "invocations.real.jsonl"


@pytest.fixture
def empty_stream() -> Path:
    """Zero-byte telemetry stream."""
    return FIXTURES / "invocations.empty.jsonl"


@pytest.fixture
def absent_stream(tmp_path: Path) -> Path:
    """A path that does not exist."""
    return tmp_path / "does_not_exist.jsonl"


@pytest.fixture
def build_step_report_sample() -> Path:
    """Real reviewer-report header (verdict + finding counts). Outcome class 1."""
    return FIXTURES / "build_step_report.sample.md"


@pytest.fixture
def gh_issues_sample() -> Path:
    """Real `gh issue list` JSON (#1-#12). Outcome class 2."""
    return FIXTURES / "gh_issues.sample.json"


@pytest.fixture
def git_log_sample() -> Path:
    """Real mesh-lens `git log` TSV (sha, author-date, subject). Outcome class 3."""
    return FIXTURES / "git_log.sample.tsv"


@pytest.fixture
def plan_status_sample() -> Path:
    """Real plan Build-Steps section: 2 `**Status:** DONE` markers, Steps 3-6 unmarked. Class 4."""
    return FIXTURES / "plan_status.sample.md"


@pytest.fixture
def skill_iterate_sample() -> Path:
    """Real skill-iterate run-log TSV (commit/score/status). Outcome class 5."""
    return FIXTURES / "skill_iterate_results.sample.tsv"


@pytest.fixture
def analyze_golden_stream() -> Path:
    """Synthetic dispatch stream with KNOWN records -> KNOWN aggregates (Step 4 golden).

    Five records: three skillmesh-v1 in cohort (plan-init, claude) -- one with a null
    latency to exercise missingness; one skillmesh-v1 in cohort (repo-sync, gpt-5.5);
    and one unknown-schema record (an extra ``run_id`` field) sharing the (plan-init,
    claude) skill+model but which MUST stay in its own incomparable bucket.
    """
    return FIXTURES / "analyze_golden.jsonl"


@pytest.fixture
def synthetic_keyed_dispatches() -> Path:
    """Synthetic FUTURE run-keyed dispatch stream (each row carries a run_id)."""
    return FIXTURES / "synthetic_keyed_dispatches.jsonl"


@pytest.fixture
def synthetic_keyed_outcomes() -> Path:
    """Synthetic FUTURE run-keyed outcome stream (each row carries a run_id)."""
    return FIXTURES / "synthetic_keyed_outcomes.jsonl"
