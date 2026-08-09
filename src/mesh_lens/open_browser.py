"""Testable local-file browser opener for the mesh-lens static report surfaces.

The browser is deliberately a final presentation step: the caller renders a known
local HTML artifact, then this module asks the operating system's registered browser
to open that *file path*. On Windows it uses PowerShell ``Start-Process -FilePath`` and
waits only for that short-lived PowerShell handshake, never for the browser itself. A
spawn failure, non-zero handshake, or timeout is returned to the CLI instead of being
silently presented as success.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from webbrowser import open as webbrowser_open

#: Bound the PowerShell Start-Process acknowledgement, not the browser lifetime.
WINDOWS_OPEN_HANDSHAKE_SECONDS = 5.0


@dataclass(frozen=True)
class BrowserOpenResult:
    """The observable result of asking the host platform to open one local file."""

    opened: bool
    uri: str
    message: str


def _powershell_script(path: Path) -> str:
    """Build a literal, injection-safe ``Start-Process`` acknowledgement script.

    A Windows path is data inside a PowerShell single-quoted literal. Doubling a quote
    is PowerShell's literal escape, so punctuation in a project directory cannot turn
    into executable script. The generated page has already been checked as a regular
    file by :func:`open_local_file` before this script is created.
    """
    path_text = str(path)
    if "\r" in path_text or "\n" in path_text:
        raise ValueError("browser artifact path contains a control character")
    literal = path_text.replace("'", "''")
    return (
        "$ErrorActionPreference = 'Stop'; "
        f"Start-Process -FilePath '{literal}' -ErrorAction Stop; "
        "[Console]::Out.WriteLine('mesh-lens browser launch acknowledged')"
    )


def _open_windows(path: Path, uri: str) -> BrowserOpenResult:
    """Open via a bounded PowerShell handshake, preserving asynchronous launch failures."""
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        _powershell_script(path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=WINDOWS_OPEN_HANDSHAKE_SECONDS,
        )
    except (OSError, ValueError) as exc:
        return BrowserOpenResult(
            opened=False,
            uri=uri,
            message=f"PowerShell browser handshake could not start: {type(exc).__name__}: {exc}",
        )
    except subprocess.TimeoutExpired:
        return BrowserOpenResult(
            opened=False,
            uri=uri,
            message=(
                "PowerShell browser handshake timed out after "
                f"{WINDOWS_OPEN_HANDSHAKE_SECONDS:g}s; browser launch is not claimed"
            ),
        )

    if completed.returncode != 0:
        diagnostic = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        return BrowserOpenResult(
            opened=False,
            uri=uri,
            message=(
                "PowerShell Start-Process browser handshake failed "
                f"(exit {completed.returncode}): {diagnostic}"
            ),
        )
    return BrowserOpenResult(
        opened=True,
        uri=uri,
        message="PowerShell Start-Process browser handshake acknowledged",
    )


def _open_non_windows(uri: str) -> BrowserOpenResult:
    """Use the stdlib platform opener outside Windows, retaining the visible failure seam."""
    try:
        opened = webbrowser_open(uri)
    except OSError as exc:
        return BrowserOpenResult(
            opened=False,
            uri=uri,
            message=f"platform browser opener raised {type(exc).__name__}: {exc}",
        )
    if not opened:
        return BrowserOpenResult(
            opened=False,
            uri=uri,
            message="platform browser opener reported no available browser",
        )
    return BrowserOpenResult(opened=True, uri=uri, message="opened with the platform browser")


def open_local_file(path: Path) -> BrowserOpenResult:
    """Open an existing rendered HTML artifact and visibly report a failed handshake."""
    resolved = path.resolve()
    uri = resolved.as_uri()
    if not resolved.is_file():
        return BrowserOpenResult(
            opened=False,
            uri=uri,
            message=f"rendered browser artifact does not exist: {resolved}",
        )
    if sys.platform == "win32":
        return _open_windows(resolved, uri)
    return _open_non_windows(uri)
