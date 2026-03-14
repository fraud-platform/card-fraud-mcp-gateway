"""Backend connection pool lifecycle management."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import structlog

from app.config import settings
from app.constants import (
    MILLISECONDS_PER_SECOND,
    SOCKET_TIMEOUT_SECONDS,
    TIMEOUT_SECONDS,
)
from app.metrics import record_backend_init_failure

logger = structlog.get_logger("backends")


class BackendManager:
    """Backend lifecycle manager with init/get/close pattern."""

    def __init__(
        self,
        name: str,
        not_configured_msg: str,
        close_fn: Callable[[Any], Any] | None = None,
    ):
        self._name = name
        self._not_configured_msg = not_configured_msg
        self._close_fn = close_fn
        self._instance: Any = None

    @property
    def is_configured(self) -> bool:
        return self._instance is not None

    def get(self) -> Any:
        if self._instance is None:
            raise RuntimeError(self._not_configured_msg)
        return self._instance

    async def close(self) -> None:
        if self._instance is not None:
            if self._close_fn:
                await self._close_fn(self._instance)
            self._instance = None

    def _set_instance(self, instance: Any) -> None:
        self._instance = instance


_pg_manager = BackendManager(
    "postgres",
    "PostgreSQL pool not initialized. Set GATEWAY_PG_DSN.",
    close_fn=lambda p: p.close(),
)
_redis_manager = BackendManager(
    "redis",
    "Redis client not initialized. Set GATEWAY_REDIS_URL.",
    close_fn=lambda c: c.aclose(),
)
_kafka_manager = BackendManager(
    "kafka",
    "Kafka client not initialized. Set GATEWAY_KAFKA_BROKERS.",
    close_fn=lambda c: c.stop(),
)
_s3_manager = BackendManager(
    "s3",
    "S3 session not initialized. Set GATEWAY_S3_ENDPOINT.",
    close_fn=None,  # aioboto3.Session has no close(); clients are closed per-operation
)
_s3_session: Any | None = None
_platform_manager = BackendManager(
    "platform",
    "Platform client not initialized. Set GATEWAY_PLATFORM_API_URL.",
    close_fn=lambda c: c.aclose(),
)


async def init_pg() -> None:
    if not settings.pg_dsn:
        logger.info("pg_skip", reason="No PG_DSN configured")
        return
    import asyncpg

    _pg_pool = await asyncpg.create_pool(
        settings.pg_dsn,
        min_size=settings.pg_pool_min,
        max_size=settings.pg_pool_max,
        command_timeout=settings.pg_statement_timeout_ms / MILLISECONDS_PER_SECOND,
        server_settings={"default_transaction_read_only": "on"},
    )
    _pg_manager._set_instance(_pg_pool)
    logger.info("pg_ready", pool_min=settings.pg_pool_min, pool_max=settings.pg_pool_max)


async def close_pg() -> None:
    await _pg_manager.close()


def get_pg_pool() -> Any:
    return _pg_manager.get()


async def init_redis() -> None:
    if not settings.redis_url:
        logger.info("redis_skip", reason="No REDIS_URL configured")
        return
    import redis.asyncio as aioredis

    _redis_client = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=SOCKET_TIMEOUT_SECONDS,
    )
    await _redis_client.ping()
    _redis_manager._set_instance(_redis_client)
    logger.info("redis_ready")


async def close_redis() -> None:
    await _redis_manager.close()


def get_redis() -> Any:
    return _redis_manager.get()


async def init_kafka() -> None:
    if not settings.kafka_broker_list:
        logger.info("kafka_skip", reason="No KAFKA_BROKERS configured")
        return
    from aiokafka import AIOKafkaConsumer

    _kafka_client = AIOKafkaConsumer(
        bootstrap_servers=settings.kafka_broker_list,
        client_id="card-fraud-mcp-gateway",
    )
    await _kafka_client.start()
    _kafka_manager._set_instance(_kafka_client)
    logger.info("kafka_ready", brokers=settings.kafka_broker_list)


async def close_kafka() -> None:
    await _kafka_manager.close()


def get_kafka_client() -> Any:
    return _kafka_manager.get()


def init_s3() -> None:
    if not settings.s3_endpoint:
        logger.info("s3_skip", reason="No S3_ENDPOINT configured")
        return
    import aioboto3

    global _s3_session
    _s3_session = aioboto3.Session(
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )
    # Store the session — callers must use `async with get_s3_session().client(...) as s3:`
    # because aioboto3 requires the async context manager protocol per operation.
    _s3_manager._set_instance(_s3_session)
    logger.info("s3_ready", endpoint=settings.s3_endpoint)


def get_s3_session() -> Any:
    """Return the configured aioboto3 Session.

    Callers must use the async context manager protocol per operation:
        async with get_s3_session().client("s3", endpoint_url=settings.s3_endpoint) as s3:
            response = await s3.list_buckets()
    """
    return _s3_manager.get()


def get_s3_client() -> Any:
    """Deprecated alias for get_s3_session(). Use get_s3_session() directly."""
    return _s3_manager.get()


async def init_platform() -> None:
    if not settings.platform_api_url:
        logger.info("platform_skip", reason="No PLATFORM_API_URL configured")
        return
    import httpx

    headers: dict[str, str] = {}
    if settings.platform_api_token:
        headers["Authorization"] = f"Bearer {settings.platform_api_token}"
    _platform_client = httpx.AsyncClient(
        base_url=settings.platform_api_url,
        headers=headers,
        timeout=TIMEOUT_SECONDS,
    )
    _platform_manager._set_instance(_platform_client)
    logger.info("platform_ready", url=settings.platform_api_url)


async def close_platform() -> None:
    await _platform_manager.close()


def get_platform_client() -> Any:
    return _platform_manager.get()


async def init_all() -> None:
    """Initialize all backend connections. Failures are logged, not fatal."""
    try:
        init_s3()
    except Exception as exc:
        record_backend_init_failure("s3")
        logger.warning("backend_init_error", backend="s3", error=str(exc))

    async_inits = [
        ("postgres", init_pg()),
        ("redis", init_redis()),
        ("kafka", init_kafka()),
        ("platform", init_platform()),
    ]
    results = await asyncio.gather(
        *(init_fn for _, init_fn in async_inits),
        return_exceptions=True,
    )

    for (name, _), result in zip(async_inits, results, strict=True):
        if isinstance(result, Exception):
            record_backend_init_failure(name)
            logger.warning("backend_init_error", backend=name, error=str(result))


async def close_all() -> None:
    """Gracefully close all backend connections."""
    await asyncio.gather(
        close_pg(),
        close_redis(),
        close_kafka(),
        close_platform(),
        close_s3(),
        return_exceptions=True,
    )


async def close_s3() -> None:
    """Clear the S3 session — individual clients are closed after each async with block."""
    global _s3_session
    _s3_session = None
    await _s3_manager.close()
