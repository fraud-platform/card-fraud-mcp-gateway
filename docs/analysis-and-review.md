# Card Fraud MCP Gateway: Analysis and Review

**Date:** 2026-03-13
**Reviewer:** Technical Architecture Review
**Scope:** Full codebase review covering Phase 1 implementation, bug fixes, enterprise standards assessment, and test results

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Deep-Dive](#2-architecture-deep-dive)
3. [Bugs Found and Fixed](#3-bugs-found-and-fixed)
4. [Enterprise Standards Assessment](#4-enterprise-standards-assessment)
5. [Gaps and Recommendations](#5-gaps-and-recommendations)
6. [Test Results Summary](#6-test-results-summary)

---

## 1. Executive Summary

The Card Fraud MCP Gateway is a production-hardened, enterprise-grade MCP (Model Context Protocol) server that exposes 22 read-only tools, 7 resources, and 5 investigation prompts across six bounded domains: PostgreSQL, Redis, Kafka/Redpanda, MinIO/S3, platform control-plane APIs, and cross-domain operations workflows. The gateway bridges AI agents (including Claude, Cursor, and other MCP clients) to live fraud platform infrastructure under strict authentication, authorization, and audit controls.

### What Was Found

The Phase 1 implementation is architecturally sound and meets most enterprise standards for a read-only data gateway. The decorator-based tool registration pattern is consistent across all domains, the scope-to-tool mapping is complete and verifiable through the `/catalog` endpoint, and the security layers (JWT auth, rate limiting, redaction, SQL safety, allowlists) are all independently testable and independently exercisable. The code is well-structured and clearly oriented toward long-term maintainability.

Two critical runtime bugs were identified and fixed during the production hardening pass:

- **Bug 1 (MCP 500 on every request):** Starlette's `Mount()` does not propagate lifespan events to sub-apps. FastMCP's `StreamableHTTPSessionManager.run()` was never called, meaning the MCP session manager's internal `anyio` task group was never initialized. Every `/mcp` request immediately failed with a 500 internal server error.
- **Bug 2 (DNS rebinding protection blocks all external clients):** FastMCP auto-enables DNS rebinding protection when its default host (`127.0.0.1`) is used. This protection rejects requests where the `Host` header does not match the loopback allowlist. Any external MCP client — including all production deployments — would receive a 421 Invalid Host response on every request.

Both bugs have been fixed. The gateway was also subjected to a comprehensive hardening pass that resolved seven additional lower-severity issues (documented in section 3).

### Overall Verdict

**Phase 1 is production-ready for read-only access.** The gateway correctly enforces authentication, authorization, rate limiting, audit logging, secret redaction, SQL safety, request size limits, and Prometheus metrics export. The 297-test suite passes in full. The two critical runtime bugs that would have prevented any production use have been resolved. Outstanding gaps are clearly scoped to Phase 3 (write operations, approval workflows) and are not regressions.

---

## 2. Architecture Deep-Dive

The gateway is a single Python package (`app/`) built on FastMCP and Starlette. All components are async-first and dependency-free from one another except through explicit imports at registration time.

### Layer Overview

```
Client (MCP or HTTP)
       |
RequestSizeLimitMiddleware  (max 1 MB; 413 if exceeded)
       |
AuthMiddleware              (JWT validation, rate limit; 401/429 if rejected)
       |
CORSMiddleware              (disabled by default; opt-in per environment)
       |
Starlette Router
  ├── GET /health            (liveness; no auth)
  ├── GET /ready             (readiness; no auth; probes all backends)
  ├── GET /catalog           (tool/resource/prompt manifest; no auth)
  └── POST /mcp (Mount)      (FastMCP Streamable HTTP; all requests auth-checked upstream)
         |
    FastMCP Server
    ├── Domain Tools (22)    (@require_scope → @audit_tool → implementation)
    ├── Resources (7)        (ensure_scope → implementation)
    └── Prompts (5)          (templates only; no auth enforcement needed)
```

### 2.1 `app/main.py` — ASGI Factory, Middleware, Endpoints

`app/main.py` is the entry point for both the ASGI server and the CLI. It defines the full middleware stack, all non-MCP HTTP endpoints, the lifespan context manager, and the CLI entry points registered in `pyproject.toml`.

**RequestSizeLimitMiddleware** is an ASGI-native middleware that rejects requests before they reach the application. It operates in two modes: fast path (rejects by `Content-Length` header before reading any bytes) and streaming path (counts bytes as they arrive via a wrapped `receive` callable and raises `_RequestTooLargeError` if the running total exceeds `GATEWAY_MAX_REQUEST_BODY_BYTES`, defaulting to 1 MB). Security headers are injected at this layer regardless of whether the request is rejected.

**AuthMiddleware** is also ASGI-native. It extracts the `x-request-id` header (generating a UUID if absent) and the client IP, stores them in a `ContextVar` for the duration of the request, and then performs JWT validation via `authenticate_request()`. Three paths skip authentication: `/health`, `/ready`, and `/catalog`. On auth failure, a 401 JSON response with security headers is returned. On success, a per-client rate-limit check runs before the request is forwarded. On rate-limit exhaustion, a 429 response with `Retry-After: 60` is returned. The `finally` block always resets the `ContextVar` token, preventing identity bleed between requests.

**`_add_security_headers()`** appends five security headers to every response that does not already contain them: `x-content-type-options: nosniff`, `x-frame-options: DENY`, `referrer-policy: no-referrer`, `cache-control: no-store`, and `strict-transport-security: max-age=63072000; includeSubDomains`. The implementation appends rather than replaces, explicitly preserving duplicate headers (such as multiple `Set-Cookie` values) that might be legitimately set by upstream handlers.

**`lifespan()`** is an `asynccontextmanager` that handles startup and shutdown. On startup it calls `init_otel()`, `init_all()` (which initializes all backend connection pools concurrently), and then explicitly starts the FastMCP `StreamableHTTPSessionManager` via `async with sm.run()`. This last step is the fix for Bug 1 described in section 3. On shutdown (after `yield`), it calls `close_all()` to gracefully drain and close all backend pools.

**`/health`** returns `{"status": "ok", "service": "card-fraud-mcp-gateway"}`. It is a pure liveness check with no backend dependencies.

**`/ready`** probes all five backends concurrently (using `asyncio.gather` semantics through `_check_backend()`). Each backend reports `"ok"`, `"not_configured"`, or `"error"`. The overall `ready` field is true when all backends are either `ok` or `not_configured`. The S3 readiness check is a real connectivity probe (`list_buckets()`) rather than a configuration existence check — this was a fix applied during the hardening pass (see section 3).

**`/catalog`** returns the full tool/resource/prompt manifest as JSON. It reads `ToolPolicy` entries from the in-memory policy registry and introspects the FastMCP `_resource_manager` and `_prompt_manager` directly. This endpoint has no authentication, making it straightforward to inspect in monitoring tools and CI pipelines. See section 5 for the security trade-off discussion around this choice.

**CLI entry points** are `dev()`, `check()`, `smoke()`, and `export_catalog()`, registered as `doppler-local`, `gateway-check`, `gateway-smoke`, and `gateway-export-catalog` in `pyproject.toml`. The `dev()` function also enforces that `reload=True` is only used when the host is loopback, preventing accidental hot-reload in non-local environments.

### 2.2 `app/server.py` — FastMCP Server Factory

`create_mcp_server()` builds the `FastMCP` instance and registers all domain modules, resources, and prompts. It calls `clear_policies()` before registration to prevent stale `ToolPolicy` entries from accumulating across test runs where the function is called multiple times.

The `FastMCP` constructor receives `transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)`. This is the fix for Bug 2 (see section 3). The rationale is documented inline: the gateway is an enterprise remote server that binds to `0.0.0.0`, enforces its own JWT authentication, and runs behind a TLS-terminating reverse proxy. The FastMCP DNS rebinding protection is intended for local desktop AI tools that accept connections only on loopback; it is not appropriate for a production API server.

Domain registration order: platform, postgres, redis, kafka, storage, ops. Resources and prompts are registered last. All six domain modules are imported inside `create_mcp_server()` to avoid circular imports at module level.

### 2.3 `app/config.py` — Pydantic Settings

All gateway configuration is expressed as a single `Settings` class using `pydantic-settings`. The `env_prefix` is `GATEWAY_`, meaning every setting is read from an environment variable of that prefix (e.g., `GATEWAY_PG_DSN`). Two settings use `validation_alias` to follow the platform-wide naming convention without the prefix: `APP_ENV` and `SECURITY_SKIP_JWT_VALIDATION`.

The `_parse_list_env` validator handles list fields in two formats: JSON arrays (`["a","b"]`) and comma-separated strings (`a,b`). This allows Doppler to inject lists as either format without application-side parsing.

The `_validate_jwt_bypass` model validator enforces a critical security invariant: `SECURITY_SKIP_JWT_VALIDATION=true` is only permitted when `APP_ENV=local`. Attempting to bypass JWT validation in `APP_ENV=prod` raises a `ValueError` at startup and prevents the application from starting. This is a boot-time guardrail rather than a runtime check, meaning misconfigured production deployments fail loudly and immediately.

Key configuration fields:

| Field | Default | Purpose |
|-------|---------|---------|
| `pg_max_rows` | 500 | Hard row-count cap on all PostgreSQL queries |
| `redis_max_keys` | 100 | Maximum keys returned by `redis.scan_prefix` |
| `redis_max_value_bytes` | 10,000 | Value size cap before truncation |
| `kafka_max_messages` | 10 | Maximum messages returned by `kafka.peek_messages` |
| `kafka_max_payload_bytes` | 10,000 | Payload size cap before truncation |
| `s3_max_object_bytes` | 1,000,000 | Maximum bytes read by `s3.get_object` |
| `max_request_body_bytes` | 1,048,576 | Request body size cap (1 MB) |
| `rate_limit_rpm` | 120 | Requests per minute per `client_id` / `sub` |
| `cors_origins` | `[]` | CORS origins allowlist; empty = CORS disabled |
| `pg_statement_timeout_ms` | 5,000 | PostgreSQL statement timeout |
| `enforce_allowlists` | `True` | Whether empty allowlists block all access |

### 2.4 `app/backends.py` — BackendManager Pattern

All backend connections are managed through a single `BackendManager` class that wraps an optional connection object with a consistent `get()` / `close()` / `is_configured` interface.

`BackendManager.get()` raises `RuntimeError` with a descriptive message when the backend was not initialized (typically because the corresponding DSN environment variable was not set). This allows tools to fail with a clear error rather than with an `AttributeError` on `None`.

`BackendManager.close()` calls the supplied `close_fn` coroutine if one was provided and the instance is not `None`. PostgreSQL uses `lambda p: p.close()`, Redis uses `lambda c: c.aclose()`, Kafka uses `lambda c: c.stop()`. S3 has no `close_fn` because `aioboto3.Session` has no lifecycle method; S3 clients are opened and closed per-operation via `async with get_s3_session().client(...) as s3:`.

`init_all()` runs the four async initializers concurrently via `asyncio.gather(..., return_exceptions=True)`. Failures are logged as warnings but do not prevent startup — a pattern appropriate for a gateway where only a subset of backends may be available in any given environment.

**PostgreSQL** uses `asyncpg` with a connection pool. The connection options include `-c default_transaction_read_only=on`, which enforces read-only mode at the PostgreSQL session level in addition to the SQL safety validator in the application layer. This is defense in depth.

**Redis** uses `redis.asyncio.from_url()` with a 5-second socket timeout. The client has `decode_responses=True` so all string values are returned as Python `str` rather than `bytes`.

**Kafka** uses `AIOKafkaConsumer` as the shared metadata client. This client is started at initialization but does not subscribe to any topics. Individual tools that need per-topic operations (peek, lag) create their own ephemeral consumers via the consumer cache in `app/domains/kafka.py`.

**S3** uses `aioboto3.Session` stored as a module-level variable. The session holds credentials but does not hold any open connections. Each tool call opens and closes its own client via the async context manager protocol.

**Platform API** uses `httpx.AsyncClient` with a 10-second timeout and a `Bearer` token authorization header if `GATEWAY_PLATFORM_API_TOKEN` is configured.

### 2.5 `app/audit.py` — Structured Logging and OpenTelemetry

`structlog` is configured once at import time with a fixed processor chain: merge contextvars, add log level, ISO timestamps, stack info, exception formatting, and JSON rendering. All log output is structured JSON written to stdout, compatible with any log aggregation pipeline.

The `@audit_tool(domain)` decorator wraps every tool function. It records:

- The tool name and domain
- The caller's `sub` and `client_id`
- The sanitized keyword arguments (values longer than 200 characters are truncated to prevent log bloat)
- The request ID and source IP from the per-request `ContextVar`
- Elapsed time in milliseconds
- Whether the call succeeded

On success, `log.info("tool_ok")` is emitted with metadata about the result type and size (not the result contents). On failure, `log.warning("tool_error")` is emitted with the exception message. Neither path logs the actual tool output, which may contain sensitive data that the redaction layer handles separately.

The `tool_result()` helper serializes any Python object to indented JSON. It is used by all domain tools for consistent output formatting.

OpenTelemetry tracing is optional. If `GATEWAY_OTEL_ENDPOINT` is configured, `init_otel()` creates a `TracerProvider` with a `BatchSpanProcessor` and an `OTLPSpanExporter`. Each tool invocation becomes a span named `{domain}.{tool_name}` with attributes for the tool name, domain, caller subject, success status, source IP, and request ID. The `insecure` flag is derived from the endpoint URL scheme; `https://` endpoints use TLS.

### 2.6 `app/security/auth.py` — JWT Validation and Caller Identity

`CallerIdentity` is a frozen dataclass containing the caller's `sub` (subject), `scopes` (a `frozenset[str]`), `client_id`, `email`, and raw JWT claims. Using a frozen dataclass guarantees that the identity cannot be mutated after construction.

`_LOCAL_IDENTITY` is a module-level constant (not a mutable default) representing the local development identity. It carries all seven `fraud.*` scopes, allowing unrestricted access to all tools in local mode.

`_request_context` is a `ContextVar` with a default of `None` (not a mutable dict). This is important: using a mutable dict as a `ContextVar` default creates a single shared object that leaks state across requests. The `get_request_context()` function handles the `None` case by returning a fresh `{"request_id": None, "source_ip": None}` dict each time.

JWKS fetching uses double-checked locking with an `asyncio.Lock()` to prevent thundering herd on token validation. The TTL is 3600 seconds (1 hour). On a cache miss for a `kid` (key ID), the JWKS cache is force-refreshed once before rejecting the token, handling key rotation events gracefully without causing an outage.

JWT validation uses `python-jwt` with RS256. The `audience` and `issuer` claims are validated against the configured Auth0 tenant. The `scope` claim is split on whitespace and converted to a `frozenset`.

`authenticate_request()` implements the two-path auth model: local dev (returns `_LOCAL_IDENTITY` when `skip_jwt_validation=True`) and production (extracts the `Bearer` token and validates it). There is no third path; any request without a valid token in non-local mode raises `PermissionError`.

### 2.7 `app/security/policy.py` — Scope Enforcement and ToolPolicy Registry

`ToolPolicy` is a frozen dataclass with four fields: `domain`, `scope`, `read_only` (default `True`), and `approval_required` (default `False`, scaffolded for Phase 3). The `approval_required` field has no runtime enforcement in Phase 1.

`_policies` is a module-level dict mapping tool names to `ToolPolicy` instances. It is populated by the `@require_scope()` decorator at tool registration time and cleared by `clear_policies()` at the start of each `create_mcp_server()` call.

`@require_scope(scope, domain=..., read_only=..., tool_name=...)` is a decorator that:
1. Registers a `ToolPolicy` for the decorated function at decoration time.
2. Wraps the function to call `_check_scope()` before every invocation.

`_check_scope(scope, *, domain, tool)` is the centralized enforcement function. It retrieves the current caller from the `ContextVar`, checks whether `scope` is in `caller.scopes`, and raises `PermissionError` with a descriptive message (including the caller's actual scopes) if not. It also emits a structured audit log event on denial.

`ensure_scope(scope, *, domain, name)` is the resource-access variant of scope enforcement, forwarding its arguments to `_check_scope()` with the `name` parameter mapped to `tool=`. This forwarding was the site of a historical bug (see section 3, Bug 1 in the hardening table).

### 2.8 `app/security/ratelimit.py` — Sliding-Window Rate Limiter

Rate limiting uses a Redis sorted-set sliding window. Each client gets a key `gateway:ratelimit:{client_id}`. A Lua script atomically: removes entries older than the window, counts current entries, rejects if at capacity, and adds the new entry with the current timestamp as the score. The TTL on the sorted set is set to the window length, preventing unbounded key growth.

The fallback in-process limiter (`_LocalSlidingWindow`) uses a `dict[str, deque[float]]` and runs a periodic cleanup every 5 seconds to prune expired entries. It is used when Redis is not configured or when the Redis call fails.

The Lua script returns a three-element array: `[allowed, current_count, remaining]`. The Python caller extracts `allowed` and `remaining`.

### 2.9 `app/security/redaction.py` — Secret and PII Redaction

`redact(text)` applies a single compiled `re.compile()` pattern with nine named groups against any string. The groups and their replacement tokens are:

| Pattern Group | Matches | Replacement |
|---------------|---------|-------------|
| `kv` | `password=`, `secret=`, `token=`, `api_key=`, etc. | `{key}=***REDACTED***` |
| `bearer` | `Bearer <token>` | `Bearer ***REDACTED***` |
| `email` | Standard email addresses | `***REDACTED_EMAIL***` |
| `phone` | Phone numbers (international and local formats) | `***REDACTED_PHONE***` |
| `pem` | PEM certificate/key blocks | `***REDACTED_PEM***` |
| `cc` | Visa, Mastercard, Amex, Discover card numbers | `***REDACTED_CARD***` |
| `cvv` | CVV/CVV2/CVC values | `cvv=***REDACTED_CVV***` |
| `ssn` | US Social Security Numbers (NNN-NN-NNNN format) | `***REDACTED_SSN***` |
| `aws` | AWS access key IDs (AKIA...) | `***REDACTED_AWS_KEY***` |
| `conn` | DSN connection strings (`://user:pass@`) | `://***:***@` |

`redact_dict(data)` recursively walks a dict, applying `redact()` to string values and replacing entire values when the key matches a set of sensitive key names (`password`, `secret`, `token`, `api_key`, `apikey`, `credential`, `auth`).

All tool output that contacts external backends passes through `redact()` before being returned. This is a defense-in-depth measure: the backend credentials are themselves never exposed to tool code, but the tool outputs may contain user-supplied data that contains sensitive values.

### 2.10 `app/security/allowlist.py` — Config-Driven Access Control

Three allowlist enforcement functions operate consistently:

- `check_exact(item, allowed, ...)`: rejects items not present in `allowed` (exact match). Used for PostgreSQL schemas, Kafka topics, Kafka consumer groups, and S3 buckets.
- `check_prefix(item, allowed, ...)`: rejects items that do not start with any string in `allowed`. Used for Redis key prefixes.
- `check_path_prefix(bucket, prefix, allowed, ...)`: constructs the path `bucket/prefix` and checks it against the allowed prefix list. Used for S3 bucket/prefix combinations.

When `enforce_allowlists=True` (the default) and an allowlist is configured but empty, all items are blocked. When `enforce_allowlists=True` but no allowlist is configured, the behavior is to block everything (fail-closed). When `enforce_allowlists=False` and no allowlist is configured, all items are permitted (fail-open, appropriate for development).

`filter_by_allowlist(items, allowed)` is used for list operations (e.g., topic listing) to silently omit items not in the allowlist rather than raising an error.

### 2.11 `app/domains/platform.py` — 4 Tools

The platform domain provides a static service inventory with an optional override from a YAML file (`GATEWAY_SERVICES_FILE`). The built-in inventory contains six services: `card-fraud-platform`, `card-fraud-api`, `card-fraud-engine`, `card-fraud-dashboard`, `card-fraud-ops-analyst-agent`, and `card-fraud-mcp-gateway`.

| Tool | Scope | Description |
|------|-------|-------------|
| `platform.inventory` | `fraud.platform.read` | Lists all services with type, owner, port |
| `platform.service_status` | `fraud.platform.read` | Calls `GET /api/v1/services/{name}/status` on the platform API |
| `platform.service_health` | `fraud.platform.read` | Calls `GET /api/v1/services/{name}/health` on the platform API |
| `platform.ownership_summary` | `fraud.platform.read` | Groups services by owner and type |

`platform.service_status` and `platform.service_health` degrade gracefully when the platform API is not configured or unreachable: they return a structured result with `status: "unknown"` rather than raising an exception.

### 2.12 `app/domains/postgres.py` — 4 Tools + SQL Safety

The postgres domain is the most complex, combining schema discovery tools with a bounded query executor and a multi-layer SQL safety system.

| Tool | Scope | Description |
|------|-------|-------------|
| `postgres.list_schemas` | `fraud.db.read` | Lists non-system schemas, filtered by allowlist |
| `postgres.list_tables` | `fraud.db.read` | Lists tables in a schema after allowlist check |
| `postgres.describe_table` | `fraud.db.read` | Returns column metadata from `information_schema` |
| `postgres.query_readonly` | `fraud.db.read` | Executes bounded SELECT/WITH/EXPLAIN queries |

**SQL Safety (`_validate_sql`)** enforces three rules:
1. The SQL (after comment and literal stripping) must not contain any forbidden keywords (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `GRANT`, `REVOKE`, and 15 others).
2. The SQL must not contain multiple statements (semicolon-separated after stripping comments and literals).
3. The normalized first token must be `SELECT`, `WITH`, or `EXPLAIN`.

The comment and literal stripping (`_strip_comments_and_literals`) handles `--` line comments, `/* */` block comments, single-quoted strings (with escape sequences and doubled-quote escapes), and both `$$` and `$tag$` dollar-quoted strings. This prevents injection via embedded keywords in quoted strings.

**Query Allowlist Enforcement (`_enforce_query_allowlists`)** extracts all table references from the query and verifies each against the configured `pg_allowed_schemas` and `pg_allowed_tables`. For simple SELECT/FROM/JOIN queries, table extraction uses a regex (`_TABLE_RE`) to avoid a round-trip to the database. For complex queries (CTEs, UNION, subqueries, LATERAL), it falls back to running `EXPLAIN (VERBOSE, FORMAT JSON)` against the database and parsing the plan tree. Extracted relations are cached in `_RELATION_CACHE` (bounded to 500 entries with LRU eviction) to avoid repeated EXPLAIN calls for the same query text.

**Row Bounding** wraps user queries in `WITH _q AS ({sql}) SELECT * FROM _q LIMIT {max_rows + 1}`. Fetching one row beyond the limit allows the tool to detect truncation without a separate `COUNT(*)` query. The `EXPLAIN` path is not wrapped.

All query results pass through `redact()` before being returned.

### 2.13 `app/domains/redis.py` — 4 Tools

| Tool | Scope | Description |
|------|-------|-------------|
| `redis.scan_prefix` | `fraud.redis.read` | Iterates keys matching `{prefix}*` up to `redis_max_keys` |
| `redis.get_key` | `fraud.redis.read` | Reads a key's value; handles string/hash/list/set/zset types |
| `redis.ttl` | `fraud.redis.read` | Returns TTL in seconds (-1=persistent, -2=missing) |
| `redis.type` | `fraud.redis.read` | Returns the data type of a key |

`redis.get_key` uses a single pipeline to fetch all possible type-specific values in one round-trip, then selects the appropriate value based on the type response. This avoids the pattern of issuing a `TYPE` command and then a type-specific command, which would be two separate round-trips. Values are truncated at `redis_max_value_bytes` (10 KB). All values pass through `redact()`.

All tools check the key or prefix against `redis_allowed_prefixes` via `check_prefix()` before interacting with Redis.

### 2.14 `app/domains/kafka.py` — 4 Tools

| Tool | Scope | Description |
|------|-------|-------------|
| `kafka.list_topics` | `fraud.kafka.read` | Lists non-internal topics, filtered by allowlist |
| `kafka.describe_topic` | `fraud.kafka.read` | Returns partition count and metadata |
| `kafka.peek_messages` | `fraud.kafka.read` | Reads up to `kafka_max_messages` recent messages |
| `kafka.consumer_lag` | `fraud.kafka.read` | Computes committed vs. log-end offsets per partition |

The consumer cache (`_consumer_cache`) avoids the 200-500 ms AIOKafka startup overhead for repeated tool calls. Consumers are keyed by `_ConsumerKey(group_id, enable_auto_commit, auto_offset_reset)` and reused for up to 60 seconds. Stale entries are stopped and evicted lazily when a new consumer is requested. The stop operation is properly `await`-ed — the absence of this `await` was a historical bug (see section 3).

`kafka.peek_messages` seeks to `end - max_messages` on each partition and calls `getmany(timeout_ms=3000)`. The `enable_auto_commit=False` setting ensures that peek operations do not advance any consumer group's committed offsets.

All message payloads are decoded as UTF-8 (with `errors="replace"` for binary data) and truncated at `kafka_max_payload_bytes`. Payloads pass through `redact()`.

`list_visible_topics()` is a module-level function (not a tool) used by both the `kafka.list_topics` tool and the `/ready` readiness check.

### 2.15 `app/domains/storage.py` — 4 Tools

| Tool | Scope | Description |
|------|-------|-------------|
| `s3.list_buckets` | `fraud.storage.read` | Lists buckets, filtered by `s3_allowed_buckets` |
| `s3.list_objects` | `fraud.storage.read` | Lists up to 100 objects with optional prefix |
| `s3.head_object` | `fraud.storage.read` | Returns object metadata without downloading content |
| `s3.get_object` | `fraud.storage.read` | Downloads object content (text types only, max 1 MB) |

All four tools use `async with get_s3_session().client("s3", endpoint_url=settings.s3_endpoint) as s3:` to open and close a fresh client per operation. This is required by `aioboto3`'s context manager protocol and was the fix for the aioboto3 misuse bug (see section 3).

`s3.get_object` enforces two additional safety checks: it rejects binary content types (only `json`, `text`, `yaml`, `xml`, `csv`, `plain` are accepted) and rejects objects larger than `s3_max_object_bytes`. The `Range: bytes=0-{max}` header is sent to avoid downloading the entire object before the size check.

`s3.head_object` passes the response metadata through `redact_dict()` to redact any sensitive values in user-defined object metadata fields.

### 2.16 `app/domains/ops.py` — 2 Tools

The ops domain provides cross-domain investigation workflows that query PostgreSQL and Redis concurrently.

| Tool | Scope | `read_only` | Description |
|------|-------|-------------|-------------|
| `ops.get_investigation_context` | `fraud.ops.investigation.read` | `True` | Gathers transaction/case/score/flags context |
| `ops.run_investigation` | `fraud.ops.investigation.run` | `False` | Runs structured investigation workflows |

`ops.get_investigation_context` accepts a `transaction_id` or `case_id` (at least one required), queries PostgreSQL for the corresponding record(s), and queries Redis for `fraud:score:{id}` and `fraud:flags:{id}`. Both backend calls are graceful: if PostgreSQL or Redis is unavailable, a note field is added to the result rather than an exception being raised. This allows partial results to be returned when only some backends are healthy.

`ops.run_investigation` supports three investigation types: `transaction_review` (fetches transaction and its decisions), `case_triage` (fetches case record), and `velocity_check` (counts transactions for a card in the last 24 hours). This tool is marked `read_only=False` in its `ToolPolicy` because the investigation type implies future write capabilities (e.g., updating case status). In Phase 1, all three types are read-only queries.

Both tools use `_build_select_sql()` to construct queries with explicit column lists from config rather than `SELECT *`. Table names from config are validated through `_validate_identifier()` and the PostgreSQL allowlist before being embedded in SQL strings, preventing identifier injection.

All results pass through `redact()`.

### 2.17 `app/resources.py` — 7 MCP Resources

MCP resources are addressable static or dynamic data sources exposed via the `fraud://` URI scheme. All resources enforce scope authorization via `ensure_scope()` before serving any data.

| URI | Scope Required | Description |
|-----|----------------|-------------|
| `fraud://platform/services` | `fraud.platform.read` | Full service inventory as JSON |
| `fraud://platform/ownership` | `fraud.platform.read` | Services grouped by owner team |
| `fraud://schemas/catalog` | `fraud.db.read` | All tables across non-system schemas |
| `fraud://topics/catalog` | `fraud.kafka.read` | All visible Kafka topics |
| `fraud://buckets/catalog` | `fraud.storage.read` | All visible S3 buckets |
| `fraud://health/topology` | `fraud.platform.read` | Backend configuration topology |
| `fraud://ops/investigation-context` | `fraud.ops.investigation.read` | Investigation bundle descriptor |

Resources that require backend connectivity (`schemas/catalog`, `topics/catalog`, `buckets/catalog`) catch all exceptions and return a structured error object rather than propagating the exception to the MCP session.

### 2.18 `app/prompts.py` — 5 Investigation Prompts

Prompts are parameterized text templates that guide AI agents through multi-step workflows. They carry no authentication enforcement (prompts are read-only templates; the actual tool calls they recommend will enforce scope when executed).

| Prompt Name | Parameters | Purpose |
|-------------|-----------|---------|
| `investigate-transaction` | `transaction_id` | 5-step transaction investigation workflow |
| `explain-decision-trace` | `transaction_id` | Trace fraud decision through rules and model |
| `triage-platform-health` | none | Full platform health check across all services |
| `inspect-ruleset-artifact` | `bucket`, `prefix` | Inspect fraud ruleset files in S3 |
| `review-consumer-lag` | `group_id` | Kafka consumer lag review for fraud pipelines |

All prompts reference specific tool names by their dot-notation identifiers, making them self-documenting guides for AI agents on which tools to call and in what order.

---

## 3. Bugs Found and Fixed

### Bug 1: MCP Endpoint Returns 500 on Every Request

**Severity:** Critical — all MCP protocol traffic blocked.

**Symptom:** `POST /mcp` with any valid MCP request returns HTTP 500. The session manager's internal `anyio` task group raises `RuntimeError: This nursery is not currently open` on the first request.

**Root Cause:** Starlette's `Mount()` does not propagate lifespan startup/shutdown events to sub-apps. The gateway mounts the FastMCP ASGI app via `Mount("/", app=mcp_app)`. FastMCP's `streamable_http_app()` creates a `StreamableHTTPSessionManager` and wraps it in a Starlette sub-application with its own lifespan that calls `sm.run()`. Because that sub-app lifespan never fires, `sm.run()` is never called, so the `anyio` task group inside the session manager is never entered. The first time any MCP request arrives, the handler tries to operate on the unopened task group and raises.

**Fix (in `app/main.py`, `lifespan()`):**

```python
sm = getattr(_main_mcp, "_session_manager", None)
if sm is not None:
    async with sm.run():
        yield
else:
    yield
```

By calling `sm.run()` directly from the parent Starlette lifespan context manager, the session manager's task group is entered before the first request arrives and exited during clean shutdown. The `getattr` with `None` default ensures this is safe if the FastMCP internal API changes.

**Test Coverage:** `test_e2e_runtime.py::TestMcpSession::test_initialize` verifies that an MCP `initialize` request succeeds end-to-end with a 200 status and a valid `mcp-session-id` header. All `TestMcpSession` and `TestPlatformTools` tests would fail without this fix.

---

### Bug 2: DNS Rebinding Protection Blocks All External Clients (421 Invalid Host)

**Severity:** Critical — all external MCP clients rejected in any non-loopback deployment.

**Symptom:** MCP clients connecting to the gateway over any address other than `127.0.0.1` or `localhost` receive a 421 Misdirected Request or an error indicating the host header is not allowed.

**Root Cause:** `FastMCP` automatically enables DNS rebinding protection when instantiated. This protection validates the `Host` header against an allowlist of permitted hosts. The default allowlist only contains `127.0.0.1` and `localhost`. The protection is designed for the Claude Desktop local server use case, where a web page served from a different origin could make requests to a locally bound MCP server. It is not designed for remote enterprise APIs.

When the gateway is deployed in any production or staging environment and an MCP client connects with a `Host: gateway.example.com` header (as all HTTP/1.1 clients must), FastMCP rejects the request before it reaches the gateway's own auth middleware.

**Fix (in `app/server.py`, `create_mcp_server()`):**

```python
mcp = FastMCP(
    "card-fraud-gateway",
    ...,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
```

**Justification:** DNS rebinding protection solves the problem of a browser making cross-origin requests to a locally bound server. The Card Fraud MCP Gateway is not a locally bound server. It:
1. Binds to `0.0.0.0` and is accessible over a network.
2. Enforces Auth0 JWT authentication on every MCP request via its own middleware.
3. Is deployed behind a TLS-terminating reverse proxy that validates the hostname.

The Auth0 JWT authentication already provides a stronger defense against unauthorized access than DNS rebinding protection. A request that spoofs a `Host` header but lacks a valid JWT will be rejected by the `AuthMiddleware` before reaching any tool handler.

**Test Coverage:** `test_e2e_runtime.py::TestMcpSession::test_initialize` and all subsequent MCP protocol tests validate that the session can be established from a `TestClient`, which presents an arbitrary host header. Without this fix, all those tests would receive 421 responses.

---

### Additional Hardening Fixes (Phase 1 Production Pass)

Seven additional issues were found and fixed during the broader hardening review:

| # | Bug | File | Impact Before Fix |
|---|-----|------|-------------------|
| 3 | `ensure_scope` passed `name=` instead of `tool=` to `_check_scope` | `security/policy.py` | Every MCP resource access raised `TypeError` at runtime; resources were completely unusable |
| 4 | asyncpg pool had no `close_fn` | `backends.py` | PostgreSQL connections leaked on shutdown; pool not drained gracefully |
| 5 | aioboto3 stored context manager instead of session | `backends.py`, `storage.py` | All S3 tool calls raised `AttributeError` at runtime; S3 domain was completely unusable |
| 6 | Kafka stale-consumer cleanup missing `await` | `domains/kafka.py` | Consumer coroutine was created but never awaited; stale consumers accumulated indefinitely, exhausting Kafka connections |
| 7 | `_add_security_headers` dropped duplicate `Set-Cookie` headers | `main.py` | Cookie-based auth flows (if used by a future auth integration) could lose cookies silently |
| 8 | Mutable dict as `ContextVar` default | `security/auth.py` | The shared default dict could leak request metadata across concurrent requests in certain concurrency edge cases |
| 9 | `_RELATION_CACHE` had no size bound | `domains/postgres.py` | Memory grew unbounded under varied query workloads; long-running gateway instances would exhaust memory |
| 10 | S3 readiness check did not probe real connectivity | `main.py` | `/ready` always reported S3 as `ok` even when the MinIO endpoint was unreachable; load balancers would not detect the failure |
| 11 | `cors_origins` defaulted to `["http://localhost:3000"]` | `config.py` | The development CORS origin leaked into production configurations; all production environments would inadvertently accept cross-origin requests from localhost |

Each of these fixes has corresponding regression tests in `tests/test_hardening.py`.

---

## 4. Enterprise Standards Assessment

### 4.1 Authentication and Authorization

**Status: Strong.**

Authentication uses Auth0 JWT RS256 with JWKS-based key discovery. The JWKS cache uses double-checked locking and auto-refreshes on key ID miss, handling key rotation without an outage. The `validate_token()` function verifies audience, issuer, algorithm, and expiration. The `CallerIdentity` object is immutable (frozen dataclass with `frozenset` for scopes).

Authorization uses a scope-per-tool model. Every tool declares exactly one required scope via `@require_scope()`. The scope format is `fraud.{domain}.{action}`. The enforcement path is synchronous (no await), runs before the tool function body, and is not bypassable through decorator ordering because `@require_scope` wraps the function returned by `@audit_tool` — the scope check runs on every invocation regardless of the audit log result.

The `APP_ENV=prod` + `SECURITY_SKIP_JWT_VALIDATION=true` combination is blocked at startup by Pydantic model validator, preventing accidental auth bypass in production.

**Gap:** Auth is enforced at the HTTP middleware layer and again at the tool decorator layer, but MCP resources (`fraud://` URIs) use `ensure_scope()` inline rather than a decorator. This is functionally equivalent but is a different code path and slightly less obvious to reviewers. Consider wrapping resource handlers with a decorator equivalent in Phase 2.

### 4.2 Rate Limiting

**Status: Good.**

120 requests per minute per `client_id` / `sub` is enforced at the middleware layer before any request reaches the MCP server. The Redis sliding-window implementation is atomic (Lua script) and prevents thundering herd on the counter. The fallback in-process limiter ensures rate limiting still functions when Redis is unreachable.

The rate limit key uses `client_id` when available (M2M tokens), falling back to `sub` (human tokens). This correctly scopes limits to the client application rather than the individual user for M2M scenarios.

**Gap:** The rate limit is global across all tools. A caller who exhausts the rate limit on cheap `platform.inventory` calls cannot reach the `postgres.query_readonly` tool. Consider per-scope or per-domain rate limits in Phase 2 for finer control.

### 4.3 Audit Logging

**Status: Strong.**

Every tool invocation produces a structured JSON log event with: tool name, domain, caller subject, caller client ID, sanitized arguments, request ID, source IP, elapsed time, and success/failure status. The audit log is correlation-ID-aware (uses `x-request-id`) and supports distributed tracing correlation via OpenTelemetry when configured.

Authorization denials produce a separate `authorization_denied` log event in `_check_scope()`, making failed access attempts visible independently of tool invocation logs.

Arguments are sanitized before logging (values over 200 characters are truncated) and the result content is never logged (only metadata: type and size). This prevents sensitive data from appearing in log streams.

**Gap:** There is no log event for authentication failures at the middleware layer. The 401 response is returned but not logged. Adding an `auth_failure` event in `AuthMiddleware` would improve traceability for security investigations.

### 4.4 Secret Redaction

**Status: Strong.**

The `redact()` function covers the patterns most likely to appear in fraud platform data: API keys, tokens, passwords, DSN connection strings, AWS access key IDs, email addresses, phone numbers, PEM blocks, credit card numbers, CVV values, and US Social Security Numbers. The pattern is compiled once at import time and uses a single-pass substitution, making it efficient for large result sets.

`redact_dict()` extends coverage to structured data by key-name matching in addition to value-pattern matching.

All domain tools that fetch user-controlled data (PostgreSQL query results, Redis values, Kafka message payloads, S3 object contents) call `redact()` on the output before returning.

**Gap:** The regex for phone numbers is broad and may produce false positives on numeric sequences that are not phone numbers (e.g., tracking IDs or amounts). This is an acceptable trade-off for a security-first gateway, but analysts may occasionally see legitimate numeric data replaced with `***REDACTED_PHONE***`. The pattern could be refined if this causes operational friction.

### 4.5 SQL Safety

**Status: Strong.**

SQL safety has three independent layers:

1. **Keyword blocklist:** Rejects any SQL containing forbidden DML/DDL/TCL keywords after stripping comments and quoted literals.
2. **Statement count:** Rejects multi-statement queries.
3. **Allowed prefixes:** Requires queries to start with `SELECT`, `WITH`, or `EXPLAIN`.
4. **Session-level read-only:** The asyncpg pool sets `default_transaction_read_only=on` at the connection level, making it impossible for application code to issue writes even if the SQL safety validator were somehow bypassed.

Table allowlist enforcement uses regex-based extraction for simple queries and falls back to `EXPLAIN` plan parsing for complex queries, covering CTEs, UNION, subqueries, and LATERAL joins.

**Gap:** The `EXPLAIN` fallback incurs a database round-trip for complex queries. This is intentional and safe, but it does add latency. The `_RELATION_CACHE` mitigates this for repeated identical queries.

### 4.6 Input Validation

**Status: Good.**

Pydantic model validation covers all configuration values. The `@field_validator` for list fields handles both JSON and CSV formats. The `_validate_jwt_bypass` model validator blocks unsafe configuration at startup.

Tool input parameters are typed Python function arguments, with Pydantic validation applied by FastMCP before the tool function is called. Identifier injection is prevented in the ops domain through `_validate_identifier()` which enforces `^[A-Za-z_][A-Za-z0-9_]*$` before any identifier is embedded in a SQL string.

Request body size is enforced at 1 MB by `RequestSizeLimitMiddleware` before the request body is read.

**Gap:** Tool function parameters are validated by type only. There is no length limit enforcement on string parameters (e.g., a very long `sql` argument to `postgres.query_readonly` is accepted). The 1 MB request body limit provides an outer bound, but explicit parameter-level validation would provide more precise error messages.

### 4.7 Security Headers

**Status: Strong.**

Five security headers are injected on every response: `x-content-type-options: nosniff`, `x-frame-options: DENY`, `referrer-policy: no-referrer`, `cache-control: no-store`, and `strict-transport-security: max-age=63072000; includeSubDomains` (2-year HSTS with subdomains). Headers are injected at both the `RequestSizeLimitMiddleware` and `AuthMiddleware` layers, ensuring they are present on 413 and 401/429 responses respectively.

The implementation appends headers rather than replacing them, preserving existing security headers set upstream and avoiding duplicate-header bugs with `Set-Cookie`.

**Gap:** `Content-Security-Policy` is not set. The gateway serves only JSON and SSE, not HTML, so CSP is not strictly necessary. If a future version adds a web UI, CSP should be added.

### 4.8 CORS

**Status: Correct.**

`cors_origins` defaults to `[]` (empty list), which disables CORS. This is the right default for an API server. The `_require_secure_cors()` function ensures `allow_credentials=True` is not set when the origin list contains `"*"` (which would be rejected by browsers anyway), preventing the common misconfiguration of wildcarded credentialed CORS.

Environments that need CORS must explicitly set `GATEWAY_CORS_ORIGINS` in their Doppler configuration.

### 4.9 Error Handling

**Status: Good.**

All tool functions return structured JSON on both success and error paths. Tools that require backends return graceful error results (a JSON object with an `"error"` key) rather than propagating Python exceptions to the MCP session layer. The MCP protocol `isError=True` field is used for fatal tool errors (e.g., scope violations, which propagate as `PermissionError` and are caught by FastMCP and converted to MCP error responses). Backend availability errors use the graceful return path.

This distinction is important: `isError=True` tells an AI agent to stop and report the problem; a graceful return with `"error"` in the JSON tells the agent that the tool executed but the data was unavailable, which may warrant a retry or a different approach.

No unhandled exceptions should produce HTTP 500 responses on the `/mcp` endpoint in normal operation. The test suite specifically validates this (`TestBackendToolsGracefulError`).

---

## 5. Gaps and Recommendations

### 5.1 No Write or Mutation Tools (Phase 3 Pending)

The gateway is read-only in Phase 1. The `ops.run_investigation` tool is marked `read_only=False` in its `ToolPolicy` and requires the `fraud.ops.investigation.run` scope, establishing the authorization boundary for future write operations. However, the tool itself performs only read queries in Phase 1.

The `approval_required` field on `ToolPolicy` is scaffolded but unenforced. Phase 3 should implement an approval workflow that checks this field before permitting `read_only=False` tool calls, integrating with the platform's human-in-the-loop review system.

**Recommendation:** Before adding any write tools, implement `approval_required` enforcement in `_check_scope()` or as a separate decorator layer. Write tools should also emit distinct audit log events and may warrant per-call logging to an immutable audit store (not just stdout).

### 5.2 No MCP Native OAuth (Custom Auth0 Instead)

The MCP specification defines an OAuth 2.1 authorization server flow for MCP server authentication. The gateway uses a custom Auth0 JWT middleware instead. This means MCP clients that implement the MCP native OAuth discovery flow (checking `/.well-known/oauth-authorization-server`) will not be able to automatically negotiate credentials.

Clients that support custom Bearer token injection (Cursor, most programmatic clients) work fine. Claude Desktop may require manual token configuration.

**Recommendation:** In Phase 2, add the MCP OAuth discovery endpoints (`/.well-known/oauth-authorization-server`, `/authorize`, `/token`) that delegate to Auth0's Authorization Code + PKCE flow. This enables zero-configuration client onboarding.

### 5.3 `ops.run_investigation` Is RW but Has No Approval Workflow

As noted in section 5.1, `ops.run_investigation` is the only tool with `read_only=False`. In Phase 1 it only reads data. In Phase 3, when write capabilities are added (e.g., updating case status, flagging transactions), the absence of an approval workflow creates a risk that an AI agent could make changes without human review.

**Recommendation:** Block `ops.run_investigation` from any operation that modifies data until the approval workflow in `approval_required` is implemented and enforced.

### 5.4 No Integration Tests Against Real Backends

The test suite is entirely unit and contract tests. No test exercises a live PostgreSQL, Redis, Kafka, or S3 connection. The Docker Compose environment (`docker-compose up` with `card-fraud-platform`) is described in the README but there are no automated integration tests that run against it.

This means backend-specific behaviors (query plan variations, Redis cluster mode, Kafka compacted topics, S3 path-style addressing) are not tested. A regression in backend interaction would only be caught in a staging environment.

**Recommendation:** Add a `tests/integration/` directory with a `conftest.py` that skips all tests unless `GATEWAY_PG_DSN` (and other DSNs) are set. These tests would run in CI against the Docker Compose stack. Target: one integration test per tool, exercising the actual backend interaction.

### 5.5 No Distributed Tracing Correlation Between MCP Sessions and Backend Calls

OpenTelemetry tracing is implemented in `audit.py` and creates spans for each tool invocation. However, there is no trace context propagation from the MCP session into the backend calls themselves. A PostgreSQL query executed by `postgres.query_readonly` does not carry the parent trace ID, so the database server's activity cannot be correlated to the MCP tool call in a distributed trace.

**Recommendation:** Instrument `asyncpg`, `aiokafka`, and `httpx` calls with child spans that carry the parent trace context from the `@audit_tool` span. The `opentelemetry-instrumentation-asyncpg` and `opentelemetry-instrumentation-httpx` packages provide auto-instrumentation for this.

### 5.6 The `/catalog` Endpoint Exposes Internal Tool and Scope Structure Without Authentication

`GET /catalog` returns the full list of tools, scopes, domains, and read/write flags without requiring any authentication. This is useful for monitoring, documentation generation (`gateway-export-catalog`), and client configuration, but it also reveals the complete security model of the gateway to any unauthenticated caller.

An attacker who discovers the gateway can learn which scopes exist, which tools are read-only vs. read-write, and what domains are available — all without a valid JWT.

**Recommendation:** Consider adding optional authentication to `/catalog` (e.g., require auth when `APP_ENV=prod`). Alternatively, remove `approval_required` from the catalog output since it reveals future write surface area. For most enterprise deployments, the gateway URL is not publicly reachable, making this a low-severity information disclosure risk.

---

## 6. Test Results Summary

**Total: 297 tests, all passing.**

| Suite | Tests | Category |
|-------|-------|----------|
| `test_policy.py` | 8 | Unit — scope enforcement, policy registration |
| `test_redaction.py` | 12 | Unit — redaction patterns for all secret types |
| `test_sql_safety.py` | 24 | Unit — SQL validation, keyword blocking, multi-statement detection |
| `test_allowlists.py` | 15 | Unit — exact/prefix/path allowlist enforcement, empty/configured states |
| `test_app.py` | 11 | Integration — health, readiness, catalog, size limit middleware |
| `test_audit.py` | 9 | Unit — `@audit_tool` decorator, log emission, OTel span creation |
| `test_audit_extended.py` | 8 | Unit — argument sanitization, result metadata extraction |
| `test_hardening.py` | 13 | Regression — one test class per hardening fix (bugs 3-11 in section 3) |
| `test_server.py` | 6 | Unit — server factory, tool count, domain coverage, policy clearing |
| `test_auth.py` | 10 | Unit — JWT validation, JWKS cache, local identity, context vars |
| `test_config.py` | 8 | Unit — settings parsing, list field formats, prod JWT bypass guard |
| `test_backends.py` | 9 | Unit — BackendManager lifecycle, init_all, close_all |
| `test_platform_domain.py` | 8 | Unit — inventory, service status/health graceful degradation |
| `test_postgres_domain.py` | 14 | Unit — list schemas/tables/describe, SQL safety, allowlist enforcement |
| `test_postgres_query_access.py` | 12 | Unit — EXPLAIN fallback, relation cache, CTE/UNION/subquery detection |
| `test_redis_domain.py` | 9 | Unit — scan, get (all types), ttl, type |
| `test_kafka_domain.py` | 11 | Unit — list topics, describe, peek messages, consumer lag |
| `test_kafka.py` | 8 | Unit — consumer cache, stale eviction, stop-and-remove |
| `test_storage_domain.py` | 10 | Unit — list buckets/objects, head/get object, binary type rejection |
| `test_ops_domain.py` | 12 | Unit — investigation context, run investigation (all 3 types), identifier validation |
| `test_ops.py` | 6 | Unit — _build_select_sql, _resolve_table_expr, _get_ops_columns |
| `test_resources.py` | 8 | Unit — all 7 resources, scope enforcement, graceful backend errors |
| `test_main.py` | 9 | Unit — auth middleware, rate limit path, security header injection |
| `test_cli.py` | 4 | Unit — check(), smoke(), export_catalog() entry points |
| `test_e2e_runtime.py` | **32** | **End-to-end — full HTTP + MCP protocol stack** |

### End-to-End Test Coverage (`test_e2e_runtime.py`)

The 32 end-to-end tests are organized into six classes and exercise the real `create_app()` / `TestClient` stack with no mocking of any middleware or MCP layer:

**`TestHttpEndpoints` (7 tests)**
- `GET /health` returns 200 with correct service name
- `GET /ready` with no backends configured returns `ready: true` with all backends `not_configured`
- `GET /catalog` returns exactly 22 tools, 7 resources, and 5 prompts
- `/catalog` contains all 22 expected tool names by exact match
- All five security headers are present on `/health` responses
- `/health` succeeds even when an invalid `Authorization` header is provided
- `POST /mcp` with a 1 MB+ body returns 413 with `request_too_large` error

**`TestMcpSession` (6 tests)**
- MCP `initialize` request succeeds and returns a valid `mcp-session-id`
- `tools/list` returns exactly 22 tools
- `resources/list` returns exactly 7 resources
- `prompts/list` returns exactly 5 prompts
- All 22 tools have non-empty `description` fields
- All 7 resource URIs use the `fraud://` scheme

**`TestPlatformTools` (4 tests)**
- `platform.inventory` returns a result containing `services`
- `platform.ownership_summary` returns without error
- `platform.service_status` for `fraud-engine` returns without error
- `platform.service_health` for `fraud-engine` returns without error

**`TestBackendToolsGracefulError` (5 tests)**
- `postgres.list_schemas`, `postgres.list_tables`, `redis.scan_prefix`, `kafka.list_topics`, and `s3.list_buckets` all return HTTP 200 (not 500) when their backends are not configured — verifying the graceful degradation path

**`TestPrompts` (3 tests)**
- `investigate-transaction` prompt returns messages containing the provided `transaction_id`
- `triage-platform-health` prompt returns non-empty messages
- `review-consumer-lag` prompt succeeds with a `consumer_group` argument

**`TestPolicyAndSecurity` (7 tests)**
- Policy registry contains exactly 22 entries after `create_app()`
- All tool scopes start with `fraud.`
- Complete scope matrix verification (all 22 tool-to-scope mappings checked by name)
- All tools are `read_only=True` except `ops.run_investigation`
- `ops.run_investigation` has `read_only=False`
- Caller with only `fraud.db.read` scope is denied when attempting a `fraud.kafka.read` tool
- Caller with `fraud.kafka.read` scope is permitted for `fraud.kafka.read` tools

---

*This document reflects the state of the codebase as of 2026-03-13. It should be updated when tool contracts, scopes, or security behavior changes.*
