# Flow 02 - Auth And Authorization

> **Status:** ⚠️ Partially Implemented  
> **Verified:** 2026-03-09

## Goal

Add enterprise authentication, scope enforcement, and approval policy primitives before any live infrastructure tools are exposed.

## Deliverables

- Auth0 JWT validation
- issuer and audience checks
- scope-to-tool policy registry
- caller identity extraction
- approval requirement model for future write tools
- structured authorization failure responses

## Implementation Tasks

1. Add Auth0 config model and startup validation.
2. Implement JWT validation middleware or request guard.
3. Define internal principal model with subject, scopes, client id, and environment.
4. Implement tool policy registry:
   - domain
   - allowed scopes
   - read-only flag
   - approval-required flag
5. Return uniform auth and authorization errors.
6. Emit audit records for denied requests.

## Guardrails

- no anonymous access in non-dev modes
- no fallback admin scope
- no tool registration without explicit policy metadata
- no implicit access inheritance across domains

## Verification

- valid token path succeeds
- wrong issuer fails
- missing scope fails
- denied access is audited

## Suggested Scope Families

- `fraud.platform.read`
- `fraud.db.read`
- `fraud.redis.read`
- `fraud.kafka.read`
- `fraud.storage.read`
- `fraud.ops.investigation.read`
- `fraud.ops.investigation.run`
- `fraud.admin.breakglass`

## Known Gaps

- ✔ Auth0 JWT validation with JWKS caching (1-hour TTL)
- ✔ Issuer and audience checks
- ✔ Scope-to-tool policy registry with `@require_scope` decorator
- ✔ CallerIdentity extraction and contextvar propagation
- ✔ Uniform 401 error responses via AuthMiddleware
- ✔ Dev identity with full scopes when auth disabled
- ✔ Denied request auditing from `require_scope` and `ensure_scope`
- ❌ **Approval workflow enforcement** — `ToolPolicy.approval_required` field exists but nothing reads or enforces it at runtime
- ❌ **Structured authorization failure responses** — scope denials raise `PermissionError` caught by MCP framework, not returned as structured JSON like auth failures

## Codex Prompt Seed

Implement Flow 02 for `card-fraud-mcp-gateway`. Add Auth0 JWT validation, scope checks, and tool policy registration primitives. Keep all tools read-only or disabled. Add tests for issuer, audience, and scope enforcement, and report the exact commands run.
