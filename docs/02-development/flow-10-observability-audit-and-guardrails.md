# Flow 10 - Observability, Audit, And Guardrails

> **Status:** ⚠️ Partially Implemented  
> **Verified:** 2026-03-09

## Goal

Close the launch gap by adding audit completeness, telemetry, rate limits, policy enforcement, and operational controls.

## Deliverables

- structured audit log schema
- OpenTelemetry tracing and metrics
- rate limiting
- request and result size controls
- prompt-injection and secret redaction defenses
- break-glass framework for future write tools

## Implementation Tasks

1. Define audit event schema for every MCP interaction.
2. Emit spans for request receipt, auth, policy, backend call, and response shaping.
3. Add metrics for:
   - call counts
   - latency
   - denied requests
   - backend failures
   - truncation events
4. Add rate limits by client and domain.
5. Add result shaping and content-redaction middleware.
6. Define approval path for future destructive tools without enabling them yet.

## Guardrails

- secret patterns must be redacted before response emission
- untrusted payloads from Kafka, Redis, and object storage must be treated as prompt-injection sources
- oversized results must be summarized rather than streamed raw

## Verification

- traces appear in local OTel target
- audit records contain principal, tool, scope, and outcome
- rate limits trigger correctly
- redaction catches configured secret patterns

## Known Gaps

- ✔ Structured audit logging via structlog with JSON output
- ✔ `@audit_tool` decorator logs tool name, caller, timing, args, and outcome
- ✔ OpenTelemetry tracing with optional OTLP export
- ✔ Rate limiting per client (sliding window, configurable RPM)
- ✔ Request body size middleware with streaming enforcement
- ✔ Secret redaction with 8 regex patterns (passwords, tokens, cards, SSNs, AWS keys, connection strings)
- ❌ **Response size controls missing** — no gateway-level response size capping or summarization middleware
- ✔ **Metrics implemented** — `/metrics` now exposes request, auth/rate-limit, tool latency/count, backend-init failures, resource failures, and truncation events
- ❌ **Approval path framework** — plan says "define approval path for future destructive tools." `ToolPolicy.approval_required` exists but is not enforced at runtime
- ❌ **Prompt-injection defenses** — plan notes "untrusted payloads from Kafka, Redis, and object storage must be treated as prompt-injection sources." No explicit prompt-injection sanitization exists beyond redaction.
- ❌ **Oversized result summarization** — plan says "oversized results must be summarized rather than streamed raw." Per-tool truncation exists but no gateway-level summarization.

## Codex Prompt Seed

Implement Flow 10 for `card-fraud-mcp-gateway`. Add structured audit logging, OpenTelemetry spans and metrics, request controls, response redaction, and the policy scaffolding for future approval-gated write tools. Keep all exposed tools read-only. Add tests and report exact validation commands.
