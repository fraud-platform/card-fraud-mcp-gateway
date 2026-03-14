"""Tests for Redis domain tools and helpers."""

from __future__ import annotations

import pytest

from app.domains import redis


class TestDecodeRedisScalar:
    def test_decode_bytes(self):
        result = redis._decode_redis_scalar(b"hello")
        assert result == "hello"

    def test_decode_string(self):
        result = redis._decode_redis_scalar("hello")
        assert result == "hello"

    def test_decode_list(self):
        result = redis._decode_redis_scalar([b"a", b"b"])
        assert result == ["a", "b"]

    def test_decode_tuple(self):
        result = redis._decode_redis_scalar((b"a", b"b"))
        assert result == ("a", "b")

    def test_decode_dict(self):
        result = redis._decode_redis_scalar({b"key": b"value"})
        assert result == {"key": "value"}

    def test_decode_nested(self):
        result = redis._decode_redis_scalar([{b"key": b"value"}])
        assert result == [{"key": "value"}]

    def test_decode_int(self):
        result = redis._decode_redis_scalar(42)
        assert result == 42


class TestDecodeScore:
    def test_decode_bytes(self):
        result = redis._decode_score(b"3.14")
        assert result == 3.14

    def test_decode_string(self):
        result = redis._decode_score("3.14")
        assert result == 3.14

    def test_decode_int(self):
        result = redis._decode_score(42)
        assert result == 42.0

    def test_decode_float(self):
        result = redis._decode_score(3.14)
        assert result == 3.14

    def test_decode_invalid_string(self):
        result = redis._decode_score("not_a_number")
        assert result == "not_a_number"

    def test_decode_other(self):
        result = redis._decode_score(None)
        assert result is None


class _FailOnUnexpectedCallClient:
    async def get(self, key):
        raise AssertionError(f"Unexpected get() call for {key}")

    async def hgetall(self, key):
        raise AssertionError(f"Unexpected hgetall() call for {key}")

    async def lrange(self, key, start, end):
        raise AssertionError(f"Unexpected lrange() call for {key}")

    async def smembers(self, key):
        raise AssertionError(f"Unexpected smembers() call for {key}")

    async def zrange(self, key, start, end, withscores=False):
        raise AssertionError(f"Unexpected zrange() call for {key}")

    async def xrevrange(self, key, count):
        raise AssertionError(f"Unexpected xrevrange() call for {key}")


class _StreamClient(_FailOnUnexpectedCallClient):
    def __init__(self):
        self.calls: list[tuple] = []

    async def type(self, key):
        self.calls.append(("type", key))
        return b"stream"

    async def xrevrange(self, key, count):
        self.calls.append(("xrevrange", key, count))
        return [(b"1-0", {b"payload": b"hello"})]


class _StringClient(_FailOnUnexpectedCallClient):
    def __init__(self):
        self.calls: list[tuple] = []

    async def type(self, key):
        self.calls.append(("type", key))
        return "string"

    async def get(self, key):
        self.calls.append(("get", key))
        return b"value"


class TestReadRedisValueByType:
    @pytest.mark.asyncio
    async def test_reads_stream_without_wrongtype_calls(self):
        client = _StreamClient()

        key_type, value = await redis._read_redis_value_by_type(client, "fraud:outbox", 5)

        assert key_type == "stream"
        assert value == [{"id": "1-0", "fields": {"payload": "hello"}}]
        assert client.calls == [("type", "fraud:outbox"), ("xrevrange", "fraud:outbox", 5)]

    @pytest.mark.asyncio
    async def test_reads_string_value(self):
        client = _StringClient()

        key_type, value = await redis._read_redis_value_by_type(client, "fraud:test:key", 10)

        assert key_type == "string"
        assert value == "value"
        assert client.calls == [("type", "fraud:test:key"), ("get", "fraud:test:key")]
