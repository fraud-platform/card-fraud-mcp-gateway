"""Application entry point — ASGI app factory, auth middleware, health endpoints, CLI."""

from __future__ import annotations

import asyncio
import posixpath
import sys
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import cast
from urllib.parse import unquote, urlsplit

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from app.audit import init_otel, init_sentry
from app.backends import close_all, init_all
from app.config import settings
from app.metrics import (
    observe_http_request,
    record_auth_failure,
    record_rate_limited,
    record_request_too_large,
    render_prometheus_metrics,
)
from app.security.auth import (
    authenticate_request,
    reset_request_context,
    set_caller,
    set_request_context,
)
from app.security.ratelimit import check_rate_limit

_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),
)
_main_mcp = None


def _add_security_headers(message: dict[str, object]) -> None:
    """Append missing security headers; never removes or deduplicates existing headers."""
    if message["type"] != "http.response.start":
        return
    response_headers = cast(list[tuple[bytes, bytes]], message.get("headers", []))
    present = {h[0].lower() for h in response_headers}
    extra = [(k, v) for k, v in _SECURITY_HEADERS if k not in present]
    if extra:
        message["headers"] = response_headers + extra


def _normalize_request_path(request: Request) -> str:
    raw_path = request.scope.get("raw_path")
    path = unquote(raw_path.decode()) if raw_path else urlsplit(str(request.url)).path
    normalized = posixpath.normpath(path)
    return normalized if normalized != "." else "/"


# ---- Request Size Limit Middleware (ASGI) ----


class _RequestTooLargeError(Exception):
    """Raised when a request body exceeds configured limits."""

    pass


class RequestSizeLimitMiddleware:
    """ASGI middleware — rejects request bodies exceeding the configured limit."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = None
        for header_name, header_value in scope.get("headers", []):
            if header_name == b"content-length":
                try:
                    content_length = int(header_value)
                except ValueError:
                    break
                break

        if content_length is not None and content_length > self.max_bytes:
            record_request_too_large()
            response = JSONResponse(
                {
                    "error": "request_too_large",
                    "detail": f"Request body ({content_length} bytes) exceeds "
                    f"limit ({self.max_bytes} bytes).",
                },
                status_code=413,
            )
            response.raw_headers = response.raw_headers + list(_SECURITY_HEADERS)
            await response(scope, receive, send)
            return

        consumed_bytes = 0

        async def limited_receive():
            nonlocal consumed_bytes
            message = await receive()
            if message["type"] == "http.request":
                consumed_bytes += len(message.get("body", b""))
                if consumed_bytes > self.max_bytes:
                    raise _RequestTooLargeError()
            return message

        try:

            async def send_with_security_headers(message: dict[str, object]) -> None:
                _add_security_headers(message)
                await send(message)

            await self.app(scope, limited_receive, send_with_security_headers)
        except _RequestTooLargeError:
            record_request_too_large()
            response = JSONResponse(
                {
                    "error": "request_too_large",
                    "detail": (
                        f"Request body exceeds the configured limit ({self.max_bytes} bytes)."
                    ),
                },
                status_code=413,
            )
            response.raw_headers = response.raw_headers + list(_SECURITY_HEADERS)
            await response(scope, receive, send)


# ---- Auth Middleware (ASGI) ----


class AuthMiddleware:
    """ASGI middleware — validates JWT on all HTTP/WebSocket requests except health/readiness."""

    SKIP_PATHS = frozenset({"/health", "/ready", "/catalog", "/metrics"})

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        source_ip = request.client.host if request.client else None
        request_meta = set_request_context(request_id, source_ip)

        path = _normalize_request_path(request)
        if path in self.SKIP_PATHS:
            reset_request_context(request_meta)
            await self.app(scope, receive, send)
            return

        try:
            caller = await authenticate_request(request)
            set_caller(caller)
        except PermissionError as exc:
            record_auth_failure("unauthorized")
            response = JSONResponse({"error": "unauthorized", "detail": str(exc)}, status_code=401)
            response.raw_headers = response.raw_headers + list(_SECURITY_HEADERS)
            await response(scope, receive, send)
            reset_request_context(request_meta)
            return

        # Rate limiting (per client_id or sub)
        rate_key = caller.client_id or caller.sub
        allowed, _remaining = await check_rate_limit(rate_key)
        if not allowed:
            record_rate_limited()
            response = JSONResponse(
                {"error": "rate_limited", "detail": "Too many requests. Try again later."},
                status_code=429,
                headers={"Retry-After": "60", "X-RateLimit-Remaining": "0"},
            )
            response.raw_headers = response.raw_headers + list(_SECURITY_HEADERS)
            await response(scope, receive, send)
            reset_request_context(request_meta)
            return

        async def send_with_security_headers(message: dict[str, object]) -> None:
            _add_security_headers(message)
            await send(message)

        try:
            await self.app(scope, receive, send_with_security_headers)
        finally:
            reset_request_context(request_meta)


class ObservabilityMiddleware:
    """ASGI middleware for request count/latency metrics."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.monotonic()
        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "unknown")
        status_code = 500

        async def send_with_status(message: dict[str, object]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        finally:
            elapsed = time.monotonic() - start
            observe_http_request(
                method=method,
                path=path,
                status_code=status_code,
                elapsed_seconds=elapsed,
            )


# ---- Lifespan ----


@asynccontextmanager
async def lifespan(_app: Starlette) -> AsyncIterator[None]:
    """Manage backend connections lifecycle."""
    init_otel(settings.otel_endpoint, settings.otel_service_name)
    init_sentry(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment or settings.app_env.value,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
        send_default_pii=settings.sentry_send_default_pii,
    )
    await init_all()
    # Starlette does NOT propagate lifespan events to sub-apps mounted via Mount().
    # FastMCP's streamable_http_app() has its own lifespan that calls
    # session_manager.run() — but since it's a sub-mount, that lifespan never fires.
    # We must explicitly start the session manager here so its anyio task group is
    # initialized before the first MCP request arrives.
    sm = getattr(_main_mcp, "_session_manager", None)
    if sm is not None:
        async with sm.run():
            yield
    else:
        yield
    await close_all()


# ---- Health / Readiness / Catalog ----


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "card-fraud-mcp-gateway"})


async def _check_s3_connectivity() -> None:
    """Probe S3 by performing a list_buckets call to verify real connectivity."""
    from app.backends import get_s3_session

    async with get_s3_session().client("s3", endpoint_url=settings.s3_endpoint) as s3:
        await s3.list_buckets()


async def _check_backend(checker: Callable[[], object], *, configured: bool) -> str:
    if not configured:
        return "not_configured"
    try:
        result = checker()
        if hasattr(result, "__await__"):
            await result
        return "ok"
    except Exception:
        return "error"


async def readiness(_request: Request) -> JSONResponse:
    from app.backends import (
        _kafka_manager,
        _pg_manager,
        _platform_manager,
        _redis_manager,
        _s3_manager,
    )
    from app.domains.kafka import list_visible_topics

    checks: dict[str, str] = {
        "postgres": await _check_backend(
            lambda: _pg_manager.get().fetchval("SELECT 1"), configured=_pg_manager.is_configured
        ),
        "redis": await _check_backend(
            lambda: _redis_manager.get().ping(), configured=_redis_manager.is_configured
        ),
        "kafka": await _check_backend(
            lambda: list_visible_topics(_kafka_manager.get()),
            configured=_kafka_manager.is_configured,
        ),
        "s3": await _check_backend(_check_s3_connectivity, configured=_s3_manager.is_configured),
        "platform_api": await _check_backend(
            lambda: _platform_manager.get(),
            configured=_platform_manager.is_configured,
        ),
    }

    healthy = all(v in ("ok", "not_configured") for v in checks.values())
    return JSONResponse(
        {"ready": healthy, "backends": checks},
        status_code=200 if healthy else 503,
    )


async def catalog(_request: Request) -> JSONResponse:
    """Export the full tool/resource/prompt catalog as JSON for documentation and audit."""
    from app.security.policy import get_all_policies

    mcp = _get_main_mcp()

    policies = get_all_policies()
    tool_items = [
        {
            "tool": name,
            "domain": p.domain,
            "scope": p.scope,
            "read_only": p.read_only,
            "approval_required": p.approval_required,
        }
        for name, p in sorted(policies.items())
    ]

    resource_names = sorted(
        str(getattr(r, "uri", r)) for r in mcp._resource_manager.list_resources()
    )
    prompt_names = sorted(str(getattr(p, "name", p)) for p in mcp._prompt_manager.list_prompts())

    return JSONResponse(
        {
            "tools": tool_items,
            "tool_count": len(tool_items),
            "resources": resource_names,
            "resource_count": len(resource_names),
            "prompts": prompt_names,
            "prompt_count": len(prompt_names),
        }
    )


async def metrics(_request: Request) -> Response:
    if not settings.metrics_enabled:
        return Response(status_code=404)
    payload, content_type = render_prometheus_metrics()
    return Response(content=payload, media_type=content_type)


def _get_main_mcp():
    """Return a cached MCP app instance."""
    global _main_mcp

    if _main_mcp is None:
        from app.server import create_mcp_server

        _main_mcp = create_mcp_server()
    return _main_mcp


def _require_secure_cors(origins: list[str]) -> bool:
    """Ensure credentials are never sent with wildcard origins."""
    if not origins:
        return False
    return "*" not in origins


# ---- App Factory ----


def create_app() -> Starlette:
    """Create the ASGI application with MCP server, auth middleware, and ops endpoints."""
    global _main_mcp

    mcp = _get_main_mcp()
    _main_mcp = mcp
    mcp_app = mcp.streamable_http_app()

    allow_credentials = _require_secure_cors(settings.cors_origins)
    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/ready", readiness, methods=["GET"]),
        Route("/catalog", catalog, methods=["GET"]),
    ]
    if settings.metrics_enabled:
        routes.append(Route("/metrics", metrics, methods=["GET"]))
    routes.append(Mount("/", app=mcp_app))

    inner = Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=settings.cors_origins,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["Authorization", "Content-Type"],
                allow_credentials=allow_credentials,
            ),
        ],
    )

    app = AuthMiddleware(inner)
    app = RequestSizeLimitMiddleware(app, max_bytes=settings.max_request_body_bytes)
    return ObservabilityMiddleware(app)


app = create_app()


# ---- CLI entry points (registered in pyproject.toml [project.scripts]) ----


def dev() -> None:
    """Run the gateway in local development mode."""
    import uvicorn

    # Windows + Proactor can emit noisy connection-reset callbacks during shutdown.
    # Apply selector loop for local dev runs started via gateway-dev only.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    print(f"Card Fraud MCP Gateway - http://{settings.host}:{settings.port}")
    auth_mode = "JWT bypass (local dev)" if settings.skip_jwt_validation else "Auth0 JWT required"
    print(f"  Auth:    {auth_mode}")
    print(f"  Env:     {settings.app_env}")
    print(f"  MCP:     http://{settings.host}:{settings.port}/mcp")
    print(f"  Health:  http://{settings.host}:{settings.port}/health")
    print(f"  Catalog: http://{settings.host}:{settings.port}/catalog")
    allow_reload = settings.debug and settings.host in {"127.0.0.1", "localhost"}
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=allow_reload,
        log_level="info",
    )


def check() -> None:
    """Verify configuration and print tool catalog."""
    print("Card Fraud MCP Gateway - Configuration Check")
    print("=" * 60)
    print(f"  Host / Port:     {settings.host}:{settings.port}")
    auth_mode = "JWT bypass (APP_ENV=local)" if settings.skip_jwt_validation else "Auth0 JWT"
    print(f"  Auth mode:       {auth_mode}")
    print(f"  App env:         {settings.app_env}")
    print(f"  Auth0 domain:    {settings.auth0_domain or '(not set)'}")
    print(f"  PostgreSQL:      {'configured' if settings.pg_dsn else '(not set)'}")
    print(f"  Redis:           {'configured' if settings.redis_url else '(not set)'}")
    print(f"  Kafka:           {'configured' if settings.kafka_brokers else '(not set)'}")
    print(f"  S3 / MinIO:      {'configured' if settings.s3_endpoint else '(not set)'}")
    print(f"  Platform API:    {'configured' if settings.platform_api_url else '(not set)'}")
    print(f"  OTel endpoint:   {'configured' if settings.otel_endpoint else '(not set)'}")
    print(f"  Metrics:         {'enabled' if settings.metrics_enabled else 'disabled'}")
    print("  Sentry PII:      " + ("enabled" if settings.sentry_send_default_pii else "disabled"))
    print()

    from app.server import create_mcp_server

    create_mcp_server()

    from app.security.policy import get_all_policies

    policies = get_all_policies()
    print(f"Registered tools: {len(policies)}")
    print("-" * 60)
    for name, p in sorted(policies.items()):
        rw = "RO" if p.read_only else "RW"
        print(f"  {name:<40} {p.scope:<35} {rw}")
    print()


def smoke() -> None:
    """Run a basic smoke test: boot the app and hit health/ready/catalog endpoints."""
    from starlette.testclient import TestClient

    print("Card Fraud MCP Gateway - Smoke Test")
    print("=" * 60)

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)

    checks = [
        ("GET /health", "GET", "/health"),
        ("GET /ready", "GET", "/ready"),
        ("GET /catalog", "GET", "/catalog"),
    ]
    passed = 0
    for label, method, path in checks:
        resp = client.request(method, path)
        status = "OK" if resp.status_code == 200 else "FAIL"
        print(f"  {status} {label} -> {resp.status_code}")
        if resp.status_code == 200:
            passed += 1

    print()
    print(f"Result: {passed}/{len(checks)} checks passed")
    if passed < len(checks):
        raise SystemExit(1)


def export_catalog() -> None:
    """Export the full tool/resource/prompt catalog as JSON to stdout."""
    import json as _json

    from starlette.testclient import TestClient

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/catalog")
    if resp.status_code != 200:
        print(f"Error: /catalog returned {resp.status_code}", file=sys.stderr)
        raise SystemExit(1)
    print(_json.dumps(resp.json(), indent=2))
