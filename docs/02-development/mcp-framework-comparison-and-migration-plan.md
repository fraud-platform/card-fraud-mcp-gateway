# MCP Framework Comparison and Deferred FastMCP Migration Plan

## Decision Status

The production gateway remains on the official MCP Python SDK. The standalone
FastMCP framework is not being introduced in the current change.

Current dependency and server API:

- package: `mcp>=2.2,<3`
- server: `mcp.server.mcpserver.MCPServer`
- transport: Streamable HTTP at `POST /mcp`

The codebase must call this class `MCPServer`. It must not alias `MCPServer` to
`FastMCP`, because that implies the separately installed `fastmcp` framework is
in use when it is not.

## Why the Names Are Confusing

FastMCP 1 was incorporated into the official MCP Python SDK. In version 2 of
the official SDK, its high-level server API is named `MCPServer`. Separately,
the actively maintained FastMCP project is installed from the `fastmcp`
package and exposes `fastmcp.FastMCP`.

Both provide decorator-based tools, resources, and prompts. They are related,
but they are not the same dependency or runtime framework.

Primary references:

- [Official MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
- [Standalone FastMCP](https://github.com/PrefectHQ/fastmcp)
- [FastMCP release history](https://github.com/PrefectHQ/fastmcp/blob/main/docs/updates.mdx)

## Current Gateway Compared with Standalone FastMCP

| Concern | Official MCP SDK in this gateway | Standalone FastMCP |
|---|---|---|
| Package | `mcp` | `fastmcp` |
| Main server class | `MCPServer` | `FastMCP` |
| Protocol implementation | Official SDK | Framework built on the MCP ecosystem |
| Tools, resources, prompts | Decorator registration | Decorator registration plus providers and transforms |
| Streamable HTTP | Supported | Supported |
| ASGI integration | Gateway owns the outer Starlette application | HTTP application and deployment helpers included |
| Authentication | Gateway-owned Auth0 JWT validation and scope policy | Built-in JWT/OAuth providers and auth middleware |
| Audit, redaction, and allowlists | Gateway-owned security controls | Middleware is available, but domain controls remain application-owned |
| Composition and proxying | Manual | Providers, proxying, mounting, transforms, and namespacing |
| OpenAPI-generated tools | Not used | Built-in OpenAPI provider |
| Component versioning and state | Application-owned where needed | Framework facilities available |
| Interactive MCP applications | Not used | FastMCP Apps supported |

## Why the Official SDK Remains Appropriate Today

The gateway already has a tested security and runtime boundary around MCP:

- Auth0 M2M JWT validation and per-tool scopes
- structured audit logging and OpenTelemetry traces
- recursive secret and PII redaction
- PostgreSQL, Redis, Kafka, and S3 allowlists
- read-only SQL enforcement and result bounds
- request-size, host, origin, and security-header controls
- `/health`, `/ready`, `/catalog`, and `/metrics` operational endpoints
- explicit backend lifecycle management

A framework migration would not remove these requirements. Each control would
need a documented FastMCP equivalent or would remain custom middleware. The
current official SDK implementation is therefore not considered obsolete or a
defect.

## When Migration Becomes Worthwhile

Revisit FastMCP when one or more of these requirements becomes concrete:

- dynamically generate tools from OpenAPI or platform ownership metadata;
- proxy or compose several MCP servers behind this gateway;
- namespace, filter, transform, or version components dynamically;
- add persistent per-user or per-session state;
- add interactive MCP Apps;
- replace enough custom transport/auth plumbing to reduce overall complexity;
- adopt a protocol capability that is available in FastMCP but not yet practical
  through the official SDK.

Do not migrate solely to change the server class name or reproduce the current
catalog with another decorator API.

## Deferred Migration Plan

This plan is intentionally future work. None of these phases is implemented by
the current change.

### Phase 0: Decision and Compatibility Review

1. Record an ADR selecting a specific stable FastMCP major and version range.
2. Compare its transitive dependencies and vulnerability report with the
   current `mcp` lock.
3. Confirm support for Python 3.14, Streamable HTTP, Codex, and Claude clients.
4. Map every current security control to a FastMCP facility or an explicitly
   retained gateway control.
5. Decide whether FastMCP owns JWT verification or consumes the existing
   verified caller identity. Do not maintain two competing auth paths.

### Phase 1: Isolated Compatibility Spike

1. Create a non-production server factory using `fastmcp.FastMCP`.
2. Register one representative tool, resource, and prompt.
3. Mount it behind the existing Starlette boundary.
4. Prove Auth0 scope denial, audit logging, redaction, request bounds, and
   graceful backend lifecycle behavior.
5. Measure startup time, memory, response latency, and container size.

The spike must not replace the production `/mcp` route.

### Phase 2: Catalog and Client Parity

1. Register all 22 tools, 7 resources, and 5 prompts through FastMCP.
2. Compare exported names, descriptions, argument schemas, and results with the
   official-SDK catalog.
3. Run the existing unit, runtime, and live backend suites unchanged.
4. Connect both Codex and Claude and execute representative calls from all six
   domains.

No catalog or scope change is permitted as an incidental migration effect.

### Phase 3: Security and Operations Parity

Prove all of the following before considering a cutover:

- Auth0 issuer, audience, expiry, and scope enforcement;
- local auth bypass remains restricted to `APP_ENV=local`;
- tool and resource scope parity;
- sanitized audit events for success and denial paths;
- recursive redaction and Sentry PII protections;
- SQL, Redis, Kafka, and S3 allowlists;
- rate limiting, request-size limits, and host/origin protection;
- readiness, Prometheus, and OpenTelemetry behavior;
- least-privilege PostgreSQL and MinIO credentials;
- clean Snyk and dependency audit results.

### Phase 4: Controlled Cutover

1. Approve the migration explicitly after reviewing parity evidence.
2. Replace the production server factory without changing the public `/mcp`
   URL or client configuration.
3. Remove obsolete compatibility code and the old SDK dependency only after no
   direct imports remain.
4. Regenerate the catalog and update all required documentation.
5. Re-run the full live suite with seeded Rule Management and Transaction
   Management data.

Rollback is the previously locked official-SDK implementation and container
image. Do not combine the cutover with tool additions, scope changes, or Auth0
tenant changes.

## Required Approval and Validation

FastMCP adoption changes the gateway's security and transport foundation. It
requires explicit implementation approval and a focused security review.

At minimum, a future migration handoff must show:

```powershell
uv run ruff check app/ tests/ cli/ scripts/
uv run ruff format --check app/ tests/ cli/ scripts/
uv run pytest tests/ -q
$env:APP_ENV='local'
$env:SECURITY_SKIP_JWT_VALIDATION='true'
uv run gateway-check
uv run gateway-smoke
uv run gateway-snyk
```

It must also include live Codex and Claude MCP calls and a successful platform
Prometheus scrape after deployment.
