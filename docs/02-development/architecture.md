# Architecture

## Target Shape

The gateway is one remote MCP server with multiple bounded domain adapters behind it.

```text
Codex / Claude Code / Ops Analyst Runtime
                |
                v
      card-fraud-mcp-gateway
                |
  -----------------------------------------
  |        |        |        |            |
  v        v        v        v            v
Platform Postgres Redis  Kafka/Redpanda  MinIO
 APIs     RO       RO        RO          RO
```

## Why One Gateway

- one client configuration for approved users and agents
- one consistent auth model
- one audit trail
- one approval policy engine
- one place to apply output redaction and prompt-injection defenses

## Why Not One Unrestricted Backend Connector

- blast radius becomes too large
- least privilege becomes impossible
- audit semantics become weak
- analyst-safe workflows become mixed with admin operations
- prompt injection risks increase across untrusted data sources

## Core Runtime Components

### MCP Server Layer

- remote Streamable HTTP transport
- tool registration
- resource registration
- prompt registration
- client metadata and rate limiting

### Policy Layer

- JWT validation with Auth0
- scope-to-tool mapping
- approval requirements for high-risk tools
- domain-level read/write enforcement
- result redaction rules

### Adapter Layer

- Postgres readonly adapter
- Redis readonly adapter
- Kafka readonly adapter
- MinIO readonly adapter
- platform control-plane adapter
- business-domain adapter for ops workflows

### Metadata Layer

- generated tool catalogs from platform ownership files
- generated resource catalogs from service registry and ownership metadata
- prompt catalog for analyst and platform runbooks

### Audit Layer

- invocation logs
- caller identity
- target domain
- approval linkage
- latency, row count, object count, message count
- OpenTelemetry spans

## Security Boundary

Every backend must have its own restricted credential set.

- Postgres analyst role: readonly, allowlisted schemas, bounded statements
- Redis role or network policy: no flush, no unsafe commands
- Kafka role: topic browse and consume-preview only
- MinIO role: read only to approved prefixes
- platform APIs: service accounts with narrow scopes

## Delivery Phases

### Phase 1

- read-only tools
- resources
- prompts
- audit
- OTel
- Codex and Claude Code registration

### Phase 2

- analyst-assisted business tools
- ops-agent MCP backend
- generated catalogs from adapters and ownership metadata

### Phase 3

- tightly controlled write actions
- approval workflows
- break-glass paths
- compliance exports

## Implementation Status

### Phase 1 — Current State

All 22 tools, 7 resources, and 5 prompts are implemented.
Auth (JWT/Auth0), audit (structlog + OTel), rate limiting, config-driven allowlists,
request-size limits, secret redaction, and CORS are in place.
297 tests passing, 0 lint errors.

Key gaps against plan:

- **Approval workflow framework** is scaffolded (`ToolPolicy.approval_required`) but not enforced at runtime
- **Platform metadata ingestion** is limited to optional `services.yaml` — database, messaging, storage, and secrets ownership files are not consumed
- **Response size controls** are still per-tool rather than gateway-wide
- **Prompt-injection hardening** relies on truncation/redaction only; no explicit content-sanitization layer exists

### Phase 2 — Not Started

No flow documents exist yet. Needs planning for:

- analyst-assisted business tools
- ops-agent MCP backend contract
- generated catalogs from adapters and ownership metadata

### Phase 3 — Not Started

No flow documents exist yet. Needs planning for:

- tightly controlled write actions with approval gates
- approval workflows with audit linkage
- break-glass paths and rollback documentation
- compliance exports
