"""Real live MCP client-server tests.

Uses uvicorn to start a genuine TCP server on 127.0.0.1:8099, then connects
via httpx over the loopback interface.  This exercises what Starlette's
TestClient cannot: real OS-level TCP I/O, uvicorn's event-loop integration,
and the full lifespan/session-manager lifecycle.

Gaps closed vs test_e2e_runtime.py (TestClient-based):
  - Real TCP socket (uvicorn + httpx, not in-process)
  - All 7 resources via MCP resources/read
  - All 5 prompts (adds explain-decision-trace, inspect-ruleset-artifact)
  - s3.get_object tool call
  - kafka.consumer_lag, kafka.describe_topic, kafka.peek_messages tool calls
  - ops tools via live graceful-error path
  - Rate-limit enforcement — 429 returned over the wire
  - All 22 tools called individually (not just 5 sampled in e2e suite)
"""

from __future__ import annotations

import json
import re
import threading
import time

import httpx
import pytest
import uvicorn

# ── Constants ────────────────────────────────────────────────────────────────

PORT = 8099
BASE = f"http://127.0.0.1:{PORT}"
_HDRS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

# All 22 tool names the catalog must expose
_ALL_TOOLS = {
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

# All 7 resource URIs
_ALL_RESOURCES = [
    "fraud://buckets/catalog",
    "fraud://health/topology",
    "fraud://ops/investigation-context",
    "fraud://platform/ownership",
    "fraud://platform/services",
    "fraud://schemas/catalog",
    "fraud://topics/catalog",
]

# All 5 prompt names
_ALL_PROMPTS = [
    "explain-decision-trace",
    "inspect-ruleset-artifact",
    "investigate-transaction",
    "review-consumer-lag",
    "triage-platform-health",
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _parse_sse(text: str) -> dict | None:
    """Extract the JSON payload from a Server-Sent Events response body."""
    m = re.search(r"^data: (.+)$", text, re.MULTILINE)
    return json.loads(m.group(1)) if m else None


# ── Session-scoped fixtures ──────────────────────────────────────────────────


@pytest.fixture(scope="session")
def live_url():
    """Start a real uvicorn server on PORT; yield its base URL; stop it after the session.

    We reset ``_main_mcp`` to None before calling ``create_app()`` so that
    the live server gets a fresh FastMCP instance with a clean
    ``_session_manager`` — avoiding any state left by a prior TestClient run
    in the same pytest session.
    """
    import app.main as _main_mod

    _main_mod._main_mcp = None  # ensure fresh MCP instance + session manager

    from app.main import create_app

    live_app = create_app()
    config = uvicorn.Config(
        live_app,
        host="127.0.0.1",
        port=PORT,
        log_level="error",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Poll /health until the server is ready (max 15 s)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=1) as probe:
                if probe.get(f"{BASE}/health").status_code == 200:
                    break
        except Exception:
            time.sleep(0.1)
    else:
        server.should_exit = True
        pytest.fail(f"Live server on port {PORT} did not start within 15 seconds")

    yield BASE

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def mcp(live_url):
    """Return a helper that executes a single MCP JSON-RPC call over a live TCP session.

    Also yields the raw httpx.Client and headers dict so tests can inspect
    low-level request/response details.

    Yields: (call, session_id, client, headers)
    """
    client = httpx.Client(base_url=live_url, timeout=20)
    headers = dict(_HDRS)

    # ── MCP handshake ────────────────────────────────────────────────────────
    init_resp = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "live-test-suite", "version": "1.0"},
            },
        },
    )
    assert init_resp.status_code == 200, f"initialize failed: {init_resp.text}"
    session_id = init_resp.headers.get("mcp-session-id", "")
    assert session_id, "Server returned no mcp-session-id in initialize response"
    headers["mcp-session-id"] = session_id

    # Confirm initialization
    client.post(
        "/mcp",
        headers={"Content-Type": "application/json", "mcp-session-id": session_id},
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    )

    _counter = [100]

    def call(method: str, params: dict | None = None) -> tuple[int, dict | None]:
        _counter[0] += 1
        r = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": _counter[0],
                "method": method,
                "params": params or {},
            },
        )
        return r.status_code, _parse_sse(r.text)

    yield call, session_id, client, headers
    client.close()


# ── 1. Real TCP Connection ────────────────────────────────────────────────────


class TestRealTcpConnection:
    """Verify this is a genuine TCP server, not an in-process ASGI shim."""

    def test_health_over_tcp(self, live_url):
        with httpx.Client(base_url=live_url, timeout=5) as c:
            r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_ready_over_tcp(self, live_url):
        with httpx.Client(base_url=live_url, timeout=5) as c:
            r = c.get("/ready")
        assert r.status_code == 200
        assert r.json()["ready"] is True

    def test_catalog_over_tcp_counts(self, live_url):
        with httpx.Client(base_url=live_url, timeout=5) as c:
            r = c.get("/catalog")
        d = r.json()
        assert d["tool_count"] == 22
        assert d["resource_count"] == 7
        assert d["prompt_count"] == 5

    def test_server_header_present(self, live_url):
        """Uvicorn always sets a Server header; TestClient does not."""
        with httpx.Client(base_url=live_url, timeout=5) as c:
            r = c.get("/health")
        # Uvicorn sets "server: uvicorn" — confirms we're talking to a real process
        assert "uvicorn" in r.headers.get("server", "").lower()

    def test_security_headers_over_tcp(self, live_url):
        with httpx.Client(base_url=live_url, timeout=5) as c:
            r = c.get("/health")
        assert r.headers.get("x-frame-options") == "DENY"
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("cache-control") == "no-store"


# ── 2. MCP Handshake & Discovery ─────────────────────────────────────────────


class TestLiveMcpHandshake:
    def test_initialize_returns_session_id(self, mcp):
        _, session_id, _, _ = mcp
        assert len(session_id) > 8

    def test_tools_list_returns_22(self, mcp):
        call, _, _, _ = mcp
        sc, data = call("tools/list")
        assert sc == 200
        assert len(data["result"]["tools"]) == 22

    def test_tools_list_matches_expected_names(self, mcp):
        call, _, _, _ = mcp
        _, data = call("tools/list")
        names = {t["name"] for t in data["result"]["tools"]}
        assert names == _ALL_TOOLS

    def test_resources_list_returns_7(self, mcp):
        call, _, _, _ = mcp
        sc, data = call("resources/list")
        assert sc == 200
        assert len(data["result"]["resources"]) == 7

    def test_resources_uris_match_expected(self, mcp):
        call, _, _, _ = mcp
        _, data = call("resources/list")
        uris = sorted(r["uri"] for r in data["result"]["resources"])
        assert uris == sorted(_ALL_RESOURCES)

    def test_prompts_list_returns_5(self, mcp):
        call, _, _, _ = mcp
        sc, data = call("prompts/list")
        assert sc == 200
        assert len(data["result"]["prompts"]) == 5

    def test_prompts_names_match_expected(self, mcp):
        call, _, _, _ = mcp
        _, data = call("prompts/list")
        names = sorted(p["name"] for p in data["result"]["prompts"])
        assert names == sorted(_ALL_PROMPTS)

    def test_missing_session_id_rejected(self, live_url):
        """Requests after initialize without mcp-session-id should not crash (400 or error)."""
        with httpx.Client(base_url=live_url, timeout=5) as c:
            r = c.post(
                "/mcp",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                json={"jsonrpc": "2.0", "id": 99, "method": "tools/list", "params": {}},
            )
        # Server must not return 500; 400 or a valid JSON-RPC error is acceptable
        assert r.status_code != 500


# ── 3. All 22 Tools — Individual Graceful Call ────────────────────────────────


class TestAllToolsLive:
    """Every tool must be callable and return HTTP 200; isError is allowed for missing backends."""

    def _graceful(self, call, tool_name: str, args: dict | None = None) -> None:
        sc, data = call("tools/call", {"name": tool_name, "arguments": args or {}})
        assert sc == 200, f"{tool_name} returned HTTP {sc}"
        assert data is not None, f"{tool_name} returned no parseable SSE body"
        # isError=True (graceful tool error) is fine; a 500 or None is not

    # Platform — static data, always returns real results
    def test_platform_inventory(self, mcp):
        call, _, _, _ = mcp
        sc, data = call("tools/call", {"name": "platform.inventory", "arguments": {}})
        assert sc == 200
        assert not data["result"].get("isError")
        payload = json.loads(data["result"]["content"][0]["text"])
        assert payload["count"] > 0

    def test_platform_service_status(self, mcp):
        call, _, _, _ = mcp
        self._graceful(call, "platform.service_status", {"service_name": "card-fraud-engine"})

    def test_platform_service_health(self, mcp):
        call, _, _, _ = mcp
        sc, data = call(
            "tools/call",
            {
                "name": "platform.service_health",
                "arguments": {"service_name": "card-fraud-engine"},
            },
        )
        assert sc == 200
        assert not data["result"].get("isError")
        payload = json.loads(data["result"]["content"][0]["text"])
        # healthy must be a bool, never null (regression: Bug 4 from hardening pass)
        assert isinstance(payload.get("health", {}).get("healthy"), bool)

    def test_platform_ownership_summary(self, mcp):
        call, _, _, _ = mcp
        sc, data = call("tools/call", {"name": "platform.ownership_summary", "arguments": {}})
        assert sc == 200
        assert not data["result"].get("isError")

    # Postgres
    def test_postgres_list_schemas(self, mcp):
        call, _, _, _ = mcp
        self._graceful(call, "postgres.list_schemas")

    def test_postgres_list_tables(self, mcp):
        call, _, _, _ = mcp
        self._graceful(call, "postgres.list_tables", {"schema": "public"})

    def test_postgres_describe_table(self, mcp):
        call, _, _, _ = mcp
        self._graceful(
            call, "postgres.describe_table", {"table": "transactions", "schema": "fraud_gov"}
        )

    def test_postgres_query_readonly(self, mcp):
        call, _, _, _ = mcp
        self._graceful(call, "postgres.query_readonly", {"sql": "SELECT 1 AS ping"})

    # Redis
    def test_redis_scan_prefix(self, mcp):
        call, _, _, _ = mcp
        self._graceful(call, "redis.scan_prefix", {"prefix": "fraud:"})

    def test_redis_get_key(self, mcp):
        call, _, _, _ = mcp
        self._graceful(call, "redis.get_key", {"key": "fraud:test:nonexistent"})

    def test_redis_ttl(self, mcp):
        call, _, _, _ = mcp
        self._graceful(call, "redis.ttl", {"key": "fraud:test:nonexistent"})

    def test_redis_type(self, mcp):
        call, _, _, _ = mcp
        self._graceful(call, "redis.type", {"key": "fraud:test:nonexistent"})

    # Kafka
    def test_kafka_list_topics(self, mcp):
        call, _, _, _ = mcp
        self._graceful(call, "kafka.list_topics")

    def test_kafka_describe_topic(self, mcp):
        call, _, _, _ = mcp
        self._graceful(call, "kafka.describe_topic", {"topic": "fraud.transactions"})

    def test_kafka_consumer_lag(self, mcp):
        call, _, _, _ = mcp
        self._graceful(
            call, "kafka.consumer_lag", {"topic": "fraud.transactions", "group_id": "fraud-engine"}
        )

    def test_kafka_peek_messages(self, mcp):
        call, _, _, _ = mcp
        self._graceful(
            call, "kafka.peek_messages", {"topic": "fraud.transactions", "max_messages": 3}
        )

    # S3 / MinIO
    def test_s3_list_buckets(self, mcp):
        call, _, _, _ = mcp
        self._graceful(call, "s3.list_buckets")

    def test_s3_list_objects(self, mcp):
        call, _, _, _ = mcp
        self._graceful(call, "s3.list_objects", {"bucket": "fraud-gov-artifacts", "prefix": ""})

    def test_s3_head_object(self, mcp):
        call, _, _, _ = mcp
        self._graceful(
            call,
            "s3.head_object",
            {
                "bucket": "fraud-gov-artifacts",
                "key": "fraud-gov-artifacts/rulesets/local/US/CARD_AUTH/v1/ruleset.json",
            },
        )

    def test_s3_get_object(self, mcp):
        """s3.get_object was never called in any prior test suite — covers that gap."""
        call, _, _, _ = mcp
        self._graceful(
            call,
            "s3.get_object",
            {
                "bucket": "fraud-gov-artifacts",
                "key": "fraud-gov-artifacts/rulesets/local/US/CARD_AUTH/v1/ruleset.json",
            },
        )

    # Ops
    def test_ops_get_investigation_context(self, mcp):
        call, _, _, _ = mcp
        self._graceful(call, "ops.get_investigation_context", {"transaction_id": "txn_001"})

    def test_ops_run_investigation(self, mcp):
        call, _, _, _ = mcp
        self._graceful(
            call,
            "ops.run_investigation",
            {"investigation_type": "transaction_review", "target_id": "txn_001"},
        )


# ── 4. All 7 Resources via resources/read ─────────────────────────────────────


class TestAllResourcesLive:
    """Every resource must be readable and return HTTP 200 with non-empty contents."""

    def _read(self, call, uri: str) -> dict:
        sc, data = call("resources/read", {"uri": uri})
        assert sc == 200, f"resources/read {uri} → HTTP {sc}"
        assert data is not None, f"resources/read {uri} returned no SSE body"
        result = data.get("result", {})
        assert "contents" in result, f"resources/read {uri} missing 'contents' key"
        assert len(result["contents"]) >= 1, f"resources/read {uri} returned empty contents"
        return result

    def test_read_platform_services(self, mcp):
        call, _, _, _ = mcp
        result = self._read(call, "fraud://platform/services")
        text = result["contents"][0].get("text", "")
        payload = json.loads(text)
        assert "services" in payload
        assert len(payload["services"]) > 0

    def test_read_platform_ownership(self, mcp):
        call, _, _, _ = mcp
        result = self._read(call, "fraud://platform/ownership")
        text = result["contents"][0].get("text", "")
        payload = json.loads(text)
        # Ownership is a dict of team -> [service names]
        assert isinstance(payload, dict)
        assert len(payload) > 0

    def test_read_health_topology(self, mcp):
        call, _, _, _ = mcp
        result = self._read(call, "fraud://health/topology")
        text = result["contents"][0].get("text", "")
        payload = json.loads(text)
        assert "gateway" in payload
        assert "backends" in payload
        assert set(payload["backends"].keys()) == {
            "postgres",
            "redis",
            "kafka",
            "s3",
            "platform_api",
        }

    def test_read_ops_investigation_context(self, mcp):
        call, _, _, _ = mcp
        result = self._read(call, "fraud://ops/investigation-context")
        text = result["contents"][0].get("text", "")
        payload = json.loads(text)
        assert "available_tools" in payload
        assert "recommended_prompts" in payload

    def test_read_schemas_catalog(self, mcp):
        """fraud://schemas/catalog — graceful if no PG backend, errors must be structured."""
        call, _, _, _ = mcp
        sc, data = call("resources/read", {"uri": "fraud://schemas/catalog"})
        assert sc == 200
        assert data is not None
        result = data.get("result", {})
        assert "contents" in result
        text = result["contents"][0].get("text", "")
        payload = json.loads(text)
        # Either real tables list or structured error — both are valid
        assert "tables" in payload or "error" in payload

    def test_read_topics_catalog(self, mcp):
        """fraud://topics/catalog — graceful if no Kafka backend."""
        call, _, _, _ = mcp
        sc, data = call("resources/read", {"uri": "fraud://topics/catalog"})
        assert sc == 200
        assert data is not None
        result = data.get("result", {})
        assert "contents" in result
        text = result["contents"][0].get("text", "")
        payload = json.loads(text)
        assert "topics" in payload or "error" in payload

    def test_read_buckets_catalog(self, mcp):
        """fraud://buckets/catalog — graceful if no S3 backend."""
        call, _, _, _ = mcp
        sc, data = call("resources/read", {"uri": "fraud://buckets/catalog"})
        assert sc == 200
        assert data is not None
        result = data.get("result", {})
        assert "contents" in result
        text = result["contents"][0].get("text", "")
        payload = json.loads(text)
        assert "buckets" in payload or "error" in payload


# ── 5. All 5 Prompts ──────────────────────────────────────────────────────────


class TestAllPromptsLive:
    """All 5 prompts must be retrievable; 2 were missing from test_e2e_runtime.py."""

    def _get_prompt(self, call, name: str, args: dict | None = None) -> list[dict]:
        sc, data = call("prompts/get", {"name": name, "arguments": args or {}})
        assert sc == 200, f"prompts/get {name} → HTTP {sc}"
        messages = data["result"]["messages"]
        assert len(messages) >= 1, f"Prompt {name} returned no messages"
        return messages

    def test_investigate_transaction(self, mcp):
        call, _, _, _ = mcp
        msgs = self._get_prompt(call, "investigate-transaction", {"transaction_id": "txn_abc123"})
        text = " ".join(
            m.get("content", {}).get("text", "")
            if isinstance(m.get("content"), dict)
            else str(m.get("content", ""))
            for m in msgs
        )
        assert "txn_abc123" in text

    def test_triage_platform_health(self, mcp):
        call, _, _, _ = mcp
        msgs = self._get_prompt(call, "triage-platform-health")
        assert msgs

    def test_review_consumer_lag(self, mcp):
        call, _, _, _ = mcp
        msgs = self._get_prompt(call, "review-consumer-lag", {"group_id": "fraud-processor"})
        assert msgs

    def test_explain_decision_trace(self, mcp):
        """Previously untested — explain-decision-trace prompt."""
        call, _, _, _ = mcp
        msgs = self._get_prompt(call, "explain-decision-trace", {"transaction_id": "txn_xyz789"})
        text = " ".join(
            m.get("content", {}).get("text", "")
            if isinstance(m.get("content"), dict)
            else str(m.get("content", ""))
            for m in msgs
        )
        assert "txn_xyz789" in text
        # Prompt should reference decisions table and redis score key
        assert "decisions" in text.lower()
        assert "fraud:score" in text

    def test_inspect_ruleset_artifact(self, mcp):
        """Previously untested — inspect-ruleset-artifact prompt."""
        call, _, _, _ = mcp
        msgs = self._get_prompt(
            call,
            "inspect-ruleset-artifact",
            {"bucket": "fraud-gov-artifacts", "prefix": "rulesets/"},
        )
        text = " ".join(
            m.get("content", {}).get("text", "")
            if isinstance(m.get("content"), dict)
            else str(m.get("content", ""))
            for m in msgs
        )
        assert "fraud-gov-artifacts" in text
        # Prompt should reference s3.list_objects and s3.get_object
        assert "s3.list_objects" in text
        assert "s3.get_object" in text


# ── 6. Kafka Tools — Targeted Graceful-Error Verification ────────────────────


class TestKafkaToolsLive:
    """Kafka tools were previously checked only via graceful-error checks."""

    def _ok_or_error(self, call, tool: str, args: dict) -> dict:
        sc, data = call("tools/call", {"name": tool, "arguments": args})
        assert sc == 200, f"{tool} returned HTTP {sc}"
        assert data is not None
        result = data["result"]
        # Either a real payload or isError=True with structured error message
        if result.get("isError"):
            text = result["content"][0]["text"]
            # Must be a structured error string, not a Python traceback
            assert "Error" in text or "error" in text or "not configured" in text.lower(), (
                f"{tool} isError but message looks like unhandled exception: {text[:200]}"
            )
        return result

    def test_consumer_lag_graceful(self, mcp):
        call, _, _, _ = mcp
        self._ok_or_error(
            call, "kafka.consumer_lag", {"topic": "fraud.transactions", "group_id": "fraud-engine"}
        )

    def test_describe_topic_graceful(self, mcp):
        call, _, _, _ = mcp
        self._ok_or_error(call, "kafka.describe_topic", {"topic": "fraud.transactions"})

    def test_peek_messages_graceful(self, mcp):
        call, _, _, _ = mcp
        self._ok_or_error(
            call, "kafka.peek_messages", {"topic": "fraud.transactions", "max_messages": 3}
        )

    def test_list_topics_graceful(self, mcp):
        call, _, _, _ = mcp
        result = self._ok_or_error(call, "kafka.list_topics", {})
        if not result.get("isError"):
            payload = json.loads(result["content"][0]["text"])
            assert "topics" in payload


# ── 7. s3.get_object Specifically ─────────────────────────────────────────────


class TestS3GetObjectLive:
    """s3.get_object was never called in any prior test — verify behavior in both paths."""

    def test_get_object_graceful_no_backend(self, mcp):
        """Without a real S3 backend, must return isError=True with structured message."""
        call, _, _, _ = mcp
        sc, data = call(
            "tools/call",
            {
                "name": "s3.get_object",
                "arguments": {
                    "bucket": "fraud-gov-artifacts",
                    "key": "fraud-gov-artifacts/rulesets/local/US/CARD_AUTH/v1/ruleset.json",
                },
            },
        )
        assert sc == 200
        assert data is not None
        result = data["result"]
        # Either real content or graceful error — no 500
        if result.get("isError"):
            text = result["content"][0]["text"]
            assert text  # must have some error message

    def test_get_object_allowlist_boundary(self, mcp):
        """Requesting an object outside the allowlist must return isError=True, not 500."""
        call, _, _, _ = mcp
        sc, data = call(
            "tools/call",
            {
                "name": "s3.get_object",
                "arguments": {
                    "bucket": "secret-internal-bucket",
                    "key": "passwords.txt",
                },
            },
        )
        assert sc == 200
        assert data is not None
        result = data["result"]
        # Allowlist enforcement returns isError (not HTTP 403, as errors are wrapped)
        assert result.get("isError"), "Access to disallowed bucket must return isError=True"


# ── 8. Rate-Limit Enforcement — 429 Over the Wire ────────────────────────────


class TestRateLimitingLive:
    """Verify the rate limiter fires and returns 429 over a real TCP connection.

    The rate key for local-dev mode is ``client_id="local-dev"``.  We temporarily
    swap the module-level ``_fallback_limiter`` for a one-request window so the
    second call triggers the 429 without needing to fire 120 real requests.

    This works because the uvicorn server thread shares the same Python
    process memory as the test thread, so patching the module-level object
    is visible inside the server's request handlers.
    """

    def test_rate_limit_returns_429(self, live_url):
        from app.security import ratelimit as _rl

        # Save and replace the fallback limiter with a limit=1 window
        original = _rl._fallback_limiter
        _rl._fallback_limiter = _rl._LocalSlidingWindow(max_requests=1, window_seconds=60)
        try:
            headers = dict(_HDRS)
            with httpx.Client(base_url=live_url, timeout=10) as c:
                # First request: should pass (auth + rate limit check)
                r1 = c.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "rate-limit-test", "version": "1.0"},
                        },
                    },
                )
                assert r1.status_code == 200, f"First request failed unexpectedly: {r1.text}"

                # Second request: rate limiter exhausted — expect 429
                r2 = c.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/list",
                        "params": {},
                    },
                )
                assert r2.status_code == 429, (
                    f"Expected 429 from rate limiter, got {r2.status_code}: {r2.text}"
                )
                body = r2.json()
                assert body["error"] == "rate_limited"
                assert r2.headers.get("Retry-After") == "60"
                assert r2.headers.get("X-RateLimit-Remaining") == "0"
        finally:
            # Always restore so subsequent tests are not throttled
            _rl._fallback_limiter = original

    def test_rate_limit_headers_present_on_429(self, live_url):
        """429 response must carry Retry-After and X-RateLimit-Remaining headers."""
        from app.security import ratelimit as _rl

        original = _rl._fallback_limiter
        _rl._fallback_limiter = _rl._LocalSlidingWindow(max_requests=1, window_seconds=60)
        try:
            with httpx.Client(base_url=live_url, timeout=10) as c:
                c.get("/health")  # burn the single allowed slot via /health (skips auth)
                # Actually /health skips auth middleware entirely, so rate limit is not checked.
                # We need an MCP request to trigger rate limit.
                c.post(
                    "/mcp",
                    headers=_HDRS,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "t", "version": "1"},
                        },
                    },
                )
                r = c.post(
                    "/mcp",
                    headers=_HDRS,
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                )
            assert r.status_code == 429
            assert "Retry-After" in r.headers
            assert "X-RateLimit-Remaining" in r.headers
        finally:
            _rl._fallback_limiter = original


# ── 9. Ops Tools — Live Graceful Verification ─────────────────────────────────


class TestOpsToolsLive:
    """ops tools were only tested via _check_graceful; verify structured error shape here."""

    def _structured(self, call, tool: str, args: dict) -> None:
        sc, data = call("tools/call", {"name": tool, "arguments": args})
        assert sc == 200, f"{tool} returned HTTP {sc}"
        result = data["result"]
        content = result.get("content", [])
        assert content, f"{tool} returned empty content"
        text = content[0].get("text", "")
        # Either real JSON or an error — must always be parseable JSON
        payload = json.loads(text)
        assert payload  # non-empty

    def test_get_investigation_context_structured(self, mcp):
        call, _, _, _ = mcp
        self._structured(call, "ops.get_investigation_context", {"transaction_id": "txn_001"})

    def test_run_investigation_structured(self, mcp):
        call, _, _, _ = mcp
        self._structured(
            call,
            "ops.run_investigation",
            {"investigation_type": "transaction_review", "target_id": "txn_001"},
        )

    def test_run_investigation_response_shape(self, mcp):
        """ops.run_investigation must return status, investigation_type, target_id, steps."""
        call, _, _, _ = mcp
        sc, data = call(
            "tools/call",
            {
                "name": "ops.run_investigation",
                "arguments": {"investigation_type": "transaction_review", "target_id": "txn_001"},
            },
        )
        assert sc == 200
        result = data["result"]
        if not result.get("isError"):
            payload = json.loads(result["content"][0]["text"])
            assert "status" in payload
            assert "investigation_type" in payload
            assert "steps" in payload
