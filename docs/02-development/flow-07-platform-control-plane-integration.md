# Flow 07 - Platform Control-Plane Integration

> **Status:** ⚠️ Partially Implemented  
> **Verified:** 2026-03-08

## Goal

Turn platform-owned registry and ownership metadata into first-class MCP tools and resources so the gateway reflects the real suite layout automatically.

## Deliverables

- service registry loader
- ownership metadata loader
- platform inventory tools
- service health and readiness tools
- generated resources for infra and service catalogs

## Tool Set

- `platform.inventory`
- `platform.service_status`
- `platform.service_health`
- `platform.ownership_summary`

## Implementation Tasks

1. Parse `services.yaml` from `card-fraud-platform`.
2. Parse domain ownership files for database, storage, messaging, auth, and secrets.
3. Generate internal normalized metadata model.
4. Build service inventory and health tools from the normalized model.
5. Add resources for service catalog and ownership summary.
6. Detect and surface metadata drift between platform files and gateway registrations.

## Guardrails

- gateway must not become a second registry source
- invalid metadata should fail startup clearly
- health checks must be bounded and cached where reasonable

## Verification

- service inventory matches `services.yaml`
- ownership summary matches source files
- missing or invalid metadata fails validation cleanly

## Known Gaps

- ✔ Service registry loader from optional `services.yaml` with static fallback
- ✔ Platform inventory, service_status, service_health, and ownership_summary tools
- ✔ Resources for service catalog and ownership summary
- ❌ **Only `services.yaml` is parsed** — the plan requires parsing `database.yaml`, `messaging.yaml`, `storage.yaml`, `auth.yaml`, and `secrets.yaml` for a normalized metadata model
- ❌ **No internal normalized metadata model** — platform.py uses a flat dict, not a structured model that other domains can consume for allowlists
- ❌ **No metadata drift detection** — plan says "detect and surface metadata drift between platform files and gateway registrations"
- ❌ **Invalid metadata validation** — plan says "invalid metadata should fail startup clearly" but malformed YAML silently falls back to static inventory

## Codex Prompt Seed

Implement Flow 07 for `card-fraud-mcp-gateway`. Add platform registry ingestion and generated inventory/health tools using the platform control-plane YAML files as the source of truth. Fail clearly on metadata drift or malformed input. Add validation tests and report the exact commands used.
