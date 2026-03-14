# Flow 05 - Kafka And Redpanda

> **Status:** ⚠️ Partially Implemented  
> **Verified:** 2026-03-09

## Goal

Expose safe topic inspection, message preview, and consumer lag tools for Redpanda without enabling administrative or destructive operations.

## Deliverables

- readonly Kafka admin and consumer clients
- topic list and describe tools
- bounded message peek tool
- consumer lag inspection
- topic resources generated from platform ownership metadata

## Tool Set

- `kafka.list_topics`
- `kafka.describe_topic`
- `kafka.peek_messages`
- `kafka.consumer_lag`

## Implementation Tasks

1. Load approved topics and consumer groups from `messaging.yaml`.
2. Restrict the exposed topic catalog to owned and approved topics.
3. Implement message peek with max message count, max payload size, and decode-safe output.
4. Add consumer lag inspection for approved groups only.
5. Redact payload fields that match configured sensitive patterns.
6. Audit topic access, offsets, partitions, and message counts.

## Guardrails

- no topic create/delete
- no offset commit from browse tools
- no unrestricted admin command bridge
- no DLQ replay in Phase 1

## Verification

- approved topic list matches ownership file
- message peek is bounded and redacted
- consumer lag query works for approved group
- unknown topic is rejected

## Known Gaps

- ✔ Readonly Kafka admin client via aiokafka
- ✔ Topic list and describe tools
- ✔ Bounded message peek with ephemeral consumer, max messages, and payload size limit
- ✔ Consumer lag inspection
- ✔ Config-driven topic/group allowlists enforced across tools and topic resources
- ✔ Async Kafka metadata lookup aligned with installed `aiokafka` API
- ✔ Kafka consumer lifecycle management (connect/disconnect in lifespan)
- ✔ Result redaction via `redact()`
- ❌ **Payload field redaction** — plan says "redact payload fields that match configured sensitive patterns." Current redaction applies to the full JSON output string, not to individual payload fields before serialization.
- ❌ **Ownership metadata source of truth** — approved topics/groups are config-driven today; they are not generated from `messaging.yaml`

## Codex Prompt Seed

Implement Flow 05 for `card-fraud-mcp-gateway`. Add Redpanda-compatible readonly Kafka tools using the platform messaging ownership file as the source of truth. Keep all actions inspect-only. Add tests and report the exact verification commands.
