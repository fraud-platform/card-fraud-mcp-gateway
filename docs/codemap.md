# Codemap

## Purpose

`card-fraud-mcp-gateway` provides a single enterprise MCP surface over the Card Fraud suite.

## Runtime Structure

```
app/
  __init__.py               # Package marker
  main.py                   # ASGI app factory, auth middleware, health endpoints, CLI
  config.py                 # Pydantic settings from GATEWAY_* env vars
  server.py                 # FastMCP server factory — registers all tools/resources/prompts
  backends.py               # Backend pool lifecycle (Postgres, Redis, Kafka, S3, Platform)
  audit.py                  # Structured audit logging (structlog) + OpenTelemetry tracing
  metrics.py                # Prometheus counters/histograms + /metrics payload renderer
  security/
    __init__.py
    auth.py                 # Auth0 JWT validation, CallerIdentity, contextvar propagation
    policy.py               # @require_scope decorator, ToolPolicy registry
    allowlist.py            # Config-driven allowlist enforcement (schemas, prefixes, topics, buckets)
    ratelimit.py            # Redis sliding-window rate limiter with in-memory fallback
    redaction.py            # Secret pattern redaction (card numbers, tokens, SSNs, etc.)
  domains/
    __init__.py
    platform.py             # 4 tools: inventory, service_status, service_health, ownership
    postgres.py             # 4 tools: list_schemas, list_tables, describe_table, query_readonly
    redis.py                # 4 tools: scan_prefix, get_key, ttl, type
    kafka.py                # 4 tools: list_topics, describe_topic, peek_messages, consumer_lag
                            #   + _ConsumerKey/_CachedConsumer cache (60 s TTL, keyed by config)
    storage.py              # 4 tools: list_buckets, list_objects, head_object, get_object
    ops.py                  # 2 tools: get_investigation_context, run_investigation
  resources.py              # 7 MCP resources (fraud:// URIs)
  prompts.py                # 5 MCP prompt templates
tests/
  conftest.py               # Shared fixtures (dev settings, caller identities)
  test_app.py               # ASGI app, catalog, and request-size middleware coverage
  test_allowlists.py        # Allowlist helper coverage across all domains
  test_auth.py              # Auth middleware, JWKS cache, and rate-limit coverage
  test_config.py            # Settings parsing
  test_kafka.py             # Kafka metadata helper coverage
  test_ops.py               # Ops investigation helper coverage
  test_policy.py            # Scope-based authorization
  test_postgres_query_access.py  # Raw SQL allowlist enforcement via EXPLAIN JSON
  test_sql_safety.py        # SQL validation (allowed/rejected patterns)
  test_redaction.py         # Secret pattern matching
  test_audit.py             # Audit decorator behavior
  test_server.py            # Tool registration and catalog integrity
  test_hardening.py         # Regression tests for production hardening fixes (13 tests)
```

## Key Patterns

- **Tool registration**: Each domain module exports `register(mcp: FastMCP)` called by `server.py`
- **Auth flow**: HTTP middleware → JWT validation → contextvar → `@require_scope` check
- **Resource guards**: MCP resources enforce the same domain scopes and allowlists as the tool layer via `ensure_scope()`
- **S3 access**: Always `async with get_s3_session().client("s3", ...) as s3:` — session is shared, client is per-call
- **Audit trail**: `@audit_tool` decorator logs every invocation with timing and sanitized args
- **Backend lifecycle**: `backends.init_all()` / `backends.close_all()` managed by ASGI lifespan
- **Redaction**: All tool outputs pass through `redact()` before returning to clients
- **Metrics**: `/metrics` exposes request, auth, tool, truncation, backend-init, and resource-failure signals

## External Source Of Truth

The gateway should consume these platform-owned artifacts instead of duplicating ownership logic:

- `card-fraud-platform/control-plane/services.yaml`
- `card-fraud-platform/control-plane/ownership/database.yaml`
- `card-fraud-platform/control-plane/ownership/messaging.yaml`
- `card-fraud-platform/control-plane/ownership/storage.yaml`
- `card-fraud-platform/control-plane/ownership/secrets.yaml`
- sibling `platform-adapter.yaml` files
