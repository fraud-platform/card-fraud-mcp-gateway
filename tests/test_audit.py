"""Tests for audit logging decorator."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.audit import audit_tool
from app.security.auth import CallerIdentity


@pytest.mark.asyncio
async def test_audit_logs_success():
    caller = CallerIdentity(sub="test", client_id="test-client")

    @audit_tool("test_domain")
    async def my_tool(x: int = 0) -> str:
        return "result"

    with (
        patch("app.audit.get_caller", return_value=caller),
        patch("app.audit.logger") as mock_logger,
    ):
        mock_logger.bind.return_value = mock_logger
        result = await my_tool(x=42)
        assert result == "result"
        mock_logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_audit_logs_error():
    caller = CallerIdentity(sub="test", client_id="test-client")

    @audit_tool("test_domain")
    async def failing_tool() -> str:
        raise ValueError("boom")

    with (
        patch("app.audit.get_caller", return_value=caller),
        patch("app.audit.logger") as mock_logger,
    ):
        mock_logger.bind.return_value = mock_logger
        with pytest.raises(ValueError, match="boom"):
            await failing_tool()
        mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_audit_preserves_return_value():
    caller = CallerIdentity(sub="test", client_id="test-client")

    @audit_tool("test_domain")
    async def my_tool() -> list:
        return [1, 2, 3]

    with (
        patch("app.audit.get_caller", return_value=caller),
        patch("app.audit.logger") as mock_logger,
    ):
        mock_logger.bind.return_value = mock_logger
        result = await my_tool()
        assert result == [1, 2, 3]
