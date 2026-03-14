# Tool Catalog And Scope Matrix

## Phase 1 Tool Catalog

| Domain | Tool | Mode | Scope |
|---|---|---|---|
| platform | `platform.inventory` | read-only | `fraud.platform.read` |
| platform | `platform.service_status` | read-only | `fraud.platform.read` |
| platform | `platform.service_health` | read-only | `fraud.platform.read` |
| platform | `platform.ownership_summary` | read-only | `fraud.platform.read` |
| postgres | `postgres.list_schemas` | read-only | `fraud.db.read` |
| postgres | `postgres.list_tables` | read-only | `fraud.db.read` |
| postgres | `postgres.describe_table` | read-only | `fraud.db.read` |
| postgres | `postgres.query_readonly` | read-only | `fraud.db.read` |
| redis | `redis.scan_prefix` | read-only | `fraud.redis.read` |
| redis | `redis.get_key` | read-only | `fraud.redis.read` |
| redis | `redis.ttl` | read-only | `fraud.redis.read` |
| redis | `redis.type` | read-only | `fraud.redis.read` |
| kafka | `kafka.list_topics` | read-only | `fraud.kafka.read` |
| kafka | `kafka.describe_topic` | read-only | `fraud.kafka.read` |
| kafka | `kafka.peek_messages` | read-only | `fraud.kafka.read` |
| kafka | `kafka.consumer_lag` | read-only | `fraud.kafka.read` |
| storage | `s3.list_buckets` | read-only | `fraud.storage.read` |
| storage | `s3.list_objects` | read-only | `fraud.storage.read` |
| storage | `s3.head_object` | read-only | `fraud.storage.read` |
| storage | `s3.get_object` | read-only | `fraud.storage.read` |
| ops | `ops.get_investigation_context` | read-only | `fraud.ops.investigation.read` |
| ops | `ops.run_investigation` | controlled | `fraud.ops.investigation.run` |

## Phase 1 Resource Catalog

- `fraud://platform/services`
- `fraud://platform/ownership`
- `fraud://schemas/catalog`
- `fraud://topics/catalog`
- `fraud://buckets/catalog`
- `fraud://health/topology`
- `fraud://ops/investigation-context`

## Phase 1 Prompt Catalog

- `investigate-transaction`
- `explain-decision-trace`
- `triage-platform-health`
- `inspect-ruleset-artifact`
- `review-consumer-lag`

## Phase 1 Implementation Notes

- All 22 tools listed above are implemented and registered.
- Tool scopes are enforced via `@require_scope`; resource scopes are enforced inline at read time.
- Config-driven allowlists are enforced across Postgres, Redis, Kafka, and S3 read surfaces.
- `postgres.query_readonly` accepts an optional `parameters` list for positional bind values.
- `/catalog` exports tools, resources, and prompts from the live registration set.
- `ops.run_investigation` is the only non-read-only tool. It requires `fraud.ops.investigation.run` scope but has no approval gate.

## Deferred Tools

These should not be implemented in Phase 1:

- event replay
- bucket writes
- ruleset publish
- schema reset
- cache delete
- Kafka admin mutation
