"""MCP server factory — creates MCPServer and registers all tools, resources, prompts."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from app.security.policy import clear_policies


def create_mcp_server() -> MCPServer:
    """Build the MCP server with all domains registered."""
    clear_policies()
    mcp = MCPServer(
        "card-fraud-gateway",
        instructions=(
            "Enterprise MCP gateway for the Card Fraud platform. "
            "Provides read-only access to PostgreSQL, Redis, Kafka/Redpanda, MinIO/S3, "
            "and platform services. All tools require authentication and are audited. "
            "Use the prompts for guided investigation workflows."
        ),
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
