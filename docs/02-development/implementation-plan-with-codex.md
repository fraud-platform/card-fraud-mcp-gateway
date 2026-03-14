# Implementation Plan With Codex

## Objective

Implement the gateway in controlled slices so Codex can make progress safely without mixing infrastructure, security, and business logic in a single large change.

## Working Method

Each flow below is intended to be implemented as an isolated Codex task with:

- explicit file ownership
- clear non-goals
- exact verification commands
- no destructive backend operations unless that flow explicitly introduces them

## Recommended Implementation Order

| Flow | Name | Status | Notes |
|------|------|--------|-------|
| 01 | Foundation and repo bootstrap | ✅ Implemented | — |
| 02 | Authentication and authorization | ⚠️ Partial | Denied request auditing done; approval enforcement still scaffolded only |
| 03 | Postgres readonly domain | ⚠️ Partial | Config-driven allowlists enforced; platform metadata ingestion still missing |
| 04 | Redis readonly domain | ⚠️ Partial | Config-driven prefix allowlists enforced; ownership metadata ingestion still missing |
| 05 | Kafka and Redpanda readonly domain | ⚠️ Partial | Config-driven topic/group allowlists enforced; ownership metadata ingestion still missing |
| 06 | MinIO readonly domain | ⚠️ Partial | Config-driven bucket/prefix allowlists enforced; ownership metadata ingestion still missing |
| 07 | Platform control-plane integration | ⚠️ Partial | Only services.yaml ingested; no other ownership files; no drift detection |
| 08 | Resources, prompts, and catalog export | ⚠️ Partial | Investigation context resource and full catalog export done; no registry abstraction |
| 09 | Ops analyst agent integration | ❌ Not started | Gateway-side ops tools exist but agent-side integration not started; needs repo split |
| 10 | Observability, audit, and guardrails | ⚠️ Partial | Request limits done; response controls, metrics, and approval framework still missing |

## Flow Dependency Graph

```text
  Flow 01 ─── Foundation
    │
    v
  Flow 02 ─── Auth & Authorization
    │
    ├──────┬──────┬──────┐
    v      v      v      v
  Flow 03 Flow 04 Flow 05 Flow 06   (backend domains, parallelizable)
    │      │      │      │
    └──────┴──────┴──────┘
           │
           v
  Flow 07 ─── Platform Control-Plane
    │
    v
  Flow 08 ─── Resources, Prompts, Catalog
    │
    ├───────────────┐
    v               v
  Flow 09           Flow 10          (parallelizable)
  Ops Agent         Observability
  Integration       & Guardrails
```

Flows 03-06 can execute in parallel once Flow 02 is complete.
Flows 09 and 10 can execute in parallel once Flow 08 is complete.
Flow 09 crosses repo boundaries and requires coordination with `card-fraud-ops-analyst-agent`.

## Codex Task Template

Use this structure for every implementation turn:

1. State the exact flow being implemented.
2. Name the files that may change.
3. Define what must remain untouched.
4. Implement the smallest vertical slice that can be tested.
5. Run the documented local checks.
6. Report constraints or follow-up gaps explicitly.

## Suggested Codex Execution Batches

### Batch A

- create runtime skeleton
- config loader
- health endpoint
- HTTP server bootstrap
- docs sync

### Batch B

- Auth0 JWT verification
- scope enforcement
- tool registry policies
- audit envelope

### Batch C

- Postgres readonly tools
- schema resources
- query policy tests

### Batch D

- Redis, Kafka, MinIO readonly tools
- pagination and truncation policies
- integration smoke tests

### Batch E

- platform service registry ingestion
- generated resources and prompts
- ops-agent MCP backend contract

### Batch F

- OTel
- approval policy framework
- release hardening
- client onboarding docs

## Known Implementation Gaps

These gaps exist between the flow specifications and current implementation.
They must be resolved before declaring Phase 1 complete.

### Security — Allowlists (Flows 03-06)

Every backend flow specifies ownership-driven allowlists as the primary access control.
Config-driven allowlists are now enforced across Postgres, Redis, Kafka, and S3, including the corresponding resource surfaces.

**Remaining gap:** Load these allowlists from platform ownership YAML files instead of hand-maintained runtime config.

### Security — Denied Request Auditing (Flow 02)

Denied request auditing is implemented in both `require_scope` and `ensure_scope`.
Tool denials and resource denials now emit audit warnings before they raise.

### Security — Request/Response Size Limits (Flow 10, Security Runbook)

Gateway-level middleware now enforces request body size, including streamed bodies without `Content-Length`.
Per-tool limits still handle row, payload, and object truncation.

**Remaining gap:** Add gateway-wide response size capping or summarization middleware.

### Platform Metadata — Partial Ingestion (Flow 07)

Only `services.yaml` is consumed. The plan requires ingestion of `database.yaml`, `messaging.yaml`, `storage.yaml`, and `secrets.yaml` for ownership-driven access control and drift detection.

**Resolution:** Implement ownership file parsers or defer to config-driven allowlists with explicit documentation.

### Prompt Safety — SQL Parameters (Flow 08)

`postgres.query_readonly` now accepts an optional `parameters` list, and `explain-decision-trace` demonstrates `$1` bind syntax with that API.

### Catalog Export — Live Contract (Flow 08)

The `/catalog` endpoint exports tools, resources, and prompts from the live registration set.
The `gateway-export-catalog` CLI command is implemented.

### Approval Framework — Scaffolded Only (Flow 02, Flow 10)

`ToolPolicy.approval_required` exists but nothing reads or enforces it.
`ops.run_investigation` is `read_only=False` but passes through without any approval gate.

**Resolution:** Implement enforcement or document it as Phase 2 scope.

## Definition Of Done For Initial Launch

- remote HTTP MCP server starts locally and responds to Streamable HTTP
- Codex can register it by URL and call at least one tool per domain
- Claude Code can register it by URL and call at least one tool per domain
- all Phase 1 tools are read-only (except `ops.run_investigation` which requires `fraud.ops.investigation.run` scope)
- every tool call is audited with principal, tool name, domain, scope, timing, and outcome
- denied requests are audited
- ownership-driven allowlists or config-driven allowlists are enforced for all backend domains
- secret patterns are redacted before response emission
- rate limiting is enforced per client
- unit tests cover: config, auth, policy, SQL safety, redaction, audit, tool registration
- integration tests cover: at least one allow/deny path per domain against local infra
- smoke tests cover: server boot, auth success/deny, one tool call per domain through HTTP
- `uv run ruff check` passes with 0 errors
- `uv run pytest` passes with 0 failures
- docs (`README.md`, `codemap.md`, `local-setup.md`) match actual commands and file layout
