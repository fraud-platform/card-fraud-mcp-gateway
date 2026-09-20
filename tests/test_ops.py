"""Tests for ops investigation helpers."""

from __future__ import annotations

import pytest

from app.domains.ops import load_postgres_investigation_context, load_transaction_review
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


@pytest.mark.asyncio
async def test_load_transaction_review_uses_internal_transaction_id(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("app.domains.ops.settings.enforce_allowlists", False)
    pool = FakePool(
        [
            [{"id": "internal-123", "transaction_id": "business-456"}],
            [{"id": "review-789", "transaction_id": "internal-123"}],
        ]
    )

    transaction, reviews = await load_transaction_review(pool, "business-456")

    assert transaction == {"id": "internal-123", "transaction_id": "business-456"}
    assert reviews == [{"id": "review-789", "transaction_id": "internal-123"}]
    assert pool.calls[0][1] == ("business-456",)
    assert pool.calls[1][1] == ("internal-123",)
