"""Integration tests through the production entry point (mesh_lens.cli:main).

Per the workspace code-quality rule, a new module invoked from production code
gets an integration test that exercises the real caller end-to-end -- here the
CLI, which is what the installed ``mesh-lens`` console script runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mesh_lens.cli import main


def test_inventory_command_runs(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["inventory"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Producer fields" in out
    assert "Outcome-artifact classes" in out
    assert "all outcome classes stay unjoined" in out


def test_inventory_command_with_real_stream(
    real_stream: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["inventory", "--telemetry", str(real_stream)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Live telemetry stream" in out
    assert "records: 2" in out
    assert "matches pinned contract: True" in out


def test_compare_requires_both_selectors(capsys: pytest.CaptureFixture[str]) -> None:
    # Step 5 'compare' is now built; bare invocation errors for the required selectors.
    with pytest.raises(SystemExit) as exc:
        main(["compare"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--a" in err and "--b" in err


def test_ingest_command_normalizes_the_real_fixture(
    real_stream: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    rc = main(["ingest", "--source", str(real_stream), "--store", str(store_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ingested this run: 2" in out
    assert "skillmesh-v1=2" in out
    assert (store_dir / "events.jsonl").exists()
    assert (store_dir / "checkpoint.json").exists()


def test_ingest_command_on_absent_source_is_graceful(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    absent = tmp_path / "nope.jsonl"
    rc = main(["ingest", "--source", str(absent), "--store", str(tmp_path / "store")])
    out = capsys.readouterr().out
    assert rc == 0  # graceful, not an error
    assert "absent" in out
    assert "ingested this run: 0" in out


def test_report_command_renders_from_the_store(
    real_stream: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    out_dir = tmp_path / "out"
    assert main(["ingest", "--source", str(real_stream), "--store", str(store_dir)]) == 0
    capsys.readouterr()  # drop ingest output

    rc = main(["report", "--store", str(store_dir), "--out", str(out_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert (out_dir / "report.json").exists()
    assert (out_dir / "report.html").exists()
    assert "events: 2 total" in out
    assert "UNJOINED" in out
    # The real stream is skillmesh-v1 stub records: tokens/cost are placeholder.
    assert "placeholder; never a fabricated 0" in out


def test_report_command_json_is_honest_and_versioned(
    real_stream: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    out_dir = tmp_path / "out"
    main(["ingest", "--source", str(real_stream), "--store", str(store_dir)])
    main(["report", "--store", str(store_dir), "--out", str(out_dir), "--format", "json"])
    obj = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert obj["schema_version"] == 1
    # Two single-record skillmesh-v1 cohorts (models 'gpt-5.6-sol' and 'claude').
    assert len(obj["comparable_cohorts"]) == 2
    for cohort in obj["comparable_cohorts"]:
        assert cohort["tokens_in"]["status"] == "unavailable"
        assert cohort["tokens_in"]["sum"] is None
        assert cohort["latency_ms"]["status"] == "measured"


def test_report_command_on_empty_store_is_graceful(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["report", "--store", str(tmp_path / "empty"), "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "events: 0 total" in out


def test_compare_command_refuses_on_real_data(
    real_stream: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # End-to-end through the production CLI: the real 2-record stream is undersized,
    # so the honest compare REFUSES a directional verdict (never fabricates a winner).
    store_dir = tmp_path / "store"
    assert main(["ingest", "--source", str(real_stream), "--store", str(store_dir)]) == 0
    capsys.readouterr()

    rc = main(
        [
            "compare",
            "--store",
            str(store_dir),
            "--a",
            "model=gpt-5.6-sol",
            "--b",
            "model=claude",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "VERDICT: REFUSED" in out
    assert "insufficient sample" in out


def test_compare_command_valid_path_writes_artifacts(
    compare_cohorts_stream: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    out_dir = tmp_path / "out"
    assert main(["ingest", "--source", str(compare_cohorts_stream), "--store", str(store_dir)]) == 0
    capsys.readouterr()

    rc = main(
        [
            "compare",
            "--store",
            str(store_dir),
            "--a",
            "skill=repo-sync,model=claude",
            "--b",
            "skill=repo-sync,model=gpt-5.5",
            "--out",
            str(out_dir),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "cohort A has the lower measured latency_ms" in out
    assert "CORRELATION, NOT CAUSATION" in out
    assert (out_dir / "comparison.json").exists()
    assert (out_dir / "comparison.html").exists()
    obj = json.loads((out_dir / "comparison.json").read_text(encoding="utf-8"))
    assert obj["refused"] is False
    assert obj["metrics"]


def test_compare_command_placeholder_metric_refuses(
    compare_cohorts_stream: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    main(["ingest", "--source", str(compare_cohorts_stream), "--store", str(store_dir)])
    capsys.readouterr()
    rc = main(
        [
            "compare",
            "--store",
            str(store_dir),
            "--a",
            "skill=repo-sync,model=claude",
            "--b",
            "skill=repo-sync,model=gpt-5.5",
            "--metric",
            "cost_usd",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "VERDICT: REFUSED" in out
    assert "UNAVAILABLE" in out


def test_compare_command_bad_selector_is_graceful(
    compare_cohorts_stream: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    main(["ingest", "--source", str(compare_cohorts_stream), "--store", str(store_dir)])
    capsys.readouterr()
    rc = main(["compare", "--store", str(store_dir), "--a", "skill=nope", "--b", "model=claude"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "compare error" in out


def test_skills_list_and_show_json_preserve_metric_and_event_provenance(
    analyze_golden_stream: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    assert main(["ingest", "--source", str(analyze_golden_stream), "--store", str(store_dir)]) == 0
    capsys.readouterr()

    rc = main(["skills", "list", "--store", str(store_dir), "--json"])
    listed = json.loads(capsys.readouterr().out)
    assert rc == 0
    plan_init = next(summary for summary in listed["skills"] if summary["skill"] == "plan-init")
    assert plan_init["record_refs"] == [
        "analyze_golden.jsonl@1",
        "analyze_golden.jsonl@2",
        "analyze_golden.jsonl@3",
        "analyze_golden.jsonl@4",
    ]
    v1 = next(
        group for group in plan_init["model_mix"] if group["producer_schema"] == "skillmesh-v1"
    )
    assert v1["tokens_in"]["status"] == "unavailable"
    assert v1["tokens_in"]["sum"] is None

    rc = main(["skills", "show", "plan-init", "--store", str(store_dir), "--json"])
    shown = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert shown["recent_events"][0]["source_ref"] == "analyze_golden.jsonl@4"
    assert shown["recent_events"][0]["tokens_in"]["status"] == "placeholder"
    assert shown["outcome_coverage"]["joined_count"] is None
    assert "not a zero-outcome claim" in shown["outcome_coverage"]["note"]


def test_skills_show_unknown_skill_is_visible_error(
    real_stream: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    assert main(["ingest", "--source", str(real_stream), "--store", str(store_dir)]) == 0
    capsys.readouterr()

    rc = main(["skills", "show", "missing-skill", "--store", str(store_dir)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "skill error" in out
    assert "missing-skill" in out


def test_browse_ingests_and_renders_sparse_safe_list_detail_browser(
    analyze_golden_stream: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    out_dir = tmp_path / "browser"

    rc = main(
        [
            "browse",
            "--source",
            str(analyze_golden_stream),
            "--store",
            str(store_dir),
            "--out",
            str(out_dir),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "mesh-lens ingest" in out  # browse intentionally performs the ingest first
    assert "mesh-lens browse" in out
    assert (out_dir / "browser.html").exists()
    payload = json.loads((out_dir / "browser.json").read_text(encoding="utf-8"))
    assert payload["skills"][0]["summary"]["record_refs"]


def test_browse_empty_source_renders_visible_insufficient_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "browser"
    rc = main(
        [
            "browse",
            "--source",
            str(tmp_path / "absent.jsonl"),
            "--store",
            str(tmp_path / "store"),
            "--out",
            str(out_dir),
        ]
    )
    assert rc == 0
    html = (out_dir / "browser.html").read_text(encoding="utf-8")
    assert "No skill events are available" in html
    assert "not a zero-activity claim" in html


def test_browse_open_failure_is_visible(
    real_stream: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mesh_lens.open_browser import BrowserOpenResult

    def failed_open(path: Path) -> BrowserOpenResult:
        return BrowserOpenResult(False, path.as_uri(), "test browser refusal")

    monkeypatch.setattr("mesh_lens.cli.open_local_file", failed_open)
    rc = main(
        [
            "browse",
            "--source",
            str(real_stream),
            "--store",
            str(tmp_path / "store"),
            "--out",
            str(tmp_path / "browser"),
            "--open",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 2
    assert "browse error: could not open" in out
    assert "test browser refusal" in out


def test_no_command_errors(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main([])
