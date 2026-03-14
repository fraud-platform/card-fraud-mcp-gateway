"""Tests for the MCP server factory — verifies all tools, resources, and prompts register."""

from __future__ import annotations

from app.security.policy import get_all_policies, require_scope
from app.server import create_mcp_server


def test_server_creates_successfully():
    mcp = create_mcp_server()
    assert mcp is not None


def test_all_tools_registered():
    create_mcp_server()
    policies = get_all_policies()
    # 22 tools expected: 4 platform + 4 postgres + 4 redis + 4 kafka + 4 storage + 2 ops
    assert len(policies) == 22, f"Expected 22 tools, got {len(policies)}: {sorted(policies)}"


def test_tool_domains_covered():
    create_mcp_server()
    policies = get_all_policies()
    domains = {p.domain for p in policies.values()}
    expected = {"platform", "postgres", "redis", "kafka", "storage", "ops"}
    assert domains == expected, f"Unexpected domains: {domains}"


def test_all_tools_have_scopes():
    create_mcp_server()
    policies = get_all_policies()
    for name, policy in policies.items():
        assert policy.scope, f"Tool {name} has no scope"
        assert policy.domain, f"Tool {name} has no domain"


def test_readonly_default():
    create_mcp_server()
    policies = get_all_policies()
    rw_tools = {name for name, p in policies.items() if not p.read_only}
    # Only ops.run_investigation should be non-readonly
    assert rw_tools == {"ops.run_investigation"}, f"Unexpected RW tools: {rw_tools}"


def test_server_registration_clears_stale_policies():
    @require_scope("fraud.db.read")
    async def temp_tool():
        return "ok"

    assert "temp_tool" in get_all_policies()

    create_mcp_server()
    policies = get_all_policies()

    assert "temp_tool" not in policies
    assert len(policies) == 22
