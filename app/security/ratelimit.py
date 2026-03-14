"""Distributed, Redis-backed request rate limiter."""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from app.config import settings
from app.constants import RATE_LIMIT_WINDOW_SECONDS

_LOCAL_LIMITER_PREFIX = "gateway:ratelimit"

_RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local member = ARGV[4]

local window_start = now_ms - window_ms
redis.call('ZREMRANGEBYSCORE', key, 0, window_start)
local count = tonumber(redis.call('ZCARD', key))

if count >= max_requests then
  return {0, count, 0}
end

redis.call('ZADD', key, now_ms, member)
redis.call('EXPIRE', key, tonumber(math.floor((window_ms + 999) / 1000)))
return {1, count + 1, math.max(0, max_requests - (count + 1))}
"""


class _LocalSlidingWindow:
    """Fallback in-process limiter when Redis is not configured."""

    def __init__(self, max_requests: int, window_seconds: int = 60):
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_cleanup = 0.0

    def _prune_key(self, key: str, now: float) -> list[float]:
        cutoff = now - self._window
        values = self._hits[key]
        while values and values[0] <= cutoff:
            values.popleft()
        if not values:
            self._hits.pop(key, None)
            return []
        return list(values)

    def _prune_all_keys(self, now: float) -> None:
        for key in list(self._hits):
            self._prune_key(key, now)

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        if now - self._last_cleanup >= 5:
            self._prune_all_keys(now)
            self._last_cleanup = now

        values = self._prune_key(key, now)
        if len(values) >= self._max:
            return False, 0

        values.append(now)
        self._hits[key] = deque(values)
        return True, self._max - len(self._hits[key])

    def check(self, key: str) -> tuple[bool, int]:
        return self.allow(key)


_fallback_limiter = _LocalSlidingWindow(settings.rate_limit_rpm, RATE_LIMIT_WINDOW_SECONDS)


def _new_member() -> str:
    # A lightweight per-process unique token; uniqueness is best-effort only.
    return f"{time.time_ns()}:{os.getpid()}"


async def _check_rate_limit_redis(client_id: str) -> tuple[bool, int]:
    """Check rate limit using Redis sorted sets.

    Keeps one sorted set per client and prunes entries outside the rolling window.
    """
    from app.backends import get_redis

    redis_client = get_redis()
    key = f"{_LOCAL_LIMITER_PREFIX}:{client_id}"
    now_ms = int(time.time() * 1000)
    member = f"{now_ms}:{_new_member()}"

    allowed, _, remaining = await redis_client.eval(
        _RATE_LIMIT_SCRIPT,
        1,
        key,
        now_ms,
        RATE_LIMIT_WINDOW_SECONDS * 1000,
        settings.rate_limit_rpm,
        member,
    )
    return bool(int(allowed)), int(remaining)


async def check_rate_limit(client_id: str) -> tuple[bool, int]:
    """Check if `client_id` is within the rate limit.

    Returns ``(allowed, remaining_requests)``.
    """
    if not settings.redis_url:
        return _fallback_limiter.check(client_id)

    try:
        return await _check_rate_limit_redis(client_id)
    except Exception:
        return _fallback_limiter.check(client_id)
