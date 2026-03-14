"""Application-wide constants for timeouts, limits, and conversion factors."""

from __future__ import annotations

from typing import Final

# ---- Time Conversion Factors ----
MILLISECONDS_PER_SECOND: Final[int] = 1000

# ---- Timeouts (seconds) ----
TIMEOUT_SECONDS: Final[int] = 10
SOCKET_TIMEOUT_SECONDS: Final[int] = 5

# ---- Timeouts (milliseconds) ----
KAFKA_CONSUMER_TIMEOUT_MS: Final[int] = 5000
KAFKA_FETCH_TIMEOUT_MS: Final[int] = 3000

# ---- Cache TTLs (seconds) ----
JWKS_CACHE_TTL_SECONDS: Final[float] = 3600.0  # 1 hour
RATE_LIMIT_WINDOW_SECONDS: Final[int] = 60  # 1 minute

# ---- Query/Scan Limits ----
REDIS_SCAN_COUNT: Final[int] = 100
REDIS_LIST_RANGE_LIMIT: Final[int] = 100
S3_MAX_KEYS: Final[int] = 100
