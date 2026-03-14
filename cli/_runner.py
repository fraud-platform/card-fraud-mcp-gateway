"""Shared CLI runner helper."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence


def run(cmd: Sequence[str]) -> None:
    """Run a command and propagate its exit code."""
    result = subprocess.run(cmd)
    raise SystemExit(result.returncode)
