"""Auth0 verification wrapper for card-fraud-mcp-gateway.

Checks that all required Auth0 resources exist and are correctly configured.
Read-only — makes no changes.

Usage:
    uv run auth0-verify
    uv run auth0-verify --verbose
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_DOPPLER_PROJECT = "card-fraud-mcp-gateway"
_SCRIPT = Path(__file__).parent.parent / "scripts" / "verify_auth0.py"


def _doppler_run(config: str, cmd: list[str]) -> int:
    full = ["doppler", "run", "--project", _DOPPLER_PROJECT, "--config", config, "--"] + cmd
    return subprocess.run(full, check=False).returncode  # nosec


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify Auth0 resources for the MCP gateway (read-only)."
    )
    parser.add_argument(
        "--config",
        default="local",
        choices=["local", "test", "prod"],
        help="Doppler config to use (default: local)",
    )
    parser.add_argument(
        "--no-doppler",
        action="store_true",
        help="Run without Doppler (env vars must already be set)",
    )
    parser.add_argument("--verbose", action="store_true", help="Print full resource details")
    args = parser.parse_args()

    script_args: list[str] = []
    if args.verbose:
        script_args.append("--verbose")

    cmd = [sys.executable, str(_SCRIPT), *script_args]

    if args.no_doppler:
        sys.exit(subprocess.run(cmd, check=False).returncode)  # nosec

    os.environ["ENV_FILE"] = ""
    sys.exit(_doppler_run(args.config, cmd))
