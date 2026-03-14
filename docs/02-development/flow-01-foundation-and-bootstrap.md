# Flow 01 - Foundation And Bootstrap

> **Status:** ✅ Implemented  
> **Verified:** 2026-03-08

## Goal

Create the runtime skeleton and local development shape for the gateway without touching business logic or backend connectors.

## Deliverables

- `uv` project with dev dependencies
- app package layout
- config loading
- health endpoint
- server bootstrap
- local run command
- docs index and codemap

## Implementation Tasks

1. Create package structure for config, server bootstrap, domains, security, and audit.
2. Add environment model for host, port, environment, logging, and feature flags.
3. Add process-level startup validation for required config groups.
4. Add health and readiness endpoints.
5. Add a minimal MCP server bootstrap with no business tools yet.
6. Add local commands for dev, lint, format, and smoke.

## Guardrails

- no live database connectors yet
- no auth bypass
- no write actions
- no generated catalogs yet

## Verification

- app boots locally
- health endpoint returns success
- readiness reflects dependency-free startup
- docs reference the actual commands

## Known Gaps

None — this flow is fully implemented.

- `uv` project with `pyproject.toml` and dev extras ✔
- App package layout with `config.py`, `server.py`, `main.py` ✔
- Pydantic settings from `GATEWAY_*` environment variables ✔
- `/health` and `/ready` endpoints ✔
- MCP server bootstrap via `create_mcp_server()` ✔
- `gateway-dev` and `gateway-check` CLI entry points ✔
- `gateway-smoke` and `gateway-export-catalog` CLI commands are referenced in `local-setup.md` but not yet implemented

## Codex Prompt Seed

Implement Flow 01 for `card-fraud-mcp-gateway`. Create the Python runtime skeleton, config model, health endpoint, and MCP bootstrap shell only. Do not add backend connectors yet. Update docs if commands or file layout change. Run the local smoke check and report exact commands used.
