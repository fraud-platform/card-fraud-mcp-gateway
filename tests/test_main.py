"""Tests for main application module."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app import main


class TestSecurityHeaders:
    def test_add_security_headers_http_response(self):
        message = {"type": "http.response.start", "headers": []}
        main._add_security_headers(message)
        assert "headers" in message

    def test_add_security_headers_non_http_response(self):
        message = {"type": "http.request", "headers": []}
        main._add_security_headers(message)
        assert message["headers"] == []


class TestRequireSecureCors:
    def test_require_secure_cors_empty(self):
        assert main._require_secure_cors([]) is False

    def test_require_secure_cors_wildcard(self):
        assert main._require_secure_cors(["*"]) is False

    def test_require_secure_cors_valid(self):
        assert main._require_secure_cors(["http://localhost:3000"]) is True

    def test_require_secure_cors_mixed(self):
        assert main._require_secure_cors(["http://localhost:3000", "*"]) is False


class TestHealthEndpoint:
    def test_health_returns_ok(self):
        client = TestClient(main.app, raise_server_exceptions=False)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestReadinessEndpoint:
    def test_readiness_returns_status(self):
        client = TestClient(main.app, raise_server_exceptions=False)
        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert "backends" in data


class TestCatalogEndpoint:
    def test_catalog_returns_tools(self):
        client = TestClient(main.app, raise_server_exceptions=False)
        response = client.get("/catalog")
        assert response.status_code == 200
        data = response.json()
        assert "tools" in data
        assert "tool_count" in data


class TestCheckBackend:
    @pytest.mark.asyncio
    async def test_check_backend_not_configured(self):
        result = await main._check_backend(lambda: None, configured=False)
        assert result == "not_configured"

    @pytest.mark.asyncio
    async def test_check_backend_configured_ok(self):
        async def checker():
            return "ok"

        result = await main._check_backend(checker, configured=True)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_check_backend_configured_error(self):
        def checker():
            raise ValueError("error")

        result = await main._check_backend(checker, configured=True)
        assert result == "error"


class TestGetMainMcp:
    def test_get_main_mcp_returns_instance(self):
        mcp = main._get_main_mcp()
        assert mcp is not None

    def test_get_main_mcp_caches(self):
        mcp1 = main._get_main_mcp()
        mcp2 = main._get_main_mcp()
        assert mcp1 is mcp2
