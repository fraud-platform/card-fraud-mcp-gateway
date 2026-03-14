"""Tests for Postgres raw-query allowlist enforcement."""

from __future__ import annotations

import pytest

from app.domains.postgres import (
    _enforce_query_allowlists,
    _extract_query_relations,
    _extract_tables_from_sql,
)
from tests.conftest import FakePool


@pytest.mark.asyncio
async def test_extract_query_relations_strips_explain_prefix():
    """EXPLAIN prefix is stripped when present."""
    pool = FakePool(
        [{"Plan": {"Node Type": "Seq Scan", "Schema": "fraud", "Relation Name": "transactions"}}]
    )

    # EXPLAIN queries bypass regex extraction and use EXPLAIN
    relations = await _extract_query_relations("EXPLAIN SELECT * FROM fraud.transactions", pool)

    assert relations == {("fraud", "transactions")}
    assert pool.calls == [("EXPLAIN (VERBOSE, FORMAT JSON) SELECT * FROM fraud.transactions", ())]


@pytest.mark.asyncio
async def test_extract_query_relations_simple_select_uses_regex():
    """Simple SELECT queries use regex extraction, avoiding EXPLAIN round-trip."""
    # Simple SELECT with FROM/JOIN patterns - no EXPLAIN call
    relations = _extract_tables_from_sql("SELECT * FROM fraud.transactions WHERE id = $1")

    assert relations == {("fraud", "transactions")}


@pytest.mark.asyncio
async def test_extract_query_relations_complex_query_falls_back_to_explain():
    """Complex queries (CTEs, UNION) fall back to EXPLAIN."""
    pool = FakePool(
        [{"Plan": {"Node Type": "Seq Scan", "Schema": "fraud", "Relation Name": "transactions"}}]
    )

    # WITH (CTE) triggers EXPLAIN fallback
    relations = await _extract_query_relations(
        "WITH tx AS (SELECT * FROM fraud.transactions) SELECT * FROM tx",
        pool,
    )

    assert relations == {("fraud", "transactions")}
    assert len(pool.calls) == 1  # EXPLAIN was called
    assert "EXPLAIN" in pool.calls[0][0]


@pytest.mark.asyncio
async def test_extract_query_relations_preserves_bind_parameters():
    """Bind parameters are preserved when EXPLAIN fallback is used."""
    pool = FakePool(
        [{"Plan": {"Node Type": "Seq Scan", "Schema": "fraud", "Relation Name": "transactions"}}]
    )

    # Complex query triggers EXPLAIN fallback
    await _extract_query_relations(
        "WITH tx AS (SELECT * FROM fraud.transactions WHERE id = $1) SELECT * FROM tx",
        pool,
        ("tx-123",),
    )

    assert pool.calls == [
        (
            "EXPLAIN (VERBOSE, FORMAT JSON) "
            "WITH tx AS (SELECT * FROM fraud.transactions WHERE id = $1) "
            "SELECT * FROM tx",
            ("tx-123",),
        )
    ]


@pytest.mark.asyncio
async def test_enforce_query_allowlists_allows_configured_relations(monkeypatch):
    pool = FakePool(
        [{"Plan": {"Node Type": "Seq Scan", "Schema": "fraud", "Relation Name": "transactions"}}]
    )
    monkeypatch.setattr("app.domains.postgres.settings.pg_allowed_schemas", ["fraud"])
    monkeypatch.setattr("app.domains.postgres.settings.pg_allowed_tables", ["fraud.transactions"])

    await _enforce_query_allowlists("SELECT * FROM fraud.transactions", pool)


@pytest.mark.asyncio
async def test_enforce_query_allowlists_rejects_unapproved_relation(monkeypatch):
    pool = FakePool(
        [{"Plan": {"Node Type": "Seq Scan", "Schema": "secret", "Relation Name": "cards"}}]
    )
    monkeypatch.setattr("app.domains.postgres.settings.pg_allowed_schemas", ["fraud"])
    monkeypatch.setattr("app.domains.postgres.settings.pg_allowed_tables", ["fraud.transactions"])

    with pytest.raises(ValueError, match="allowed"):
        await _enforce_query_allowlists("SELECT * FROM secret.cards", pool)


@pytest.mark.asyncio
async def test_enforce_query_allowlists_rejects_relationless_query(monkeypatch):
    pool = FakePool([{"Plan": {"Node Type": "Result"}}])
    monkeypatch.setattr("app.domains.postgres.settings.pg_allowed_schemas", ["fraud"])
    monkeypatch.setattr("app.domains.postgres.settings.pg_allowed_tables", [])

    with pytest.raises(ValueError, match="must reference allowed tables"):
        await _enforce_query_allowlists("SELECT 1", pool)
