"""Integration tests through the production entry point (mesh_lens.cli:main).

Per the workspace code-quality rule, a new module invoked from production code
gets an integration test that exercises the real caller end-to-end -- here the
CLI, which is what the installed ``mesh-lens`` console script runs.
"""

from __future__ import annotations

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


def test_not_yet_built_commands_are_honest(capsys: pytest.CaptureFixture[str]) -> None:
    for cmd in ("ingest", "report", "compare"):
        with pytest.raises(SystemExit) as exc:
            main([cmd])
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "not built yet" in err


def test_no_command_errors(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main([])
