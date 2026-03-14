"""Regression tests for production hardening fixes.

Each test class maps to a specific bug that was fixed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.security.auth import CallerIdentity

# ---------------------------------------------------------------------------
# Bug 1: ensure_scope passed name= instead of tool= to _check_scope
# ---------------------------------------------------------------------------


class TestEnsureScopeKeyword:
    """ensure_scope must forward the resource name as tool= not name=."""

    @pytest.mark.asyncio
    async def test_ensure_scope_passes_tool_kwarg(self):
        """_check_scope must be called with tool= (not name=) — wrong kwarg raises TypeError."""
        caller = CallerIdentity(sub="u", scopes=frozenset({"fraud.storage.read"}))

        with (
            patch("app.security.policy.get_caller", return_value=caller),
            patch("app.security.policy._check_scope") as mock_check,
        ):
            from app.security.policy import ensure_scope

            ensure_scope("fraud.storage.read", domain="storage", name="fraud://buckets/catalog")

        mock_check.assert_called_once_with(
            "fraud.storage.read",
            tool="fraud://buckets/catalog",
            domain="storage",
        )

    @pytest.mark.asyncio
    async def test_ensure_scope_denied_raises_permission_error(self):
        """ensure_scope raises PermissionError when caller lacks the required scope."""
        caller = CallerIdentity(sub="u", scopes=frozenset({"fraud.db.read"}))

        with (
            patch("app.security.policy.get_caller", return_value=caller),
            pytest.raises(PermissionError, match="fraud.storage.read"),
        ):
            from app.security.policy import ensure_scope

            ensure_scope("fraud.storage.read", domain="storage", name="fraud://buckets/catalog")

    @pytest.mark.asyncio
    async def test_ensure_scope_allowed_does_not_raise(self):
        """ensure_scope must not raise when caller has the required scope."""
        caller = CallerIdentity(sub="u", scopes=frozenset({"fraud.storage.read"}))

        with patch("app.security.policy.get_caller", return_value=caller):
            from app.security.policy import ensure_scope

            # Should not raise
            ensure_scope("fraud.storage.read", domain="storage", name="fraud://buckets/catalog")


# ---------------------------------------------------------------------------
# Bug 2: _add_security_headers dropped duplicate Set-Cookie headers
# ---------------------------------------------------------------------------


class TestSecurityHeadersPreserveDuplicates:
    """_add_security_headers must not drop duplicate headers (e.g. Set-Cookie)."""

    def test_multiple_set_cookie_headers_are_preserved(self):
        from app.main import _add_security_headers

        message = {
            "type": "http.response.start",
            "headers": [
                (b"set-cookie", b"session=abc; HttpOnly"),
                (b"set-cookie", b"csrf=xyz; SameSite=Strict"),
            ],
        }
        _add_security_headers(message)

        cookie_headers = [v for k, v in message["headers"] if k == b"set-cookie"]
        assert len(cookie_headers) == 2, "Both Set-Cookie headers must be preserved"
        assert b"session=abc; HttpOnly" in cookie_headers
        assert b"csrf=xyz; SameSite=Strict" in cookie_headers

    def test_security_headers_are_added(self):
        from app.main import _SECURITY_HEADERS, _add_security_headers

        message = {"type": "http.response.start", "headers": []}
        _add_security_headers(message)

        keys = {k for k, _ in message["headers"]}
        for expected_key, _ in _SECURITY_HEADERS:
            assert expected_key in keys, f"Security header {expected_key!r} not added"

    def test_existing_security_header_not_duplicated(self):
        """If a security header is already present, it must not be added again."""
        from app.main import _add_security_headers

        message = {
            "type": "http.response.start",
            "headers": [
                (b"x-frame-options", b"SAMEORIGIN"),
            ],
        }
        _add_security_headers(message)

        x_frame = [v for k, v in message["headers"] if k == b"x-frame-options"]
        assert len(x_frame) == 1, "x-frame-options must not be duplicated"
        assert x_frame[0] == b"SAMEORIGIN", "Original header value must be kept"

    def test_non_http_response_message_is_ignored(self):
        from app.main import _add_security_headers

        original = {"type": "http.request", "headers": []}
        _add_security_headers(original)
        assert original["headers"] == []


# ---------------------------------------------------------------------------
# Bug 3: S3 tools stored context manager instead of session (aioboto3 misuse)
# ---------------------------------------------------------------------------


class TestS3SessionContextManager:
    """S3 tool calls must use async with session.client(...) as s3:, never call
    methods on the bare context manager returned by session.client(...)."""

    @pytest.mark.asyncio
    async def test_s3_list_buckets_uses_context_manager(self):
        """s3.list_buckets tool opens a fresh client via async with on each call."""
        fake_client = AsyncMock()
        fake_client.list_buckets = AsyncMock(return_value={"Buckets": []})

        fake_ctx_mgr = AsyncMock()
        fake_ctx_mgr.__aenter__ = AsyncMock(return_value=fake_client)
        fake_ctx_mgr.__aexit__ = AsyncMock(return_value=False)

        fake_session = MagicMock()
        fake_session.client = MagicMock(return_value=fake_ctx_mgr)

        caller = CallerIdentity(sub="u", scopes=frozenset({"fraud.storage.read"}))

        with (
            patch("app.domains.storage.get_s3_session", return_value=fake_session),
            patch("app.security.policy.get_caller", return_value=caller),
        ):
            from mcp.server.fastmcp import FastMCP

            from app.domains.storage import register

            mcp = FastMCP("test")
            register(mcp)

            tool_fn = next(
                t.fn for t in mcp._tool_manager.list_tools() if t.name == "s3.list_buckets"
            )
            await tool_fn()

        fake_session.client.assert_called_once()
        fake_ctx_mgr.__aenter__.assert_called_once()
        fake_ctx_mgr.__aexit__.assert_called_once()
        fake_client.list_buckets.assert_called_once()

    @pytest.mark.asyncio
    async def test_s3_list_objects_uses_context_manager(self):
        """s3.list_objects tool opens a fresh client via async with on each call."""
        fake_client = AsyncMock()
        fake_client.list_objects_v2 = AsyncMock(return_value={"Contents": []})

        fake_ctx_mgr = AsyncMock()
        fake_ctx_mgr.__aenter__ = AsyncMock(return_value=fake_client)
        fake_ctx_mgr.__aexit__ = AsyncMock(return_value=False)

        fake_session = MagicMock()
        fake_session.client = MagicMock(return_value=fake_ctx_mgr)

        caller = CallerIdentity(sub="u", scopes=frozenset({"fraud.storage.read"}))

        with (
            patch("app.domains.storage.get_s3_session", return_value=fake_session),
            patch("app.security.policy.get_caller", return_value=caller),
            # Bypass allowlist enforcement — not what this test is about
            patch("app.domains.storage._check_object_allowed"),
        ):
            from mcp.server.fastmcp import FastMCP

            from app.domains.storage import register

            mcp = FastMCP("test")
            register(mcp)

            tool_fn = next(
                t.fn for t in mcp._tool_manager.list_tools() if t.name == "s3.list_objects"
            )
            await tool_fn(bucket="test-bucket", prefix="")

        fake_client.list_objects_v2.assert_called_once()
        fake_ctx_mgr.__aexit__.assert_called_once()


# ---------------------------------------------------------------------------
# Bug 4: Kafka stale-consumer cleanup called without await (coroutine dropped)
# ---------------------------------------------------------------------------


class TestKafkaConsumerCleanupIsAwaited:
    """_get_cached_consumer must await _stop_and_remove_consumer for stale entries."""

    @pytest.mark.asyncio
    async def test_stale_consumer_is_awaited_and_removed(self):
        """Stale cache entries are stopped via awaited coroutine, not fire-and-forget."""
        import time

        from app.domains import kafka as kafka_mod
        from app.domains.kafka import _CachedConsumer, _ConsumerKey

        # Reset cache to a known state
        kafka_mod._consumer_cache.clear()

        stopped: list[str] = []

        async def fake_stop():
            stopped.append("stopped")

        fake_stale_consumer = MagicMock()
        fake_stale_consumer.stop = fake_stop

        stale_key = _ConsumerKey(group_id="stale-group")
        # Make it look old: last_used well before the TTL threshold
        kafka_mod._consumer_cache[stale_key] = _CachedConsumer(
            consumer=fake_stale_consumer,
            last_used=time.monotonic() - kafka_mod._CACHE_TTL_SECONDS - 10,
            key=stale_key,
        )

        # Also add a fresh entry for the key we'll request — avoids creating a
        # real AIOKafkaConsumer (which is a local import inside _get_cached_consumer)
        fresh_consumer = MagicMock()
        fresh_key = _ConsumerKey(
            group_id="fresh-group",
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        kafka_mod._consumer_cache[fresh_key] = _CachedConsumer(
            consumer=fresh_consumer,
            last_used=time.monotonic(),
            key=fresh_key,
        )

        await kafka_mod._get_cached_consumer(
            group_id="fresh-group",
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )

        # The stale consumer must have been stopped
        assert "stopped" in stopped, "Stale consumer was not stopped (await was missing)"
        # The stale key must have been evicted
        assert stale_key not in kafka_mod._consumer_cache, "Stale key was not evicted"
        # The fresh key must still be present
        assert fresh_key in kafka_mod._consumer_cache

        # Clean up cache for other tests
        kafka_mod._consumer_cache.clear()

    @pytest.mark.asyncio
    async def test_stop_and_remove_consumer_suppresses_exceptions(self):
        """_stop_and_remove_consumer must not propagate stop() failures."""
        from app.domains import kafka as kafka_mod

        kafka_mod._consumer_cache.clear()

        async def bad_stop():
            raise RuntimeError("Kafka connection reset")

        fake_consumer = MagicMock()
        fake_consumer.stop = bad_stop

        from app.domains.kafka import _CachedConsumer, _ConsumerKey, _stop_and_remove_consumer

        key = _ConsumerKey(group_id="error-group")
        kafka_mod._consumer_cache[key] = _CachedConsumer(
            consumer=fake_consumer,
            last_used=0.0,
            key=key,
        )

        # Must not raise
        await _stop_and_remove_consumer(key)
        assert key not in kafka_mod._consumer_cache


# ---------------------------------------------------------------------------
# Bug 5: Mutable ContextVar default in auth.py
# ---------------------------------------------------------------------------


class TestRequestContextMutableDefault:
    """_request_context must not use a mutable dict as its ContextVar default."""

    def test_default_is_not_mutable_dict(self):
        """The ContextVar default must be None (immutable), not a shared dict."""
        from app.security import auth

        default = auth._request_context.get(None)
        # When no context has been set the default must be None, not a dict
        assert default is None, (
            "ContextVar default must be None to avoid shared mutable state across requests"
        )

    def test_get_request_context_returns_safe_fallback(self):
        """get_request_context() must return a fresh dict even when context is unset."""
        from app.security.auth import get_request_context

        ctx = get_request_context()
        assert isinstance(ctx, dict)
        assert "request_id" in ctx
        assert "source_ip" in ctx
