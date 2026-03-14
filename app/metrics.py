"""Prometheus metrics for gateway observability."""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

_HTTP_REQUESTS_TOTAL = Counter(
    "gateway_http_requests_total",
    "Total HTTP requests handled by the gateway.",
    ("method", "path", "status"),
)

_HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "gateway_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ("method", "path", "status"),
)

_AUTH_FAILURES_TOTAL = Counter(
    "gateway_auth_failures_total",
    "Authentication/authorization failures.",
    ("reason",),
)

_RATE_LIMITED_TOTAL = Counter(
    "gateway_rate_limited_total",
    "Requests denied by rate limiting.",
)

_REQUEST_TOO_LARGE_TOTAL = Counter(
    "gateway_request_too_large_total",
    "Requests denied because body exceeded configured max bytes.",
)

_TOOL_INVOCATIONS_TOTAL = Counter(
    "gateway_tool_invocations_total",
    "Tool invocations by domain/tool and success state.",
    ("domain", "tool", "success"),
)

_TOOL_DURATION_SECONDS = Histogram(
    "gateway_tool_duration_seconds",
    "Tool invocation latency in seconds.",
    ("domain", "tool"),
)

_BACKEND_INIT_FAILURES_TOTAL = Counter(
    "gateway_backend_init_failures_total",
    "Backend initialization failures.",
    ("backend",),
)

_RESOURCE_READ_FAILURES_TOTAL = Counter(
    "gateway_resource_read_failures_total",
    "Resource read failures.",
    ("resource",),
)

_RESULT_TRUNCATIONS_TOTAL = Counter(
    "gateway_result_truncations_total",
    "Result truncation or size-limit events.",
    ("domain", "tool", "reason"),
)


def observe_http_request(method: str, path: str, status_code: int, elapsed_seconds: float) -> None:
    status = str(status_code)
    _HTTP_REQUESTS_TOTAL.labels(method=method, path=path, status=status).inc()
    _HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=path, status=status).observe(
        elapsed_seconds
    )


def record_auth_failure(reason: str) -> None:
    _AUTH_FAILURES_TOTAL.labels(reason=reason).inc()


def record_rate_limited() -> None:
    _RATE_LIMITED_TOTAL.inc()


def record_request_too_large() -> None:
    _REQUEST_TOO_LARGE_TOTAL.inc()


def record_tool_invocation(domain: str, tool: str, success: bool, elapsed_seconds: float) -> None:
    _TOOL_INVOCATIONS_TOTAL.labels(domain=domain, tool=tool, success=str(success).lower()).inc()
    _TOOL_DURATION_SECONDS.labels(domain=domain, tool=tool).observe(elapsed_seconds)


def record_backend_init_failure(backend: str) -> None:
    _BACKEND_INIT_FAILURES_TOTAL.labels(backend=backend).inc()


def record_resource_read_failure(resource: str) -> None:
    _RESOURCE_READ_FAILURES_TOTAL.labels(resource=resource).inc()


def record_result_truncation(domain: str, tool: str, reason: str, count: int = 1) -> None:
    _RESULT_TRUNCATIONS_TOTAL.labels(domain=domain, tool=tool, reason=reason).inc(count)


def render_prometheus_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
