from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


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
