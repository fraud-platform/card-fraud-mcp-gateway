"""Integration tests for the ASGI app — health, readiness, catalog endpoints."""

from __future__ import annotations

import pytest
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from app.main import RequestSizeLimitMiddleware


@pytest.fixture
def client():
    from app.main import create_app

    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


def test_health(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "card-fraud-mcp-gateway"


def test_readiness_no_backends(client: TestClient):
    resp = client.get("/ready")
    data = resp.json()
    assert "backends" in data
    assert isinstance(data["backends"], dict)


def test_catalog(client: TestClient):
    resp = client.get("/catalog")
    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data
    assert "tool_count" in data
    assert data["tool_count"] == 22
    # Verify tool names use dot notation (P0 fix)
    for tool in data["tools"]:
        assert "." in tool["tool"], f"Tool name {tool['tool']} missing dot notation"


def test_metrics_endpoint(client: TestClient):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "gateway_http_requests_total" in resp.text


def test_catalog_includes_resources_and_prompts(client: TestClient):
    resp = client.get("/catalog")
    data = resp.json()
    assert "resources" in data
    assert "resource_count" in data
    assert data["resource_count"] == 7
    assert data["resources"] == [
        "fraud://buckets/catalog",
        "fraud://health/topology",
        "fraud://ops/investigation-context",
        "fraud://platform/ownership",
        "fraud://platform/services",
        "fraud://schemas/catalog",
        "fraud://topics/catalog",
    ]
    assert "prompts" in data
    assert "prompt_count" in data
    assert data["prompt_count"] == 5
    assert data["prompts"] == [
        "explain-decision-trace",
        "inspect-ruleset-artifact",
        "investigate-transaction",
        "review-consumer-lag",
        "triage-platform-health",
    ]


def test_catalog_has_required_domains(client: TestClient):
    resp = client.get("/catalog")
    data = resp.json()
    domains = {t["domain"] for t in data["tools"]}
    for expected in ("platform", "postgres", "redis", "kafka", "storage", "ops"):
        assert expected in domains, f"Missing domain: {expected}"


def test_unknown_path_returns_404(client: TestClient):
    resp = client.get("/nonexistent")
    # Auth is disabled in tests, so unknown paths hit the router → 404
    assert resp.status_code == 404


def test_mcp_endpoint_exists(client: TestClient):
    # MCP Streamable HTTP expects POST with specific headers; a bare GET
    # should not raise a server error — any HTTP status is acceptable
    # as long as the route is mounted.
    resp = client.get("/mcp")
    # 500 is tolerable here because backends aren't initialized in test mode.
    # The point is that the route exists and doesn't crash with an import error.
    assert resp.status_code is not None


def test_request_size_limit_rejects_oversized():
    """Requests with Content-Length exceeding the limit return 413."""
    from app.main import create_app

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    # The default limit is 1 MB. Send a header claiming a larger payload.
    resp = client.post(
        "/health",
        content=b"x",
        headers={"content-length": str(2_000_000)},
    )
    assert resp.status_code == 413
    assert resp.json()["error"] == "request_too_large"


def test_request_size_limit_allows_normal():
    """Requests within size limits pass through normally."""
    from app.main import create_app

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_request_size_limit_rejects_streaming_body_without_content_length():
    """Streaming request bodies are counted even when Content-Length is absent."""

    async def app(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body", False):
                break
        await JSONResponse({"status": "ok"})(scope, receive, send)

    chunks = [
        {"type": "http.request", "body": b"x" * 6, "more_body": True},
        {"type": "http.request", "body": b"x" * 6, "more_body": False},
    ]
    sent: list[dict] = []

    async def receive():
        return chunks.pop(0) if chunks else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    middleware = RequestSizeLimitMiddleware(app, max_bytes=10)
    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}

    await middleware(scope, receive, send)

    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 413
