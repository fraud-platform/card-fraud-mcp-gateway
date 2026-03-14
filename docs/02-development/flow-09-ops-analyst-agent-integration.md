# Flow 09 - Ops Analyst Agent Integration

> **Status:** ❌ Not Started (gateway-side ops tools exist; agent-side integration not started)  
> **Verified:** 2026-03-08

## Repo Boundary Notice

This flow crosses repository boundaries. The Codex prompt seed references both `card-fraud-mcp-gateway` and `card-fraud-ops-analyst-agent`. Follow sibling-repo ownership contracts from `card-fraud-platform`.

**Gateway-side scope** (this repo): Expose stable MCP tools and resources that the ops agent can consume. The two ops tools (`ops.get_investigation_context`, `ops.run_investigation`) are already implemented.

**Agent-side scope** (`card-fraud-ops-analyst-agent`): Add backend abstraction, MCP-backed adapter, feature flag, and comparison harness. This work belongs in the agent repo.

## Goal

Let `card-fraud-ops-analyst-agent` use the gateway as a tool plane without forcing a risky or premature replacement of existing direct API clients.

## Target Runtime Modes

- `native`
  - existing direct clients remain active
- `mcp`
  - tool and context access comes only from the MCP gateway
- `hybrid`
  - deterministic service calls remain direct
  - discovery and investigation tooling uses MCP

## Recommended Initial Default

Use `hybrid` for development and evaluation. Keep `native` as the production default until parity, latency, and audit quality are proven.

## Deliverables

- tool-backend abstraction in ops analyst agent
- gateway client for MCP tool/resource access
- feature flag for backend mode
- compatibility mapping between native tools and MCP tools
- latency and quality comparison harness

## Implementation Tasks

1. Inventory existing ops-agent tools and service clients.
2. Split them into:
   - deterministic direct-service operations
   - discovery or investigation operations suitable for MCP
3. Add backend interface for each portable operation.
4. Implement MCP-backed adapter.
5. Add feature flag and per-environment defaults.
6. Run side-by-side comparison for output quality, latency, and auditability.

## Candidate MCP-backed Ops Flows

- transaction context retrieval
- case timeline enrichment
- ruleset artifact inspection
- Kafka decision event trace preview
- Redis velocity counter inspection
- platform health checks used during investigation

## Keep Direct For Now

- transaction write paths
- recommendation acknowledgement
- business actions requiring service-local invariants
- any latency-critical synchronous decision path

## Verification

- native and MCP modes produce compatible investigation context
- hybrid mode degrades gracefully when MCP is unavailable
- audit logs correlate ops-agent runs with MCP tool calls

## Known Gaps

**Gateway-side (this repo):**
- ✔ `ops.get_investigation_context` tool implemented (Postgres + Redis cross-domain lookup)
- ✔ `ops.run_investigation` tool implemented (transaction_review, case_triage, velocity_check)
- ❌ **No MCP compatibility mapping** — plan calls for mapping between native ops-agent tools and MCP equivalents
- ❌ **No latency/quality comparison harness** — plan calls for side-by-side comparison infrastructure

**Agent-side (card-fraud-ops-analyst-agent):**
- ❌ Tool-backend abstraction not implemented
- ❌ MCP-backed adapter not implemented
- ❌ Feature flag for backend mode not implemented
- ❌ Hybrid/native/mcp mode selection not implemented

## Codex Prompt Seed

Implement Flow 09 across `card-fraud-mcp-gateway` and `card-fraud-ops-analyst-agent`. Add a backend abstraction to the ops agent so selected investigation capabilities can use the MCP gateway in `hybrid` mode while direct API clients remain available. Do not replace production-critical write paths. Add comparison tests and report exact commands run.
