"""Shared test fixtures."""

from __future__ import annotations

import os

import pytest

# Platform-convention local dev auth bypass — same pattern used across all card-fraud-* services.
# Tests run as APP_ENV=local with JWT validation skipped; authenticated as the local-dev identity
# with all fraud.* scopes. Individual tests that need to exercise real auth paths should
# monkeypatch settings.skip_jwt_validation = False directly.
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("SECURITY_SKIP_JWT_VALIDATION", "true")

from app.config import Settings  # noqa: E402


@pytest.fixture
def dev_settings() -> Settings:
    return Settings(
        pg_dsn="",
        redis_url="",
        kafka_brokers="",
        s3_endpoint="",
        platform_api_url="",
    )


class FakePool:
    """Minimal asyncpg-compatible fake used in unit tests."""

    def __init__(self, results):
        self._results = results
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args: object):
        self.calls.append((sql, args))
        return self._results[len(self.calls) - 1]

    async def fetchval(self, sql: str, *parameters: object):
        self.calls.append((sql, parameters))
        return self._results[len(self.calls) - 1]
