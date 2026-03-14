# Deployment Topology

## Runtime Target

Deploy the gateway as a separate service in the Card Fraud suite, not as code embedded into `card-fraud-platform`.

## Why Separate Runtime

- independent scaling
- independent security boundary
- independent release cadence
- clean client endpoint
- easier audit and policy isolation

## Relationship To Platform

`card-fraud-platform` remains the local orchestrator and ownership source.

`card-fraud-mcp-gateway` is a suite component that consumes platform metadata and backend services.

## Environment Shape

### Local

- starts beside the platform stack
- uses platform-hosted infra on localhost
- uses local Auth0 dev config

### Test

- runs with test-only readonly credentials
- validates real auth scopes and audit export

### Production

- runs behind internal ingress
- requires OIDC auth
- restricted to approved internal clients and service accounts
- uses separate readonly credentials per backend

## Deployment Requirements

- TLS termination
- stable internal DNS name
- structured logs
- OTel export
- rate limiting
- secret management through Doppler or approved enterprise equivalent
