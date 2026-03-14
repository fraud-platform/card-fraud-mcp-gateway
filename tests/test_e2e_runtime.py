"""End-to-end runtime tests for the MCP gateway — exercises the full HTTP + MCP protocol stack."""

from __future__ import annotations

import json
import re

import pytest
from starlette.testclient import TestClient


def parse_sse(text: str) -> dict | None:
    """Extract JSON payload from a Server-Sent Events response."""
    m = re.search(r"^data: (.+)$", text, re.MULTILINE)
    return json.loads(m.group(1)) if m else None


@pytest.fixture(scope="module")
def client():
    from app.main import create_app

    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="module")
def mcp_session(client):
    """Establish a full MCP session (initialize + notifications/initialized) and return helpers."""
    r = client.post(
        "/mcp",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "e2e-test", "version": "1.0"},
            },
        },
    )
    assert r.status_code == 200, f"initialize failed: {r.text}"
    session_id = r.headers.get("mcp-session-id", "")
    assert session_id, "No mcp-session-id in initialize response"

    # Send initialized notification
    client.post(
        "/mcp",
        headers={"Content-Type": "application/json", "mcp-session-id": session_id},
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    )

    _counter = [100]

    def call(method: str, params: dict | None = None) -> tuple[int, dict | None]:
        _counter[0] += 1
        r2 = client.post(
            "/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "mcp-session-id": session_id,
            },
            json={"jsonrpc": "2.0", "id": _counter[0], "method": method, "params": params or {}},
        )
        return r2.status_code, parse_sse(r2.text)

    return call, session_id


# ── HTTP Layer ──────────────────────────────────────────────────────────────


class TestHttpEndpoints:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert r.json()["service"] == "card-fraud-mcp-gateway"

    def test_ready_all_not_configured(self, client):
        r = client.get("/ready")
        assert r.status_code == 200
        data = r.json()
        assert data["ready"] is True
        assert all(v == "not_configured" for v in data["backends"].values())

    def test_catalog_counts(self, client):
        r = client.get("/catalog")
        assert r.status_code == 200
        d = r.json()
        assert d["tool_count"] == 22
        assert d["resource_count"] == 7
        assert d["prompt_count"] == 5

    def test_catalog_all_tool_names(self, client):
        r = client.get("/catalog")
        names = {t["tool"] for t in r.json()["tools"]}
        expected = {
            "kafka.consumer_lag",
            "kafka.describe_topic",
            "kafka.list_topics",
            "kafka.peek_messages",
            "ops.get_investigation_context",
            "ops.run_investigation",
            "platform.inventory",
            "platform.ownership_summary",
            "platform.service_health",
            "platform.service_status",
            "postgres.describe_table",
            "postgres.list_schemas",
            "postgres.list_tables",
            "postgres.query_readonly",
            "redis.get_key",
            "redis.scan_prefix",
            "redis.ttl",
            "redis.type",
            "s3.get_object",
            "s3.head_object",
            "s3.list_buckets",
            "s3.list_objects",
        }
        assert names == expected

    def test_security_headers(self, client):
        r = client.get("/health")
        h = dict(r.headers)
        assert h.get("x-frame-options") == "DENY"
        assert h.get("x-content-type-options") == "nosniff"
        assert h.get("cache-control") == "no-store"
        assert h.get("referrer-policy") == "no-referrer"
        assert "max-age=63072000" in h.get("strict-transport-security", "")

    def test_health_skips_auth(self, client):
        r = client.get("/health", headers={"Authorization": "Bearer garbage"})
        assert r.status_code == 200

    def test_request_too_large_rejected(self, client):
        big = json.dumps({"data": "x" * (1024 * 1024 + 100)})
        r = client.post("/mcp", content=big, headers={"Content-Type": "application/json"})
        assert r.status_code == 413
        assert "request_too_large" in r.json()["error"]


# ── MCP Protocol Layer ──────────────────────────────────────────────────────


class TestMcpSession:
    def test_initialize(self, mcp_session):
        # Already done in fixture — we test the results here
        # The fixture raises AssertionError if initialize failed
        call, session_id = mcp_session
        assert session_id

    def test_tools_list(self, mcp_session):
        call, _ = mcp_session
        sc, data = call("tools/list")
        assert sc == 200
        tools = data["result"]["tools"]
        assert len(tools) == 22

    def test_resources_list(self, mcp_session):
        call, _ = mcp_session
        sc, data = call("resources/list")
        assert sc == 200
        resources = data["result"]["resources"]
        assert len(resources) == 7

    def test_prompts_list(self, mcp_session):
        call, _ = mcp_session
        sc, data = call("prompts/list")
        assert sc == 200
        prompts = data["result"]["prompts"]
        assert len(prompts) == 5

    def test_tool_schemas_have_descriptions(self, mcp_session):
        call, _ = mcp_session
        sc, data = call("tools/list")
        for tool in data["result"]["tools"]:
            assert tool.get("description"), f"Tool {tool['name']} has no description"

    def test_resource_uris_scheme(self, mcp_session):
        call, _ = mcp_session
        sc, data = call("resources/list")
        for res in data["result"]["resources"]:
            assert res["uri"].startswith("fraud://"), f"Resource {res['uri']} has unexpected scheme"


# ── Tool Calls ──────────────────────────────────────────────────────────────


class TestPlatformTools:
    def test_inventory(self, mcp_session):
        call, _ = mcp_session
        sc, data = call("tools/call", {"name": "platform.inventory", "arguments": {}})
        assert sc == 200
        result = data["result"]
        assert not result.get("isError"), f"Unexpected error: {result}"
        content = result["content"][0]["text"]
        assert "services" in content or "fraud" in content.lower()

    def test_ownership_summary(self, mcp_session):
        call, _ = mcp_session
        sc, data = call("tools/call", {"name": "platform.ownership_summary", "arguments": {}})
        assert sc == 200
        assert not data["result"].get("isError")

    def test_service_status(self, mcp_session):
        call, _ = mcp_session
        sc, data = call(
            "tools/call",
            {"name": "platform.service_status", "arguments": {"service_name": "fraud-engine"}},
        )
        assert sc == 200
        assert not data["result"].get("isError")

    def test_service_health(self, mcp_session):
        call, _ = mcp_session
        sc, data = call(
            "tools/call",
            {"name": "platform.service_health", "arguments": {"service_name": "fraud-engine"}},
        )
        assert sc == 200
        assert not data["result"].get("isError")


class TestBackendToolsGracefulError:
    """Tools requiring backends should return structured errors, never 500."""

    def _check_graceful(self, call, tool_name, args=None):
        sc, data = call("tools/call", {"name": tool_name, "arguments": args or {}})
        assert sc == 200, f"{tool_name} returned {sc}"
        assert data is not None, f"{tool_name} returned no data"
        # May be isError=True (graceful tool error) or actual result
        # Either way, should not be a crash

    def test_postgres_list_schemas(self, mcp_session):
        call, _ = mcp_session
        self._check_graceful(call, "postgres.list_schemas")

    def test_postgres_list_tables(self, mcp_session):
        call, _ = mcp_session
        self._check_graceful(call, "postgres.list_tables", {"schema": "public"})

    def test_redis_scan_prefix(self, mcp_session):
        call, _ = mcp_session
        self._check_graceful(call, "redis.scan_prefix", {"prefix": "test:"})

    def test_kafka_list_topics(self, mcp_session):
        call, _ = mcp_session
        self._check_graceful(call, "kafka.list_topics")

    def test_s3_list_buckets(self, mcp_session):
        call, _ = mcp_session
        self._check_graceful(call, "s3.list_buckets")


# ── Prompts ─────────────────────────────────────────────────────────────────


class TestPrompts:
    def test_investigate_transaction(self, mcp_session):
        call, _ = mcp_session
        sc, data = call(
            "prompts/get",
            {
                "name": "investigate-transaction",
                "arguments": {"transaction_id": "txn_abc123"},
            },
        )
        assert sc == 200
        messages = data["result"]["messages"]
        assert len(messages) >= 1
        # Prompt should reference the transaction ID
        all_text = " ".join(
            m.get("content", {}).get("text", "")
            if isinstance(m.get("content"), dict)
            else str(m.get("content", ""))
            for m in messages
        )
        assert "txn_abc123" in all_text

    def test_triage_platform_health(self, mcp_session):
        call, _ = mcp_session
        sc, data = call("prompts/get", {"name": "triage-platform-health", "arguments": {}})
        assert sc == 200
        assert data["result"]["messages"]

    def test_review_consumer_lag(self, mcp_session):
        call, _ = mcp_session
        sc, data = call(
            "prompts/get",
            {
                "name": "review-consumer-lag",
                "arguments": {"consumer_group": "fraud-processor"},
            },
        )
        assert sc == 200


# ── Authorization / Policy ───────────────────────────────────────────────────


class TestPolicyAndSecurity:
    def test_policy_registry_count(self):
        from app.security.policy import get_all_policies

        assert len(get_all_policies()) == 22

    def test_all_tools_have_scopes(self):
        from app.security.policy import get_all_policies

        for name, policy in get_all_policies().items():
            assert policy.scope.startswith("fraud."), f"{name} has bad scope: {policy.scope}"

    def test_scope_matrix(self):
        from app.security.policy import get_all_policies

        policies = get_all_policies()
        expected = {
            "postgres.list_schemas": "fraud.db.read",
            "postgres.list_tables": "fraud.db.read",
            "postgres.describe_table": "fraud.db.read",
            "postgres.query_readonly": "fraud.db.read",
            "redis.scan_prefix": "fraud.redis.read",
            "redis.get_key": "fraud.redis.read",
            "redis.ttl": "fraud.redis.read",
            "redis.type": "fraud.redis.read",
            "kafka.list_topics": "fraud.kafka.read",
            "kafka.describe_topic": "fraud.kafka.read",
            "kafka.consumer_lag": "fraud.kafka.read",
            "kafka.peek_messages": "fraud.kafka.read",
            "s3.list_buckets": "fraud.storage.read",
            "s3.list_objects": "fraud.storage.read",
            "s3.head_object": "fraud.storage.read",
            "s3.get_object": "fraud.storage.read",
            "platform.inventory": "fraud.platform.read",
            "platform.ownership_summary": "fraud.platform.read",
            "platform.service_status": "fraud.platform.read",
            "platform.service_health": "fraud.platform.read",
            "ops.get_investigation_context": "fraud.ops.investigation.read",
            "ops.run_investigation": "fraud.ops.investigation.run",
        }
        for tool, scope in expected.items():
            assert policies[tool].scope == scope, (
                f"{tool}: got {policies[tool].scope}, expected {scope}"
            )

    def test_all_tools_read_only_except_run_investigation(self):
        from app.security.policy import get_all_policies

        policies = get_all_policies()
        non_ro = [
            n for n, p in policies.items() if not p.read_only and n != "ops.run_investigation"
        ]
        assert non_ro == [], f"Unexpected RW tools: {non_ro}"

    def test_ops_run_investigation_is_rw(self):
        from app.security.policy import get_all_policies

        assert get_all_policies()["ops.run_investigation"].read_only is False

    def test_scope_enforcement_denies_wrong_scope(self):
        """Caller with only db.read scope must not be able to call kafka tools."""
        from app.security.auth import CallerIdentity, set_caller
        from app.security.policy import _check_scope

        readonly_db_caller = CallerIdentity(
            sub="test-user",
            scopes=frozenset({"fraud.db.read"}),
        )
        token = set_caller(readonly_db_caller)
        try:
            with pytest.raises(PermissionError):
                _check_scope("fraud.kafka.read", domain="kafka", tool="kafka.list_topics")
        finally:
            from app.security.auth import _auth_context

            _auth_context.reset(token)

    def test_scope_enforcement_allows_correct_scope(self):
        from app.security.auth import CallerIdentity, set_caller
        from app.security.policy import _check_scope

        kafka_caller = CallerIdentity(
            sub="test-user",
            scopes=frozenset({"fraud.kafka.read"}),
        )
        token = set_caller(kafka_caller)
        try:
            # Should not raise
            _check_scope("fraud.kafka.read", domain="kafka", tool="kafka.list_topics")
        finally:
            from app.security.auth import _auth_context

            _auth_context.reset(token)
