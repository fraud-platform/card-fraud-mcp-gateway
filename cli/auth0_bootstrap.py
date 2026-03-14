"""Auth0 bootstrap wrapper for card-fraud-mcp-gateway.

Provisions all Auth0 resources required by the gateway (API, scopes, M2M client).
Safe to re-run — setup_auth0.py is fully idempotent.

Usage:
    uv run auth0-bootstrap --yes --verbose
    uv run auth0-bootstrap --yes --verbose --config test
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_DOPPLER_PROJECT = "card-fraud-mcp-gateway"
_SCRIPT = Path(__file__).parent.parent / "scripts" / "setup_auth0.py"


def _doppler_run(config: str, cmd: list[str]) -> int:
    full = ["doppler", "run", "--project", _DOPPLER_PROJECT, "--config", config, "--"] + cmd
    return subprocess.run(full, check=False).returncode  # nosec


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap Auth0 resources for the MCP gateway (idempotent)."
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
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--verbose", action="store_true", help="Print created/updated resource IDs")
    args = parser.parse_args()

    script_args: list[str] = []
    if args.yes:
        script_args.append("--yes")
    if args.verbose:
        script_args.append("--verbose")

    cmd = [sys.executable, str(_SCRIPT), *script_args]

    if args.no_doppler:
        sys.exit(subprocess.run(cmd, check=False).returncode)  # nosec

    os.environ["ENV_FILE"] = ""
    sys.exit(_doppler_run(args.config, cmd))
