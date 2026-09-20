"""Tests for the uv-to-Snyk compatibility command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import call

from cli import snyk_scan


def test_snyk_scan_uses_temporary_frozen_export(monkeypatch, tmp_path: Path):
    run_calls = []
    scan_calls = []

    monkeypatch.setattr(
        snyk_scan.shutil,
        "which",
        lambda command: {"uv": "uv-bin", "snyk": "snyk-bin"}[command],
    )
    monkeypatch.setattr(
        snyk_scan.tempfile,
        "TemporaryDirectory",
        lambda **_kwargs: _TemporaryDirectory(tmp_path),
    )
    monkeypatch.setattr(
        snyk_scan.subprocess, "run", lambda *args, **kwargs: run_calls.append(call(*args, **kwargs))
    )
    monkeypatch.setattr(
        snyk_scan,
        "_run_snyk",
        lambda snyk, requirements: scan_calls.append((snyk, requirements)),
    )

    snyk_scan.main()

    requirements = tmp_path / "requirements.txt"
    assert run_calls == [
        call(
            [
                "uv-bin",
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
    ]
    assert scan_calls == [("snyk-bin", requirements)]


class _TemporaryDirectory:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self) -> str:
        return str(self.path)

    def __exit__(self, *_args) -> None:
        return None
