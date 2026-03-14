"""Tests for authentication and rate limiting paths."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from app.main import create_app
from app.security import auth
from app.security.auth import CallerIdentity, authenticate_request, validate_token


def test_mcp_requires_bearer_token(monkeypatch):
    monkeypatch.setattr("app.config.settings.skip_jwt_validation", False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    resp = client.get("/mcp")

    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


def test_rate_limited_request_returns_429(monkeypatch):
    caller = CallerIdentity(sub="rate-limited-user", client_id="rate-limit-client")
    client = TestClient(create_app(), raise_server_exceptions=False)

    with (
        patch("app.main.authenticate_request", AsyncMock(return_value=caller)),
        patch("app.main.check_rate_limit", return_value=(False, 0)),
    ):
        resp = client.get("/mcp", headers={"Authorization": "Bearer test-token"})

    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "60"
    assert resp.json()["error"] == "rate_limited"


@pytest.mark.asyncio
async def test_authenticate_request_requires_bearer_when_enabled(monkeypatch):
    monkeypatch.setattr("app.config.settings.skip_jwt_validation", False)
    request = Request({"type": "http", "headers": []})

    with pytest.raises(PermissionError, match="Authorization"):
        await authenticate_request(request)


@pytest.mark.asyncio
async def test_validate_token_parses_claims(monkeypatch):
    monkeypatch.setattr(
        auth,
        "_fetch_jwks",
        AsyncMock(return_value={"keys": [{"kid": "kid-1", "kty": "RSA", "n": "n", "e": "e"}]}),
    )
    monkeypatch.setattr(auth.jwt, "get_unverified_header", lambda _token: {"kid": "kid-1"})
    monkeypatch.setattr(auth.jwt.algorithms.RSAAlgorithm, "from_jwk", lambda _jwk: "public-key")
    monkeypatch.setattr(
        auth.jwt,
        "decode",
        lambda *_args, **_kwargs: {
            "sub": "user-123",
            "scope": "fraud.db.read fraud.redis.read",
            "azp": "client-123",
            "email": "user@example.com",
        },
    )

    caller = await validate_token("token")

    assert caller.sub == "user-123"
    assert caller.client_id == "client-123"
    assert caller.email == "user@example.com"
    assert caller.scopes == frozenset({"fraud.db.read", "fraud.redis.read"})


@pytest.mark.asyncio
async def test_fetch_jwks_uses_cache(monkeypatch):
    calls = 0

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"keys": [{"kid": "kid-1"}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _url: str) -> FakeResponse:
            nonlocal calls
            calls += 1
            return FakeResponse()

    monkeypatch.setattr(auth.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(auth.settings, "auth0_domain", "example.auth0.com")
    auth._jwks_cache = None
    auth._jwks_cached_at = 0.0

    first = await auth._fetch_jwks()
    second = await auth._fetch_jwks()

    assert first == second == {"keys": [{"kid": "kid-1"}]}
    assert calls == 1

    auth._jwks_cache = None
    auth._jwks_cached_at = 0.0
