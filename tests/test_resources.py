"""Tests for MCP resources."""

from __future__ import annotations

from unittest.mock import MagicMock

from app import resources


class TestResourcesRegistration:
    def test_register_does_not_raise(self):
        mcp = MagicMock()
        resources.register(mcp)
        assert mcp.resource.called
