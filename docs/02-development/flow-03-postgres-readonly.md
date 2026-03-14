# Flow 03 - Postgres Readonly

> **Status:** ⚠️ Partially Implemented  
> **Verified:** 2026-03-09

## Goal

Expose safe Postgres tools and resources for analysts and coding agents without allowing arbitrary or high-cost database access.

## Deliverables

- readonly connection pool
- schema and table discovery tools
- bounded readonly query tool
- schema resources
- query audit metadata

## Tool Set

- `postgres.list_schemas`
- `postgres.list_tables`
- `postgres.describe_table`
- `postgres.query_readonly`

## Resource Set

- schema catalog
- ownership-aware table catalog
- common analyst views

## Implementation Tasks

1. Load platform DB ownership metadata from `card-fraud-platform`.
2. Build allowlist for exposed schemas and tables.
3. Enforce readonly transactions and statement timeout.
4. Enforce row limit and result truncation.
5. Reject DDL, DML, transaction control, and multi-statement input.
6. Add schema resources generated from information schema plus ownership metadata.

## Guardrails

- no unrestricted SQL shell
- no superuser credential
- no schema reset hooks
- no hidden bypass for internal callers

## Verification

- describe table works for approved tables
- query limit is enforced
- multi-statement query is rejected
- forbidden table or schema is rejected

## Known Gaps

- ✔ Readonly connection pool via asyncpg
- ✔ Schema and table discovery tools (`list_schemas`, `list_tables`, `describe_table`)
- ✔ Bounded readonly query tool with CTE wrapping, row limit, and statement timeout
- ✔ Config-driven schema/table allowlists enforced for discovery, resources, and raw query planning
- ✔ `EXPLAIN` queries supported without the row-bounding wrapper
- ✔ SQL safety validation (rejects DDL, DML, transaction control, multi-statement)
- ✔ Result redaction via `redact()`
- ❌ **No ownership metadata ingestion** — `database.yaml` from platform control-plane is not consumed
- ❌ **Ownership metadata source of truth** — allowlists are config-driven today; they are not yet generated from platform ownership metadata

## Codex Prompt Seed

Implement Flow 03 for `card-fraud-mcp-gateway`. Add Postgres readonly connectors and safe schema/query tools using the platform ownership metadata as the allowlist source. Reject unsafe SQL and enforce row and time limits. Add tests and report the exact verification commands.
