"""Tests for the visible-failure, platform browser opening seam (Step 8)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from mesh_lens.open_browser import WINDOWS_OPEN_HANDSHAKE_SECONDS, open_local_file


def test_windows_open_uses_start_process_and_acknowledged_handshake(
    tmp_path: Path, monkeypatch
) -> None:
    page = tmp_path / "browser.html"
    page.write_text("<!doctype html>", encoding="utf-8")
    calls: list[tuple[list[str], float]] = []

    def run(command, **kwargs):
        calls.append((command, kwargs["timeout"]))
        return subprocess.CompletedProcess(command, 0, stdout="acknowledged\n", stderr="")

    monkeypatch.setattr("mesh_lens.open_browser.sys.platform", "win32")
    monkeypatch.setattr("mesh_lens.open_browser.subprocess.run", run)
    result = open_local_file(page)

    assert result.opened is True
    assert result.uri.startswith("file:")
    assert result.message == "PowerShell Start-Process browser handshake acknowledged"
    command, timeout = calls[0]
    assert command[:2] == ["powershell.exe", "-NoProfile"]
    assert "Start-Process -FilePath" in command[-1]
    assert "-ErrorAction Stop" in command[-1]
    assert timeout == WINDOWS_OPEN_HANDSHAKE_SECONDS


def test_windows_open_reports_start_process_failure(tmp_path: Path, monkeypatch) -> None:
    page = tmp_path / "browser.html"
    page.write_text("<!doctype html>", encoding="utf-8")

    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="no browser association")

    monkeypatch.setattr("mesh_lens.open_browser.sys.platform", "win32")
    monkeypatch.setattr("mesh_lens.open_browser.subprocess.run", run)
    result = open_local_file(page)

    assert result.opened is False
    assert "Start-Process browser handshake failed" in result.message
    assert "no browser association" in result.message


def test_windows_open_reports_bounded_handshake_timeout(tmp_path: Path, monkeypatch) -> None:
    page = tmp_path / "browser.html"
    page.write_text("<!doctype html>", encoding="utf-8")

    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("mesh_lens.open_browser.sys.platform", "win32")
    monkeypatch.setattr("mesh_lens.open_browser.subprocess.run", run)
    result = open_local_file(page)

    assert result.opened is False
    assert "timed out" in result.message
    assert "not claimed" in result.message


def test_windows_script_escapes_a_single_quote_in_local_path(tmp_path: Path, monkeypatch) -> None:
    page = tmp_path / "O'Brien.html"
    page.write_text("<!doctype html>", encoding="utf-8")
    captured: list[str] = []

    def run(command, **kwargs):
        captured.append(command[-1])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("mesh_lens.open_browser.sys.platform", "win32")
    monkeypatch.setattr("mesh_lens.open_browser.subprocess.run", run)
    assert open_local_file(page).opened is True
    assert "O''Brien.html" in captured[0]


def test_non_windows_open_reports_platform_failure(tmp_path: Path, monkeypatch) -> None:
    page = tmp_path / "browser.html"
    page.write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr("mesh_lens.open_browser.sys.platform", "linux")
    monkeypatch.setattr("mesh_lens.open_browser.webbrowser_open", lambda uri: False)

    result = open_local_file(page)

    assert result.opened is False
    assert "no available browser" in result.message


def test_open_local_file_reports_missing_artifact(tmp_path: Path) -> None:
    result = open_local_file(tmp_path / "missing.html")
    assert result.opened is False
    assert "does not exist" in result.message
