"""Tests for scope-based authorization policy."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.security.auth import CallerIdentity
from app.security.policy import require_scope


@pytest.mark.asyncio
async def test_scope_allowed():
    caller = CallerIdentity(sub="test", scopes=frozenset({"fraud.db.read"}))

    @require_scope("fraud.db.read")
    async def allowed_tool():
        return "ok"

    with patch("app.security.policy.get_caller", return_value=caller):
        result = await allowed_tool()
        assert result == "ok"


@pytest.mark.asyncio
async def test_scope_denied():
    caller = CallerIdentity(sub="test", scopes=frozenset({"fraud.redis.read"}))

    @require_scope("fraud.db.read")
    async def denied_tool():
        return "should not reach"

    with (
        patch("app.security.policy.get_caller", return_value=caller),
        pytest.raises(PermissionError, match="fraud.db.read"),
    ):
        await denied_tool()


@pytest.mark.asyncio
async def test_multiple_scopes():
    caller = CallerIdentity(
        sub="test",
        scopes=frozenset({"fraud.db.read", "fraud.redis.read", "fraud.kafka.read"}),
    )

    @require_scope("fraud.redis.read")
    async def redis_tool():
        return "redis"

    @require_scope("fraud.kafka.read")
    async def kafka_tool():
        return "kafka"

    with patch("app.security.policy.get_caller", return_value=caller):
        assert await redis_tool() == "redis"
        assert await kafka_tool() == "kafka"


@pytest.mark.asyncio
async def test_empty_scopes_denied():
    caller = CallerIdentity(sub="test", scopes=frozenset())

    @require_scope("fraud.db.read")
    async def tool():
        return "nope"

    with (
        patch("app.security.policy.get_caller", return_value=caller),
        pytest.raises(PermissionError),
    ):
        await tool()


@pytest.mark.asyncio
async def test_denied_request_is_audited():
    """When scope check fails, an audit warning is emitted before the exception."""
    caller = CallerIdentity(
        sub="test-user",
        scopes=frozenset({"fraud.redis.read"}),
        client_id="test-client",
    )

    @require_scope("fraud.db.read", domain="db", tool_name="postgres.query_readonly")
    async def my_tool():
        return "unreachable"

    with (
        patch("app.security.policy.get_caller", return_value=caller),
        patch("app.security.policy._audit_logger") as mock_audit,
        pytest.raises(PermissionError, match="fraud.db.read"),
    ):
        await my_tool()

    mock_audit.warning.assert_called_once()
    call_kwargs = mock_audit.warning.call_args
    # First positional arg is the event name
    assert call_kwargs[0][0] == "authorization_denied"
    assert call_kwargs[1]["tool"] == "postgres.query_readonly"
    assert call_kwargs[1]["required_scope"] == "fraud.db.read"
    assert call_kwargs[1]["caller_sub"] == "test-user"
