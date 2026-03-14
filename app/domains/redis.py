from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from app.audit import audit_tool, tool_result
from app.backends import get_redis
from app.config import settings
from app.metrics import record_result_truncation
from app.security.allowlist import check_prefix
from app.security.policy import require_scope
from app.security.redaction import redact


def _decode_redis_scalar(value: object) -> object:
    """Decode bytes-like Redis values while preserving non-string shapes."""
    if isinstance(value, bytes):
        return value.decode()
    if isinstance(value, list):
        return [_decode_redis_scalar(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_decode_redis_scalar(item) for item in value)
    if isinstance(value, dict):
        return {_decode_redis_scalar(k): _decode_redis_scalar(v) for k, v in value.items()}
    return value


def _decode_score(value: object) -> object:
    """Decode and normalize Redis zset scores when possible."""
    if isinstance(value, bytes):
        value = value.decode()
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return value


async def _read_redis_value_by_type(client: object, key: str, max_items: int) -> tuple[str, object]:
    """Read a Redis key by detected type to avoid WRONGTYPE errors."""
    key_type_raw = await client.type(key)
    key_type = key_type_raw.decode() if isinstance(key_type_raw, bytes) else str(key_type_raw)

    if key_type == "string":
        return key_type, _decode_redis_scalar(await client.get(key))

    if key_type == "hash":
        return key_type, _decode_redis_scalar(await client.hgetall(key))

    if key_type == "list":
        list_value = await client.lrange(key, 0, max_items - 1)
        return key_type, [_decode_redis_scalar(item) for item in list_value]

    if key_type == "set":
        set_value = await client.smembers(key)
        return key_type, [_decode_redis_scalar(member) for member in set_value]

    if key_type == "zset":
        zset_value = await client.zrange(key, 0, max_items - 1, withscores=True)
        decoded = [
            (
                _decode_redis_scalar(pair[0]),
                _decode_score(pair[1]),
            )
            if isinstance(pair, (list, tuple)) and len(pair) == 2
            else _decode_redis_scalar(pair)
            for pair in zset_value
        ]
        return key_type, decoded

    if key_type == "stream":
        entries = await client.xrevrange(key, count=max_items)
        decoded = [
            {
                "id": _decode_redis_scalar(entry_id),
                "fields": _decode_redis_scalar(fields),
            }
            for entry_id, fields in entries
        ]
        return key_type, decoded

    if key_type == "none":
        return key_type, None

    return key_type, {"note": f"Unsupported Redis type '{key_type}'"}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="redis.scan_prefix",
        description=(
            f"Scan Redis keys matching a prefix pattern. Max {settings.redis_max_keys} keys. "
            "Args: prefix. Domain: redis | Read-only | Scope: fraud.redis.read"
        ),
    )
    @require_scope("fraud.redis.read", domain="redis", tool_name="redis.scan_prefix")
    @audit_tool("redis")
    async def redis_scan_prefix(prefix: str) -> str:
        check_prefix(prefix, settings.redis_allowed_prefixes, "Prefix")
        client = get_redis()
        keys: list[str] = []
        async for key in client.scan_iter(match=f"{prefix}*", count=100):
            keys.append(key)
            if len(keys) >= settings.redis_max_keys:
                record_result_truncation("redis", "redis.scan_prefix", "max_keys")
                break
        return tool_result(
            {
                "prefix": prefix,
                "keys": keys,
                "count": len(keys),
                "truncated": len(keys) >= settings.redis_max_keys,
            }
        )

    @mcp.tool(
        name="redis.get_key",
        description=(
            "Get the value of a Redis key. Large values are truncated. "
            "Supports string, hash, list, set, and zset types. "
            "Args: key. Domain: redis | Read-only | Scope: fraud.redis.read"
        ),
    )
    @require_scope("fraud.redis.read", domain="redis", tool_name="redis.get_key")
    @audit_tool("redis")
    async def redis_get_key(key: str) -> str:
        check_prefix(key, settings.redis_allowed_prefixes, "Key")
        client = get_redis()
        max_items = min(settings.redis_max_keys, 100)
        key_type, value = await _read_redis_value_by_type(client, key, max_items)

        serialized = json.dumps(value, default=str)
        truncated = len(serialized) > settings.redis_max_value_bytes
        if truncated:
            record_result_truncation("redis", "redis.get_key", "max_value_bytes")
            serialized = serialized[: settings.redis_max_value_bytes]

        return redact(
            tool_result(
                {
                    "key": key,
                    "type": key_type,
                    "value_preview": serialized,
                    "truncated": truncated,
                }
            )
        )

    @mcp.tool(
        name="redis.ttl",
        description=(
            "Get the TTL (time to live) of a Redis key in seconds. "
            "-1 = persistent, -2 = key does not exist. "
            "Args: key. Domain: redis | Read-only | Scope: fraud.redis.read"
        ),
    )
    @require_scope("fraud.redis.read", domain="redis", tool_name="redis.ttl")
    @audit_tool("redis")
    async def redis_ttl(key: str) -> str:
        check_prefix(key, settings.redis_allowed_prefixes, "Key")
        client = get_redis()
        ttl = await client.ttl(key)
        return tool_result(
            {"key": key, "ttl_seconds": ttl, "persistent": ttl == -1, "missing": ttl == -2}
        )

    @mcp.tool(
        name="redis.type",
        description=(
            "Get the data type of a Redis key (string, hash, list, set, zset, none). "
            "Args: key. Domain: redis | Read-only | Scope: fraud.redis.read"
        ),
    )
    @require_scope("fraud.redis.read", domain="redis", tool_name="redis.type")
    @audit_tool("redis")
    async def redis_type(key: str) -> str:
        check_prefix(key, settings.redis_allowed_prefixes, "Key")
        client = get_redis()
        key_type = await client.type(key)
        return tool_result({"key": key, "type": key_type})
