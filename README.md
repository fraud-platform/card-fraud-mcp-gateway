# Card Fraud MCP Gateway

Enterprise MCP gateway for the Card Fraud suite.

This service exposes a single Streamable HTTP MCP endpoint and provides a safe, scope-gated interface across platform backends: PostgreSQL, Redis, Redpanda/Kafka, MinIO/S3, platform inventory, and ops investigation workflows.

## Status

| Area | Status |
|------|--------|
| MCP tools | 22 implemented |
| MCP resources | 7 implemented |
| MCP prompts | 5 implemented |
| Auth | Auth0 JWT + scope enforcement |
| Observability | OpenTelemetry + structured audit logs + Prometheus + Sentry hooks |
| Security controls | SQL safety, allowlists, redaction, bounded payloads |
| Test suite | 302 passing tests |

## Prerequisites

- [Python](https://www.python.org/) 3.14+
- [uv](https://docs.astral.sh/uv/)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (for live backend integration)
- [Doppler CLI](https://docs.doppler.com/docs/cli) (recommended for local secrets)
- Sibling repo: `card-fraud-platform` (shared infra stack)

## Repository Layout

```text
card-fraud-mcp-gateway/
├── app/                          # Gateway runtime
│   ├── domains/                  # Domain tools (postgres/redis/kafka/storage/platform/ops)
│   ├── security/                 # Auth, policy, allowlist, redaction
│   ├── main.py                   # FastAPI app + health/readiness/catalog
│   ├── server.py                 # FastMCP registration
│   └── backends.py               # Backend lifecycle management
├── cli/                          # Helper CLIs (catalog check, auth0 bootstrap/verify)
├── tests/                        # Unit + runtime/e2e tests
├── docs/                         # Setup, architecture, testing, reference docs
├── docker-compose.yml            # Standalone gateway container wired to platform infra
├── Dockerfile                    # Production/runtime image
└── README.md
```

## What This Gateway Exposes

### Tool Domains

| Domain | Tools | Scope |
|--------|-------|-------|
| PostgreSQL | 4 | `fraud.db.read` |
| Redis | 4 | `fraud.redis.read` |
| Kafka | 4 | `fraud.kafka.read` |
| Storage (S3/MinIO) | 4 | `fraud.storage.read` |
| Platform | 4 | `fraud.platform.read` |
| Ops | 2 | `fraud.ops.investigation.read`, `fraud.ops.investigation.run` |

### Endpoints

| Path | Purpose |
|------|---------|
| `GET /health` | Liveness |
| `GET /ready` | Backend readiness summary |
| `GET /catalog` | Tool/resource/prompt catalog |
| `GET /metrics` | Prometheus metrics |
| `POST /mcp` | Streamable HTTP MCP endpoint |

## Security and Governance

- Read-only by default.
- Every tool enforces scope checks (`@require_scope`).
- Tool calls are audited (`@audit_tool`) with structured logs.
- SQL is validated and bounded for read-only operations.
- Redis/Kafka/S3/Postgres access is constrained by allowlists.
- Sensitive output is redacted before returning to clients.
- Local JWT bypass is only valid with `APP_ENV=local`.

## Sibling Repo Integration

This repo is a sibling of `card-fraud-platform` and is designed to run against platform infrastructure.

Live integration points:

- Docker network: `card-fraud-network`
- PostgreSQL: `card-fraud-postgres` / internal `postgres`
- Redis: `card-fraud-redis` / internal `redis`
- Redpanda: `card-fraud-redpanda` / internal `redpanda:29092`
- MinIO: `card-fraud-minio` / internal `minio`
- Jaeger OTLP: `card-fraud-jaeger`
- Prometheus: `card-fraud-prometheus`

## Quick Start (Local via Doppler)

```powershell
# 1) Install dependencies
uv sync --extra dev

# 2) Start gateway with Doppler-injected env
uv run doppler-local

# 3) Verify configuration and catalog
$env:APP_ENV='local'
$env:SECURITY_SKIP_JWT_VALIDATION='true'
uv run gateway-check
```

## Quick Start (Docker Against Platform Stack)

```powershell
# In card-fraud-platform repo (start shared infra)
cd ..\card-fraud-platform
doppler run -- uv run platform-up

# In this repo (start MCP gateway)
cd ..\card-fraud-mcp-gateway
$env:POSTGRES_ADMIN_PASSWORD=(docker inspect card-fraud-postgres --format "{{range .Config.Env}}{{println .}}{{end}}" | Select-String '^POSTGRES_PASSWORD=' | ForEach-Object {($_.ToString() -split '=',2)[1]})
docker compose up -d --build gateway

# Verify
curl http://localhost:8005/health
curl http://localhost:8005/ready
```

Note: immediately after container recreate on Windows, first MCP/HTTP call can briefly fail while startup settles. Retry after a few seconds.

## MCP Client Configuration

### Codex (`codex.toml`)

```toml
[mcp_servers.card-fraud-gateway]
url = "http://localhost:8005/mcp"
```

### Generic MCP JSON config

```json
{
  "mcpServers": {
    "card-fraud-gateway": {
      "url": "http://localhost:8005/mcp"
    }
  }
}
```

## Developer Commands

```bash
# Full quality gate commands used in this repo
uv run ruff check app/ tests/ cli/ scripts/
uv run ruff format --check app/ tests/ cli/ scripts/
uv run pytest tests/ -q
APP_ENV=local SECURITY_SKIP_JWT_VALIDATION=true uv run gateway-check

# Additional useful commands
uv run gateway-smoke
uv run gateway-export-catalog
uv run auth0-bootstrap --yes --verbose
uv run auth0-verify
```

## Validation Before Handoff

Run these and require all pass:

```bash
uv run ruff check app/ tests/ cli/ scripts/
uv run pytest tests/ -q
APP_ENV=local SECURITY_SKIP_JWT_VALIDATION=true uv run gateway-check
```

## Documentation Index

- `docs/01-setup/local-setup.md`
- `docs/01-setup/connecting-clients.md`
- `docs/02-development/architecture.md`
- `docs/04-testing/test-strategy.md`
- `docs/07-reference/tool-catalog-and-scope-matrix.md`

## Notes

- Runtime target is remote MCP over HTTP (`POST /mcp`).
- Destructive/admin operations are intentionally excluded from Phase 1.
- If contracts/scopes/catalogs change, update `README.md`, `docs/README.md`, and `docs/codemap.md` together.
