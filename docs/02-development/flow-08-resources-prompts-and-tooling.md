# Flow 08 - Resources, Prompts, And Tooling

> **Status:** ⚠️ Partially Implemented  
> **Verified:** 2026-03-09

## Goal

Add high-value MCP resources and prompts so analysts and coding agents can use the gateway as a guided investigation surface, not only as a raw tool list.

## Deliverables

- generated MCP resources
- analyst-safe MCP prompts
- catalog export for docs and review
- prompt templates tied to approved tools only

## Resource Families

- service registry
- database ownership map
- topic catalog
- artifact catalog
- health and topology summary
- analyst-friendly investigation context bundles

## Prompt Families

- `investigate-transaction`
- `explain-decision-trace`
- `triage-platform-health`
- `inspect-ruleset-artifact`
- `review-consumer-lag`

## Implementation Tasks

1. Create a resource registry abstraction.
2. Generate resources from platform metadata and live bounded lookups.
3. Create prompt templates that reference only approved tools and resources.
4. Add catalog export to markdown or JSON for review.
5. Keep prompt outputs human-controlled and audit-friendly.

## Guardrails

- prompts must not imply autonomous adjudication
- resources must not expose secrets
- prompt templates must not reference disabled tools

## Verification

- resources list is complete and stable
- prompts resolve correctly
- catalog export reflects the live registration set

## Known Gaps

- ✔ 7 MCP resources registered (`fraud://` URIs for services, ownership, schemas, topics, buckets, health, ops investigation context)
- ✔ 5 prompt templates (investigate-transaction, explain-decision-trace, triage-platform-health, inspect-ruleset-artifact, review-consumer-lag)
- ✔ `/catalog` endpoint exports tools, resources, and prompts from the live registration set
- ✔ Investigation context bundle resource implemented
- ✔ Prompt and resource surfaces now honor the same scope/allowlist boundaries as the tool layer
- ❌ **No resource registry abstraction** — resources are directly registered inline; plan calls for a registry pattern
- ✔ `gateway-export-catalog` CLI command implemented in `pyproject.toml`
- ✔ `explain-decision-trace` now matches the tool contract by using bind parameters explicitly
- ❌ **Database ownership map resource** — plan lists it as a resource family; `fraud://schemas/catalog` is filtered by config-driven allowlists, not generated ownership metadata

## Codex Prompt Seed

Implement Flow 08 for `card-fraud-mcp-gateway`. Add generated MCP resources, a prompt catalog for analyst workflows, and a catalog export command. Keep prompts advisory and aligned with approved read-only tools. Run the documented checks and report exact commands.
