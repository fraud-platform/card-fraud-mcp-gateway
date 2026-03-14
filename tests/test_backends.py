"""Tests for backend connection management."""

from __future__ import annotations

import pytest

from app import backends


class TestBackendManager:
    def test_backend_manager_not_configured(self):
        manager = backends.BackendManager("test", "Test not configured")
        assert not manager.is_configured

    def test_backend_manager_get_raises_when_not_configured(self):
        manager = backends.BackendManager("test", "Test not configured")
        with pytest.raises(RuntimeError, match="Test not configured"):
            manager.get()

    def test_backend_manager_set_and_get(self):
        manager = backends.BackendManager("test", "Test not configured")
        manager._set_instance("test_instance")
        assert manager.is_configured
        assert manager.get() == "test_instance"

    @pytest.mark.asyncio
    async def test_backend_manager_close_clears_instance(self):
        manager = backends.BackendManager("test", "Test not configured", close_fn=None)
        manager._set_instance("test_instance")
        await manager.close()
        assert not manager.is_configured

    @pytest.mark.asyncio
    async def test_backend_manager_close_calls_close_fn(self):
        closed = []

        async def close_fn(instance):
            closed.append(instance)

        manager = backends.BackendManager("test", "Test not configured", close_fn=close_fn)
        manager._set_instance("test_instance")
        await manager.close()
        assert closed == ["test_instance"]
        assert not manager.is_configured


class TestBackendGetters:
    def test_get_pg_pool_raises_when_not_initialized(self):
        with pytest.raises(RuntimeError, match="PostgreSQL pool not initialized"):
            backends.get_pg_pool()

    def test_get_redis_raises_when_not_initialized(self):
        with pytest.raises(RuntimeError, match="Redis client not initialized"):
            backends.get_redis()

    def test_get_kafka_client_raises_when_not_initialized(self):
        with pytest.raises(RuntimeError, match="Kafka client not initialized"):
            backends.get_kafka_client()

    def test_get_s3_client_raises_when_not_initialized(self):
        with pytest.raises(RuntimeError, match="S3 session not initialized"):
            backends.get_s3_client()

    def test_get_s3_session_raises_when_not_initialized(self):
        with pytest.raises(RuntimeError, match="S3 session not initialized"):
            backends.get_s3_session()

    def test_get_platform_client_raises_when_not_initialized(self):
        with pytest.raises(RuntimeError, match="Platform client not initialized"):
            backends.get_platform_client()


class TestS3Session:
    def test_get_s3_session_returns_none_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(backends, "_s3_session", None)
        with pytest.raises(RuntimeError, match="S3 session not initialized"):
            backends.get_s3_session()
