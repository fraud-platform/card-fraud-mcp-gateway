"""Structured audit logging and OpenTelemetry tracing."""

from __future__ import annotations

import functools
import json
import time
from collections.abc import Callable
from contextlib import nullcontext
from typing import Any
from urllib.parse import urlparse

import structlog

from app.metrics import record_tool_invocation
from app.security.auth import get_caller, get_request_context
from app.security.redaction import redact


def tool_result(data: Any, *, default: Any = None) -> str:
    """Format tool result as JSON with consistent indentation.

    Used by all domain tools to return structured output.
    Pass default=str to handle non-serializable types (e.g. datetime, Decimal).
    """
    return json.dumps(data, indent=2, default=default)


logger = structlog.get_logger("audit")

_SENSITIVE_ARG_TOKENS = (
    "authorization",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
)


structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(0),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)


def audit_tool(domain: str) -> Callable:
    """Decorator that logs tool invocations with timing and caller info."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            caller = get_caller()
            tool_name = fn.__name__
            start = time.monotonic()
            result: Any | None = None
            error_msg: str | None = None
            context = get_request_context()
            tracer = get_tracer()
            span_context = (
                tracer.start_as_current_span(f"{domain}.{tool_name}") if tracer else nullcontext()
            )

            with span_context as span:
                try:
                    result = await fn(*args, **kwargs)
                    return result
                except Exception as exc:
                    error_msg = redact(str(exc))
                    if span is not None:
                        span.set_attribute("error", True)
                        span.record_exception(exc)
                    raise
                finally:
                    if span is not None:
                        span.set_attribute("tool", tool_name)
                        span.set_attribute("domain", domain)
                        span.set_attribute("caller_sub", caller.sub)
                        span.set_attribute("success", error_msg is None)
                        if error_msg:
                            from opentelemetry.trace.status import Status, StatusCode

                            span.set_status(Status(StatusCode.ERROR, error_msg))

                        span.set_attribute("source_ip", context.get("source_ip") or "")
                        span.set_attribute("request_id", context.get("request_id") or "")

                    elapsed_ms = (time.monotonic() - start) * 1000
                    elapsed_seconds = elapsed_ms / 1000
                    success = error_msg is None
                    record_tool_invocation(
                        domain=domain,
                        tool=tool_name,
                        success=success,
                        elapsed_seconds=elapsed_seconds,
                    )
                    log = logger.bind(
                        event="tool_invocation",
                        tool=tool_name,
                        domain=domain,
                        caller_sub=caller.sub,
                        caller_client=caller.client_id,
                        args=_safe_args(kwargs),
                        request_id=context.get("request_id"),
                        source_ip=context.get("source_ip"),
                        elapsed_ms=round(elapsed_ms, 2),
                        success=success,
                    )
                    if error_msg:
                        log.warning("tool_error", error=error_msg)
                    else:
                        log.info("tool_ok", **_result_metadata(result))

        return wrapper

    return decorator


def _safe_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Strip large values from args for logging."""
    return {k: _sanitize_value(k, v) for k, v in kwargs.items()}


def _sanitize_value(key: str, value: Any) -> Any:
    if any(token in key.lower() for token in _SENSITIVE_ARG_TOKENS):
        return "***REDACTED***"

    if isinstance(value, str):
        cleaned = redact(value)
        return cleaned[:200] + "...(truncated)" if len(cleaned) > 200 else cleaned

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for k, v in list(value.items())[:20]:
            result[str(k)] = _sanitize_value(str(k), v)
        if len(value) > 20:
            result["_truncated_keys"] = len(value) - 20
        return result

    if isinstance(value, list):
        items = [_sanitize_value(key, v) for v in value[:20]]
        if len(value) > 20:
            items.append(f"...(truncated {len(value) - 20} items)")
        return items

    if isinstance(value, tuple):
        return tuple(_sanitize_value(key, v) for v in value[:20])

    return value


def _result_metadata(result: Any) -> dict[str, Any]:
    """Extract metadata about the result without logging full content."""
    if result is None:
        return {"result_type": "none"}
    if isinstance(result, str):
        return {"result_type": "str", "result_length": len(result)}
    if isinstance(result, list):
        return {"result_type": "list", "result_count": len(result)}
    if isinstance(result, dict):
        return {"result_type": "dict", "result_keys": len(result)}
    return {"result_type": type(result).__name__}


# ---- OpenTelemetry ----

_tracer = None


def init_otel(endpoint: str, service_name: str) -> None:
    """Initialize OpenTelemetry tracing if an endpoint is configured."""
    if not endpoint:
        return

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    parsed_endpoint = urlparse(endpoint)
    insecure = parsed_endpoint.scheme != "https"
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    global _tracer
    _tracer = trace.get_tracer(service_name)


def get_tracer():
    return _tracer


# ---- Sentry Error Tracking ----

_sentry_initialized = False


def init_sentry(
    dsn: str,
    environment: str,
    traces_sample_rate: float = 0.1,
    profiles_sample_rate: float = 0.0,
    send_default_pii: bool = False,
) -> None:
    """Initialize Sentry error tracking if a DSN is configured."""
    if not dsn:
        return

    import sentry_sdk
    from sentry_sdk.integrations.asyncpg import AsyncPgIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.redis import RedisIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    global _sentry_initialized
    if _sentry_initialized:
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=environment or "production",
        integrations=[
            FastApiIntegration(),
            StarletteIntegration(),
            RedisIntegration(),
            AsyncPgIntegration(),
        ],
        traces_sample_rate=traces_sample_rate,
        profiles_sample_rate=profiles_sample_rate,
        send_default_pii=send_default_pii,
        # Filter sensitive data from breadcrumbs
        before_breadcrumb=_sentry_before_breadcrumb,
        before_send=_sentry_before_send,
    )
    _sentry_initialized = True


def _sanitize_sentry_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive key/value pairs from Sentry payload maps."""
    sensitive = {"authorization", "password", "token", "cookie", "set-cookie", "api-key"}
    result: dict[str, Any] = {}
    for key, value in data.items():
        key_str = str(key)
        if key_str.lower() in sensitive:
            result[key_str] = "***REDACTED***"
            continue
        if isinstance(value, str):
            result[key_str] = redact(value)
        elif isinstance(value, dict):
            result[key_str] = _sanitize_sentry_mapping(value)
        elif isinstance(value, list):
            result[key_str] = [redact(v) if isinstance(v, str) else v for v in value[:50]]
        else:
            result[key_str] = value
    return result


def _sentry_before_breadcrumb(
    breadcrumb: dict[str, Any],
    hint: dict[str, Any],
) -> dict[str, Any] | None:
    """Filter sensitive data from Sentry breadcrumbs."""
    del hint
    data = breadcrumb.get("data", {})
    if isinstance(data, dict):
        breadcrumb["data"] = _sanitize_sentry_mapping(data)
    return breadcrumb


def _sentry_before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Filter sensitive data from top-level Sentry events."""
    del hint
    request = event.get("request")
    if isinstance(request, dict):
        if isinstance(request.get("headers"), dict):
            request["headers"] = _sanitize_sentry_mapping(request["headers"])
        if isinstance(request.get("data"), dict):
            request["data"] = _sanitize_sentry_mapping(request["data"])
        elif isinstance(request.get("data"), str):
            request["data"] = redact(request["data"])

    user = event.get("user")
    if isinstance(user, dict):
        # Keep stable user id, redact direct identifiers.
        for key in ("email", "ip_address", "username"):
            if key in user:
                user[key] = "***REDACTED***"

    return event


def is_sentry_initialized() -> bool:
    """Check if Sentry has been initialized."""
    return _sentry_initialized
