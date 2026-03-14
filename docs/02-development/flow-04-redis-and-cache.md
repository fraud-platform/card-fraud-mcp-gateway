# Flow 04 - Redis And Cache

> **Status:** ⚠️ Partially Implemented  
> **Verified:** 2026-03-09

## Goal

Expose safe Redis inspection tools for counters, cached values, and troubleshooting without allowing mutation or destructive cache operations.

## Deliverables

- readonly Redis client
- prefix-safe inspection tools
- TTL and metadata views
- optional key-shape redaction

## Tool Set

- `redis.scan_prefix`
- `redis.get_key`
- `redis.ttl`
- `redis.type`

## Implementation Tasks

1. Define approved key prefixes from platform and service ownership docs.
2. Implement paginated prefix scan.
3. Restrict key fetches to approved prefixes.
4. Normalize return shapes for strings, hashes, sets, and sorted sets.
5. Add output truncation for large values.
6. Audit requested prefixes and returned key counts.

## Guardrails

- no `FLUSHDB`
- no `DEL`
- no unrestricted `KEYS *`
- no raw command passthrough

## Verification

- approved prefix scan returns bounded results
- disallowed prefix is rejected
- large values are truncated safely
- TTL works for existing and missing keys

## Known Gaps

- ✔ Readonly Redis client via redis.asyncio
- ✔ Prefix scan via `redis.scan_prefix` with configurable max keys
- ✔ Key inspection tools (`get_key`, `ttl`, `type`)
- ✔ Config-driven key prefix allowlists enforced for scans and direct key reads
- ✔ Multi-type value support (strings, hashes, sets, sorted sets, lists)
- ✔ Value truncation at configurable byte limit
- ✔ Result redaction via `redact()`
- ❌ **Ownership metadata source of truth** — approved prefixes are config-driven today; they are not generated from platform ownership docs

## Codex Prompt Seed

Implement Flow 04 for `card-fraud-mcp-gateway`. Add readonly Redis inspection tools with prefix allowlists, pagination, and value truncation. Do not add any mutation command. Add tests and report the exact commands used.
