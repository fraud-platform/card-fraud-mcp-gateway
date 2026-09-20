"""Run Snyk against a temporary pip-compatible export of the uv lockfile."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def _run_snyk(snyk: str, requirements: Path) -> None:
    arguments = [
        "test",
        f"--file={requirements}",
        "--package-manager=pip",
        "--skip-unresolved",
    ]
    if os.name != "nt":
        subprocess.run([snyk, *arguments], check=True)
        return

    # npm's Windows .cmd shim loses absolute --file values when launched by
    # subprocess. Its PowerShell shim preserves them, so pass the path through
    # an environment variable rather than interpolating it into source text.
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        raise SystemExit("PowerShell 7 is required to run Snyk on Windows")
    environment = os.environ.copy()
    environment["CARD_FRAUD_SNYK_MANIFEST"] = str(requirements)
    subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            '& snyk test "--file=$env:CARD_FRAUD_SNYK_MANIFEST" '
            "--package-manager=pip --skip-unresolved",
        ],
        check=True,
        env=environment,
    )


def main() -> None:
    """Export locked dependencies, scan them with Snyk, then remove the export."""
    uv = shutil.which("uv")
    snyk = shutil.which("snyk")
    if uv is None:
        raise SystemExit("uv is required but was not found on PATH")
    if snyk is None:
        raise SystemExit("Snyk CLI is required but was not found on PATH")

    # Snyk resolves pip projects relative to the checkout and can fail for a
    # manifest in the system temp directory on Windows.
    with tempfile.TemporaryDirectory(
        prefix=".snyk-scan-",
        dir=Path.cwd(),
    ) as temp_dir:
        requirements = Path(temp_dir) / "requirements.txt"
        subprocess.run(
            [
                uv,
                "export",
                "--quiet",
                "--frozen",
                "--all-extras",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "--no-hashes",
                "--output-file",
                str(requirements),
            ],
            check=True,
        )
        _run_snyk(snyk, requirements)


if __name__ == "__main__":
    main()
