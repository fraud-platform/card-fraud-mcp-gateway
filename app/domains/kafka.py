"""Kafka / Redpanda read-only inspection tools — topics, messages, consumer lag."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.audit import audit_tool, tool_result
from app.backends import get_kafka_client
from app.config import settings
from app.constants import KAFKA_CONSUMER_TIMEOUT_MS
from app.metrics import record_result_truncation
from app.security.allowlist import check_exact, filter_by_allowlist
from app.security.policy import require_scope
from app.security.redaction import redact


def _check_topic_allowed(topic: str) -> None:
    """Reject access to topics not in the allowlist (when configured)."""
    check_exact(topic, settings.kafka_allowed_topics, "Topic")


def _check_group_allowed(group_id: str) -> None:
    """Reject access to consumer groups not in the allowlist (when configured)."""
    check_exact(group_id, settings.kafka_allowed_groups, "Consumer group")


# ---- Consumer Cache ----
# Cache consumers to avoid the 200-500ms startup overhead on each request


@dataclass(frozen=True, slots=True)
class _ConsumerKey:
    """Key for caching consumers with identical configuration."""

    group_id: str | None = None
    enable_auto_commit: bool = True
    auto_offset_reset: str = "latest"


@dataclass(slots=True)
class _CachedConsumer:
    """A cached Kafka consumer with last-used timestamp."""

    consumer: Any
    last_used: float = field(default_factory=time.monotonic)
    key: _ConsumerKey = field(default_factory=_ConsumerKey)


_consumer_cache: dict[_ConsumerKey, _CachedConsumer] = {}
_CACHE_TTL_SECONDS = 60


def _make_consumer_key(**kwargs: Any) -> _ConsumerKey:
    """Build a _ConsumerKey from aiokafka consumer kwargs."""
    return _ConsumerKey(
        group_id=kwargs.get("group_id"),
        enable_auto_commit=kwargs.get("enable_auto_commit", True),
        auto_offset_reset=kwargs.get("auto_offset_reset", "latest"),
    )


async def _get_cached_consumer(**kwargs: Any) -> Any:
    """Get a consumer from cache or create a new one.

    Consumers are cached by configuration (group_id, auto_commit, offset_reset)
    and reused for up to _CACHE_TTL_SECONDS to avoid the startup overhead.
    """
    key = _make_consumer_key(**kwargs)

    now = time.monotonic()
    # Remove stale entries
    for stale_key in list(_consumer_cache.keys()):
        if now - _consumer_cache[stale_key].last_used > _CACHE_TTL_SECONDS:
            await _stop_and_remove_consumer(stale_key)

    # Check for cached consumer
    cached = _consumer_cache.get(key)
    if cached is not None:
        cached.last_used = now
        return cached.consumer

    # Create new consumer
    from aiokafka import AIOKafkaConsumer

    consumer = AIOKafkaConsumer(
        bootstrap_servers=settings.kafka_broker_list,
        **kwargs,
    )
    await consumer.start()
    _consumer_cache[key] = _CachedConsumer(consumer=consumer, last_used=now, key=key)
    return consumer


async def _stop_and_remove_consumer(key: _ConsumerKey) -> None:
    """Stop and remove a consumer from the cache."""
    cached = _consumer_cache.pop(key, None)
    if cached is not None:
        with contextlib.suppress(Exception):
            await cached.consumer.stop()


@asynccontextmanager
async def _ephemeral_consumer(**kwargs: Any):
    """Get a consumer from cache or create a new temporary one.

    The shared global consumer (get_kafka_client) is used for metadata only.
    Peek and lag operations need their own consumer with specific
    group/offset config, so we use a cached consumer when possible.
    """
    consumer = await _get_cached_consumer(**kwargs)
    try:
        yield consumer
    except Exception:
        # If consumer fails, remove it from cache
        await _stop_and_remove_consumer(_make_consumer_key(**kwargs))
        raise


async def list_visible_topics(client: Any) -> list[str]:
    """Return non-internal topics after applying the configured allowlist."""
    all_topics = await client.topics()
    topics = sorted(t for t in all_topics if not t.startswith("_"))
    return filter_by_allowlist(topics, settings.kafka_allowed_topics)


async def get_topic_partitions(client: Any, topic: str) -> set[int]:
    """Fetch metadata and return known partitions for a topic."""
    fetch_all_metadata = getattr(getattr(client, "_client", None), "fetch_all_metadata", None)
    if callable(fetch_all_metadata):
        metadata = await fetch_all_metadata()
        partitions = metadata.partitions_for_topic(topic)
        if partitions:
            return set(partitions)

    # Fallback for simpler clients/mocks used in tests.
    await client.topics()
    partitions = client.partitions_for_topic(topic)
    return set(partitions) if partitions else set()


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="kafka.list_topics",
        description=(
            "List all non-internal Kafka/Redpanda topics. "
            "Domain: kafka | Read-only | Scope: fraud.kafka.read"
        ),
    )
    @require_scope("fraud.kafka.read", domain="kafka", tool_name="kafka.list_topics")
    @audit_tool("kafka")
    async def kafka_list_topics() -> str:
        client = get_kafka_client()
        topics = await list_visible_topics(client)
        return tool_result({"topics": topics, "count": len(topics)})

    @mcp.tool(
        name="kafka.describe_topic",
        description=(
            "Describe a Kafka topic: partition count and metadata. "
            "Args: topic. Domain: kafka | Read-only | Scope: fraud.kafka.read"
        ),
    )
    @require_scope("fraud.kafka.read", domain="kafka", tool_name="kafka.describe_topic")
    @audit_tool("kafka")
    async def kafka_describe_topic(topic: str) -> str:
        _check_topic_allowed(topic)
        client = get_kafka_client()
        partitions = await get_topic_partitions(client, topic)
        if not partitions:
            return tool_result({"error": f"Topic '{topic}' not found"})
        return tool_result(
            {"topic": topic, "partitions": sorted(partitions), "partition_count": len(partitions)}
        )

    @mcp.tool(
        name="kafka.peek_messages",
        description=(
            "Preview recent messages from a Kafka topic. "
            f"Max {settings.kafka_max_messages} messages. "
            "Messages are deserialized as UTF-8 with sensitive fields redacted. "
            "Args: topic, max_messages (default 5). "
            "Domain: kafka | Read-only | Scope: fraud.kafka.read"
        ),
    )
    @require_scope("fraud.kafka.read", domain="kafka", tool_name="kafka.peek_messages")
    @audit_tool("kafka")
    async def kafka_peek_messages(topic: str, max_messages: int = 5) -> str:
        _check_topic_allowed(topic)
        from aiokafka import TopicPartition

        count = min(max_messages, settings.kafka_max_messages)
        async with _ephemeral_consumer(
            enable_auto_commit=False,
            auto_offset_reset="latest",
            consumer_timeout_ms=KAFKA_CONSUMER_TIMEOUT_MS,
        ) as consumer:
            messages: list[dict] = []
            truncation_events = 0
            partitions_set = await get_topic_partitions(consumer, topic)
            if not partitions_set:
                return json.dumps({"error": f"Topic '{topic}' has no partitions"})

            tps = [TopicPartition(topic, p) for p in sorted(partitions_set)]
            consumer.assign(tps)
            await consumer.seek_to_end(*tps)

            for tp in tps:
                pos = await consumer.position(tp)
                consumer.seek(tp, max(0, pos - count))

            batch = await consumer.getmany(timeout_ms=3000, max_records=count)
            for _tp, records in batch.items():
                for record in records[:count]:
                    payload = (
                        record.value.decode("utf-8", errors="replace") if record.value else None
                    )
                    if payload and len(payload) > settings.kafka_max_payload_bytes:
                        truncation_events += 1
                        payload = payload[: settings.kafka_max_payload_bytes] + "...(truncated)"
                    messages.append(
                        {
                            "partition": record.partition,
                            "offset": record.offset,
                            "timestamp": record.timestamp,
                            "key": (
                                record.key.decode("utf-8", errors="replace") if record.key else None
                            ),
                            "value": payload,
                        }
                    )
            if truncation_events:
                record_result_truncation(
                    "kafka",
                    "kafka.peek_messages",
                    "max_payload_bytes",
                    truncation_events,
                )

        return redact(tool_result({"topic": topic, "messages": messages, "count": len(messages)}))

    @mcp.tool(
        name="kafka.consumer_lag",
        description=(
            "Check consumer group lag for a topic — committed offsets vs. log end. "
            "Args: group_id, topic. "
            "Domain: kafka | Read-only | Scope: fraud.kafka.read"
        ),
    )
    @require_scope("fraud.kafka.read", domain="kafka", tool_name="kafka.consumer_lag")
    @audit_tool("kafka")
    async def kafka_consumer_lag(group_id: str, topic: str) -> str:
        _check_topic_allowed(topic)
        _check_group_allowed(group_id)
        from aiokafka import TopicPartition

        async with _ephemeral_consumer(
            group_id=group_id,
            enable_auto_commit=False,
        ) as consumer:
            partitions_set = await get_topic_partitions(consumer, topic)
            if not partitions_set:
                return json.dumps({"error": f"No partitions found for topic '{topic}'"})

            tps = [TopicPartition(topic, p) for p in sorted(partitions_set)]
            end_offsets = await consumer.end_offsets(tps)
            committed_values = await asyncio.gather(
                *(consumer.committed(tp) for tp in tps),
            )

            lag_data: list[dict] = []
            total_lag = 0
            for tp, committed in zip(tps, committed_values, strict=True):
                current = committed if committed is not None else 0
                end = end_offsets.get(tp, 0)
                lag = end - current
                total_lag += lag
                lag_data.append(
                    {"partition": tp.partition, "committed": current, "end": end, "lag": lag}
                )

        return json.dumps(
            {
                "group_id": group_id,
                "topic": topic,
                "partitions": lag_data,
                "total_lag": total_lag,
            },
            indent=2,
        )
