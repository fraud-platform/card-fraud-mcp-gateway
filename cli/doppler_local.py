"""Doppler development commands for card-fraud-mcp-gateway.

Usage:
    uv run doppler-local       # Start dev server with Doppler secrets (local config)
    uv run doppler-test        # Run test suite with Doppler secrets (local config)
"""

from __future__ import annotations

import os
import sys

from cli._runner import run

_DOPPLER_PROJECT = "card-fraud-mcp-gateway"


def _doppler_prefix(config: str) -> list[str]:
    return ["doppler", "run", f"--project={_DOPPLER_PROJECT}", f"--config={config}", "--"]


def main() -> None:
    """Run the gateway dev server with Doppler secrets injected (local config)."""
    os.environ.setdefault("APP_ENV", "local")
    os.environ["ENV_FILE"] = ""  # Prevent any .env file loading

    cmd = _doppler_prefix("local") + [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--reload",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        *sys.argv[1:],
    ]
    run(cmd)


def test() -> None:
    """Run the test suite with Doppler secrets injected (local config)."""
    os.environ["ENV_FILE"] = ""

    cmd = _doppler_prefix("local") + [
        sys.executable,
        "-m",
        "pytest",
        "-v",
        *sys.argv[1:],
    ]
    run(cmd)
