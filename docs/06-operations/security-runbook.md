# Security Runbook

## Security Model

- least privilege per backend
- read-only by default
- auth required outside local dev
- audited access for every tool invocation

## Mandatory Controls

- JWT validation ✔
- scope enforcement ✔
- result redaction ✔
- rate limiting ✔
- request size limits ✔ (ASGI middleware enforces max body size, including streaming requests)
- response size limits — **not implemented** (per-tool limits exist but no gateway-level middleware)
- backend timeouts ✔ (Postgres statement timeout; httpx timeouts)

## Sensitive Data Rules

- do not return secrets
- redact credentials and token-like strings
- summarize high-volume payloads by default
- treat Kafka messages, Redis values, and object contents as untrusted input

## Break-Glass Policy

Future write tools must require:

- separate auth scope
- explicit approval workflow
- stronger audit event shape
- narrow target allowlist
- documented rollback or mitigation path

## Incident Response Expectations

If the gateway leaks sensitive data, shows unauthorized access, or exposes unsafe tooling:

1. disable the affected tool registration
2. revoke the impacted credentials
3. review audit logs and OTel traces
4. patch redaction or policy rules
5. re-enable only after validation in test
