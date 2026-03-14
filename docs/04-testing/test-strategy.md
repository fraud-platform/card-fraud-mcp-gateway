# Test Strategy

## Objectives

- verify auth and policy behavior
- verify safe bounded access to each backend
- verify generated catalogs match platform metadata
- verify client compatibility for Codex and Claude Code

## Test Layers

### Unit

- config parsing
- scope policy checks
- SQL safety parser
- result shaping and redaction
- metadata normalization

### Integration

- Postgres readonly connectors
- Redis prefix-safe reads
- Kafka topic and message preview
- MinIO listing and bounded reads
- platform health and inventory generation

### Contract

- tool catalog snapshot
- resource catalog snapshot
- prompt catalog snapshot
- failure shape consistency

### Smoke

- server boot
- auth success and deny paths
- at least one tool call per domain
- client registration checks

## Phase 1 Required Checks

- lint ✔ (`uv run ruff check app/ tests/` — 0 errors)
- format ✔ (`uv run ruff format --check app/ tests/`)
- unit tests ✔ (`uv run pytest tests/ -v` — 302 tests passing)
- integration tests against local platform stack — partial (backend integration tests exist but require live infra)
- smoke tests through the actual HTTP MCP endpoint ✔ (`uv run gateway-smoke`)

## Quality Gates

- all exposed tools tested for allow and deny paths — partial (policy/auth coverage improved; per-domain deny paths are still not exhaustive)
- audit events asserted in tests ✔ (test_audit.py)
- truncation and pagination behavior covered — partial (SQL row limit tested; Redis/Kafka/S3 truncation not tested)
