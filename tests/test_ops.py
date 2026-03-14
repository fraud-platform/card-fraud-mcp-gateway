"""Tests for ops investigation helpers."""

from __future__ import annotations

import pytest

from app.domains.ops import load_postgres_investigation_context
from tests.conftest import FakePool


@pytest.mark.asyncio
async def test_load_postgres_investigation_context_returns_case_when_only_case_id(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("app.domains.ops.settings.enforce_allowlists", False)
    pool = FakePool([[{"id": "case-123", "status": "open"}]])

    context, sources = await load_postgres_investigation_context(pool, case_id="case-123")

    assert context == {"case": {"id": "case-123", "status": "open"}}
    assert sources == ["postgres:cases"]
