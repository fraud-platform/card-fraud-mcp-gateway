# Auth0 Setup Guide

This guide covers provisioning the Auth0 resources needed by the MCP gateway.
All steps are automated via `auth0-bootstrap` — this doc explains what it does and
how to verify it worked.

> **Platform-shared tenant** — all `card-fraud-*` services share one Auth0 tenant.
> The Management M2M client and API Resource Servers are provisioned once per project,
> not once per developer. Only one person on the team needs to run the bootstrap.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Doppler access | `doppler login` with the org account |
| `card-fraud-mcp-gateway` Doppler project | `local` and `prod` configs must exist |
| `AUTH0_MGMT_CLIENT_SECRET` in Doppler `local` config | Shared management M2M secret — get from a team member |

---

## What the bootstrap creates

| Resource | Name | Value |
|----------|------|-------|
| API Resource Server | Card Fraud MCP Gateway API | audience: `https://card-fraud-mcp-gateway` |
| Scopes | 7 scopes | see table below |
| M2M Application | Card Fraud MCP Gateway M2M | `client_credentials` grant |
| Client Grant | M2M → API | all 7 scopes |

### Scopes

| Scope | Purpose |
|-------|---------|
| `fraud.platform.read` | Service inventory, health, ownership metadata |
| `fraud.db.read` | PostgreSQL schema/table discovery and bounded SELECT |
| `fraud.redis.read` | Redis key listing and value inspection |
| `fraud.kafka.read` | Kafka topic metadata and consumer group inspection |
| `fraud.storage.read` | MinIO bucket/prefix listing and object metadata |
| `fraud.ops.investigation.read` | Read fraud case and decision context |
| `fraud.ops.investigation.run` | Run investigation queries against fraud tables |

---

## Step 1 — Run the bootstrap

```bash
uv run auth0-bootstrap
```

The script is **idempotent** — safe to re-run. It patches existing resources instead of
creating duplicates.

Pass `--verbose` for detailed output, `--yes` to skip confirmation prompts in CI:

```bash
uv run auth0-bootstrap --yes --verbose
```

The bootstrap automatically syncs `GATEWAY_AUTH0_CLIENT_ID` and
`GATEWAY_AUTH0_CLIENT_SECRET` to both the `local` and `prod` Doppler configs.

---

## Step 2 — Enable RBAC in the Auth0 dashboard

This step **cannot be automated** via the Management API — it must be done once in the
Auth0 dashboard:

1. Open [https://manage.auth0.com](https://manage.auth0.com)
2. Navigate to **Applications → APIs → Card Fraud MCP Gateway API**
3. Click the **Settings** tab
4. Under **RBAC Settings**, enable:
   - ✅ **Enable RBAC**
   - ✅ **Add Permissions in the Access Token**
5. Click **Save Changes**

Without this step, issued tokens will not include scopes and all tool calls will be
rejected with `403 Forbidden`.

---

## Step 3 — Verify

```bash
uv run auth0-verify
```

Expected output: all checks pass (exit code 0).

```
[1] API Resource Server — https://card-fraud-mcp-gateway
  ✓ API found
  ✓ All 7 scopes present

[2] M2M Application
  ✓ Card Fraud MCP Gateway M2M found

[3] Client Grant
  ✓ Client grant found with all 7 scopes

All checks passed.
```

If any check fails, re-run `auth0-bootstrap` to repair the resource.

---

## Tenant reference

| Item | Value |
|------|-------|
| Tenant domain | `<your-auth0-tenant-domain>` |
| Management M2M client (shared) | `<from-auth0-dashboard>` |
| Gateway audience | `https://card-fraud-mcp-gateway` |
| Doppler key for client ID | `GATEWAY_AUTH0_CLIENT_ID` |
| Doppler key for client secret | `GATEWAY_AUTH0_CLIENT_SECRET` |

---

## Obtaining a token for manual testing

```bash
export CLIENT_ID=$(doppler secrets get GATEWAY_AUTH0_CLIENT_ID --plain)
export CLIENT_SECRET=$(doppler secrets get GATEWAY_AUTH0_CLIENT_SECRET --plain)
export AUTH0_MGMT_DOMAIN=$(doppler secrets get AUTH0_MGMT_DOMAIN --plain)

curl -s -X POST https://$AUTH0_MGMT_DOMAIN/oauth/token \
  -H "Content-Type: application/json" \
  -d "{
    \"grant_type\": \"client_credentials\",
    \"client_id\": \"$CLIENT_ID\",
    \"client_secret\": \"$CLIENT_SECRET\",
    \"audience\": \"https://card-fraud-mcp-gateway\"
  }" | python -m json.tool
```

The returned `access_token` can be used as a `Bearer` token against the `/mcp` endpoint.
