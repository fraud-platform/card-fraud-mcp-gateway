# Local Setup

This guide sets up the gateway on your local machine using Doppler for secrets and
`APP_ENV=local` to bypass JWT validation so you can develop without a live Auth0 tenant.

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| [`uv`](https://docs.astral.sh/uv/) | Python package / venv manager |
| [Doppler CLI](https://docs.doppler.com/docs/cli) | `brew install dopplerhq/cli/doppler` or scoop |
| Docker Desktop | Runs the shared platform infra stack |
| `card-fraud-platform` repo | Provides Postgres, Redis, Redpanda, MinIO |

---

## Step 1 — Clone and install

```bash
git clone <this-repo>
cd card-fraud-mcp-gateway
uv sync --extra dev
```

---

## Step 2 — Authenticate Doppler

```bash
doppler login                    # opens browser — log in with your org account
doppler setup                    # select project: card-fraud-mcp-gateway, config: local
```

Doppler now injects all `GATEWAY_*` environment variables automatically.
No `.env` files are used or needed.

---

## Step 3 — Start the platform infra stack

```bash
cd ../card-fraud-platform
docker compose up -d
```

This starts Postgres, Redis, Redpanda (Kafka), and MinIO on their default local ports.

If you want to run the gateway itself in Docker (instead of `uv run doppler-local`),
set `POSTGRES_ADMIN_PASSWORD` from the platform Postgres container and start this repo's
compose stack:

```powershell
# from card-fraud-mcp-gateway/
$env:POSTGRES_ADMIN_PASSWORD=(docker inspect card-fraud-postgres --format "{{range .Config.Env}}{{println .}}{{end}}" | Select-String '^POSTGRES_PASSWORD=' | ForEach-Object {($_.ToString() -split '=',2)[1]})
docker compose up -d --build gateway
```

---

## Step 4 — Verify configuration

Print the live tool catalog and confirm settings load without errors:

```bash
uv run doppler-local gateway-check
```

Expected output: a summary of 22 tools, 7 resources, 5 prompts with their scopes, and
`Auth: JWT bypass (APP_ENV=local)` confirming no JWT validation is active.

---

## Step 5 — Start the gateway

```bash
uv run doppler-local
```

The server starts on `http://localhost:8005` with hot-reload enabled.
Secrets are injected by Doppler; `APP_ENV=local` and `SECURITY_SKIP_JWT_VALIDATION=true`
are set automatically.

---

## Step 6 — Verify the server

```bash
# Liveness
curl http://localhost:8005/health

# Readiness (checks backend connectivity)
curl http://localhost:8005/ready

# Full tool/resource/prompt catalog
curl http://localhost:8005/catalog | python -m json.tool

# Prometheus metrics
curl http://localhost:8005/metrics
```

Expected `/health` response:

```json
{"status": "ok"}
```

`/ready` reports each backend as `ok` or `error`. Backends that are not configured
show `error` — this is expected if you are not running the full platform stack.

---

## Step 7 — Connect an MCP client

See [`connecting-clients.md`](connecting-clients.md) for step-by-step instructions for
Claude Code, GitHub Copilot, Codex CLI, and curl.

---

## Running tests

```bash
uv run doppler-test               # full pytest suite via Doppler (302 tests)
uv run pytest tests/ -v           # direct pytest (requires SECURITY_SKIP_JWT_VALIDATION=true in shell)
uv run gateway-smoke              # boot + hit /health /ready /catalog
uv run gateway-export-catalog     # export catalog as JSON to stdout
```

---

## Backend port reference

| Backend | Default local port |
|---------|--------------------|
| PostgreSQL | `localhost:5432` |
| Redis | `localhost:6379` |
| Redpanda | `localhost:9092` |
| MinIO | `localhost:9000` |
| Platform API | `localhost:8080` |

---

## Sibling repo layout

```text
github/
├── card-fraud-platform          ← start infra from here
├── card-fraud-mcp-gateway       ← this repo
├── card-fraud-ops-analyst-agent
└── ... (other card-fraud-* repos)
```

Start shared infra from `card-fraud-platform` first, then start this gateway.
