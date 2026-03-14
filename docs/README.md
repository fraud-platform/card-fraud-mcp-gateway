# Card Fraud MCP Gateway Documentation

## Documentation Standards

- Keep published docs inside `docs/01-setup` through `docs/07-reference`.
- Use lowercase kebab-case file names for topic docs.
- Exceptions: `README.md`, `codemap.md`, and generated contract artifacts.
- Keep architecture, security, and rollout documents current with platform ownership contracts.
- Keep published tool/resource/prompt counts aligned with the live `/catalog` export.
- Keep observability docs aligned with the live `/metrics` output and enabled signals.

## Section Index

### `01-setup` - Setup

- `01-setup/local-setup.md` — Clone, Doppler config, start gateway, verify
- `01-setup/auth0-setup-guide.md` — Provision Auth0 resources (automated bootstrap + RBAC step)
- `01-setup/connecting-clients.md` — Connect Claude Code, Copilot, Codex, curl (local + prod)

### `02-development` - Development

- `02-development/architecture.md`
- `02-development/implementation-plan-with-codex.md`
- `02-development/flow-01-foundation-and-bootstrap.md`
- `02-development/flow-02-auth-and-authorization.md`
- `02-development/flow-03-postgres-readonly.md`
- `02-development/flow-04-redis-and-cache.md`
- `02-development/flow-05-kafka-and-redpanda.md`
- `02-development/flow-06-minio-and-artifacts.md`
- `02-development/flow-07-platform-control-plane-integration.md`
- `02-development/flow-08-resources-prompts-and-tooling.md`
- `02-development/flow-09-ops-analyst-agent-integration.md`
- `02-development/flow-10-observability-audit-and-guardrails.md`

### `03-api` - API

- `03-api/mcp-contract-outline.md`

### `04-testing` - Testing

- `04-testing/test-strategy.md`

### `05-deployment` - Deployment

- `05-deployment/deployment-topology.md`

### `06-operations` - Operations

- `06-operations/security-runbook.md`

### `07-reference` - Reference

- `07-reference/tool-catalog-and-scope-matrix.md`

## Core Index Files

- `docs/README.md`
- `docs/codemap.md`
