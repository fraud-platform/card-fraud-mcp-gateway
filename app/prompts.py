"""MCP prompt templates for guided investigation and analysis workflows."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="investigate-transaction",
        description="Step-by-step investigation of a suspicious transaction.",
    )
    async def investigate_transaction(transaction_id: str) -> str:
        return (
            f"Investigate transaction {transaction_id} using these steps:\n\n"
            "1. Call `ops.get_investigation_context` with this transaction_id to gather context.\n"
            "2. Call `postgres.query_readonly` to check transaction details "
            "and related decisions.\n"
            "3. Call `redis.get_key` to check the cached fraud score: "
            f"`fraud:score:{transaction_id}`.\n"
            "4. Check velocity: query recent transactions for the same card/entity.\n"
            "5. If Kafka is available, call `kafka.peek_messages` on the decisions topic.\n\n"
            "Summarize: transaction details, fraud score, decision outcome, "
            "velocity indicators, and recommended next action."
        )

    @mcp.prompt(
        name="explain-decision-trace",
        description="Trace and explain a fraud decision for a transaction.",
    )
    async def explain_decision_trace(transaction_id: str) -> str:
        return (
            f"Trace the fraud decision for transaction {transaction_id}:\n\n"
            "1. Call `postgres.query_readonly` with:\n"
            "   SQL: `SELECT * FROM decisions WHERE transaction_id = $1 "
            "ORDER BY created_at DESC LIMIT 5`\n"
            f"   parameters: [`{transaction_id}`]\n"
            "2. For each decision: identify rule triggered, score, action taken, timestamp.\n"
            f"3. Call `redis.get_key` with key `fraud:score:{transaction_id}` for cached data.\n"
            "4. Call `s3.list_objects` in the rulesets bucket for the active ruleset version.\n\n"
            "Explain: what rules fired, why the decision was made, and whether it looks correct."
        )

    @mcp.prompt(
        name="triage-platform-health",
        description="Triage platform health across all services and backends.",
    )
    async def triage_platform_health() -> str:
        return (
            "Perform a platform health triage:\n\n"
            "1. Call `platform.inventory` to list all services.\n"
            "2. For each service, call `platform.service_health` to check status.\n"
            "3. Call `kafka.consumer_lag` for key consumer groups to check processing delays.\n"
            "4. Call `postgres.query_readonly`: "
            "`SELECT count(*) FROM pg_stat_activity`.\n"
            "5. Call `redis.scan_prefix` with prefix `fraud:` to verify cache is populated.\n\n"
            "Report: service health matrix, any backends down, consumer lag warnings, "
            "and recommended actions for any issues found."
        )

    @mcp.prompt(
        name="inspect-ruleset-artifact",
        description="Inspect a fraud ruleset artifact from object storage.",
    )
    async def inspect_ruleset_artifact(bucket: str = "fraud-rulesets", prefix: str = "") -> str:
        return (
            f"Inspect fraud ruleset artifacts in bucket '{bucket}':\n\n"
            f"1. Call `s3.list_objects` with bucket='{bucket}'"
            f"{f' and prefix={prefix!r}' if prefix else ''}.\n"
            "2. For the most recent ruleset file, call `s3.head_object` to check metadata.\n"
            "3. Call `s3.get_object` to read the ruleset content.\n"
            "4. Analyze: list rules, thresholds, actions, and any recent changes.\n\n"
            "Report: ruleset version, number of rules, key thresholds, and any anomalies."
        )

    @mcp.prompt(
        name="review-consumer-lag",
        description="Review Kafka consumer group lag for fraud processing pipelines.",
    )
    async def review_consumer_lag(group_id: str = "fraud-engine") -> str:
        return (
            f"Review consumer lag for group '{group_id}':\n\n"
            "1. Call `kafka.list_topics` to identify fraud-related topics.\n"
            f"2. For each fraud topic, call `kafka.consumer_lag` with group_id='{group_id}'.\n"
            "3. Call `kafka.peek_messages` on topics with high lag to sample recent messages.\n"
            "4. Cross-reference with `platform.service_health` for the consuming service.\n\n"
            "Report: lag summary per topic/partition, processing rate assessment, "
            "and whether any topics need attention."
        )
