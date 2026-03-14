"""MCP resource registration — platform, schema, topic, artifact, and health resources."""

from __future__ import annotations

import structlog
from mcp.server.fastmcp import FastMCP

from app.audit import tool_result
from app.config import settings
from app.metrics import record_resource_read_failure
from app.security.policy import ensure_scope

_log = structlog.get_logger(__name__)


def register(mcp: FastMCP) -> None:
    @mcp.resource("fraud://platform/services")
    async def service_registry() -> str:
        """Service registry: all platform services with type and owner."""
        ensure_scope("fraud.platform.read", domain="platform", name="fraud://platform/services")
        from app.domains.platform import _INVENTORY_SOURCE, _SERVICE_INVENTORY

        return tool_result(
            {
                "services": [{"name": k, **v} for k, v in _SERVICE_INVENTORY.items()],
                "inventory_source": _INVENTORY_SOURCE,
            }
        )

    @mcp.resource("fraud://platform/ownership")
    async def ownership_summary() -> str:
        """Ownership summary grouped by team."""
        ensure_scope("fraud.platform.read", domain="platform", name="fraud://platform/ownership")
        from app.domains.platform import _SERVICE_INVENTORY

        by_owner: dict[str, list[str]] = {}
        for name, info in _SERVICE_INVENTORY.items():
            by_owner.setdefault(info["owner"], []).append(name)
        return tool_result(by_owner)

    @mcp.resource("fraud://schemas/catalog")
    async def schema_catalog() -> str:
        """Database schema catalog (requires active Postgres connection)."""
        ensure_scope("fraud.db.read", domain="postgres", name="fraud://schemas/catalog")
        try:
            from app.backends import get_pg_pool

            pool = get_pg_pool()
            rows = await pool.fetch(
                "SELECT table_schema, table_name, table_type "
                "FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
                "ORDER BY table_schema, table_name"
            )
            tables = [dict(r) for r in rows]
            if settings.pg_allowed_schemas:
                tables = [t for t in tables if t["table_schema"] in settings.pg_allowed_schemas]
            if settings.pg_allowed_tables:
                tables = [
                    t
                    for t in tables
                    if t["table_name"] in settings.pg_allowed_tables
                    or f"{t['table_schema']}.{t['table_name']}" in settings.pg_allowed_tables
                ]
            return tool_result({"tables": tables})
        except Exception as exc:
            record_resource_read_failure("fraud://schemas/catalog")
            _log.warning("resource_read_failed", resource="fraud://schemas/catalog", error=str(exc))
            return tool_result({"error": "Unable to read schema catalog at this time."})

    @mcp.resource("fraud://topics/catalog")
    async def topic_catalog() -> str:
        """Kafka topic catalog (requires active Kafka connection)."""
        ensure_scope("fraud.kafka.read", domain="kafka", name="fraud://topics/catalog")
        try:
            from app.backends import get_kafka_client
            from app.domains.kafka import list_visible_topics

            client = get_kafka_client()
            topics = await list_visible_topics(client)
            return tool_result({"topics": topics})
        except Exception as exc:
            record_resource_read_failure("fraud://topics/catalog")
            _log.warning("resource_read_failed", resource="fraud://topics/catalog", error=str(exc))
            return tool_result({"error": "Unable to read topic catalog at this time."})

    @mcp.resource("fraud://buckets/catalog")
    async def artifact_catalog() -> str:
        """S3/MinIO bucket catalog (requires active S3 connection)."""
        ensure_scope("fraud.storage.read", domain="storage", name="fraud://buckets/catalog")
        try:
            from app.backends import get_s3_session

            async with get_s3_session().client("s3", endpoint_url=settings.s3_endpoint) as s3:
                response = await s3.list_buckets()
            buckets = [b["Name"] for b in response.get("Buckets", [])]
            if settings.s3_allowed_buckets:
                buckets = [b for b in buckets if b in settings.s3_allowed_buckets]
            return tool_result({"buckets": buckets})
        except Exception as exc:
            record_resource_read_failure("fraud://buckets/catalog")
            _log.warning("resource_read_failed", resource="fraud://buckets/catalog", error=str(exc))
            return tool_result({"error": "Unable to read bucket catalog at this time."})

    @mcp.resource("fraud://health/topology")
    async def health_topology() -> str:
        """Platform health and topology overview — which backends are configured."""
        ensure_scope("fraud.platform.read", domain="platform", name="fraud://health/topology")
        topology = {
            "gateway": "card-fraud-mcp-gateway",
            "backends": {
                "postgres": "configured" if settings.pg_dsn else "not_configured",
                "redis": "configured" if settings.redis_url else "not_configured",
                "kafka": "configured" if settings.kafka_brokers else "not_configured",
                "s3": "configured" if settings.s3_endpoint else "not_configured",
                "platform_api": "configured" if settings.platform_api_url else "not_configured",
            },
        }
        return tool_result(topology)

    @mcp.resource("fraud://ops/investigation-context")
    async def investigation_context_bundle() -> str:
        """Investigation context bundle — available data sources and investigation types."""
        ensure_scope(
            "fraud.ops.investigation.read",
            domain="ops",
            name="fraud://ops/investigation-context",
        )
        bundle = {
            "description": "Cross-domain investigation context for fraud analysts",
            "available_tools": [
                {
                    "tool": "ops.get_investigation_context",
                    "scope": "fraud.ops.investigation.read",
                    "inputs": ["transaction_id", "case_id"],
                    "sources": [
                        "postgres:transactions",
                        "postgres:cases",
                        "redis:scores",
                        "redis:flags",
                    ],
                },
                {
                    "tool": "ops.run_investigation",
                    "scope": "fraud.ops.investigation.run",
                    "types": ["transaction_review", "case_triage", "velocity_check"],
                },
            ],
            "recommended_prompts": [
                "investigate-transaction",
                "explain-decision-trace",
            ],
            "tables": {
                "transactions": settings.ops_transactions_table,
                "cases": settings.ops_cases_table,
                "decisions": settings.ops_decisions_table,
            },
        }
        return tool_result(bundle)
