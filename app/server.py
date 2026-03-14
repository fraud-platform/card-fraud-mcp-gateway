"""MCP server factory — creates FastMCP instance and registers all tools, resources, prompts."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.security.policy import clear_policies


def create_mcp_server() -> FastMCP:
    """Build the FastMCP server with all domains registered."""
    clear_policies()
    # Disable FastMCP's built-in DNS rebinding protection.
    # This protection is designed for local dev tools (e.g. Claude Desktop on 127.0.0.1).
    # This gateway is an enterprise remote server that:
    #   1. Binds to 0.0.0.0 and accepts connections from external MCP clients
    #   2. Enforces its own Auth0 JWT authentication on every request
    #   3. Is deployed behind a TLS-terminating reverse proxy
    # Leaving it on blocks all legitimate external clients (Host header never matches
    # the default allowlist of 127.0.0.1/localhost).
    mcp = FastMCP(
        "card-fraud-gateway",
        instructions=(
            "Enterprise MCP gateway for the Card Fraud platform. "
            "Provides read-only access to PostgreSQL, Redis, Kafka/Redpanda, MinIO/S3, "
            "and platform services. All tools require authentication and are audited. "
            "Use the prompts for guided investigation workflows."
        ),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    # Domain tools — 22 tools across 6 domains
    from app.domains.kafka import register as reg_kafka
    from app.domains.ops import register as reg_ops
    from app.domains.platform import register as reg_platform
    from app.domains.postgres import register as reg_postgres
    from app.domains.redis import register as reg_redis
    from app.domains.storage import register as reg_storage

    reg_platform(mcp)
    reg_postgres(mcp)
    reg_redis(mcp)
    reg_kafka(mcp)
    reg_storage(mcp)
    reg_ops(mcp)

    # Resources — 7 resource URIs
    from app.resources import register as reg_resources

    reg_resources(mcp)

    # Prompts — 5 investigation/analysis templates
    from app.prompts import register as reg_prompts

    reg_prompts(mcp)

    return mcp
