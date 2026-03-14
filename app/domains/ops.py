"""Ops investigation tools — cross-domain context gathering and investigation workflows."""

from __future__ import annotations

import asyncio
import re

from mcp.server.fastmcp import FastMCP

from app.audit import audit_tool, tool_result
from app.backends import get_pg_pool, get_redis
from app.config import settings
from app.domains.postgres import _enforce_query_allowlists, _validate_sql
from app.security.policy import require_scope
from app.security.redaction import redact

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _get_ops_columns(table_setting: str) -> list[str]:
    """Return configured column list for an ops table, with validation.

    Maps table names to their configured column lists from settings.
    """
    if table_setting == settings.ops_transactions_table:
        return settings.ops_transactions_columns
    if table_setting == settings.ops_cases_table:
        return settings.ops_cases_columns
    if table_setting == settings.ops_decisions_table:
        return settings.ops_decisions_columns
    # Fallback for unknown tables - use minimal columns
    return ["id", "created_at", "updated_at"]


def _build_select_sql(table_setting: str, where_clause: str = "", extra: str = "") -> str:
    """Build a SELECT query with explicit columns for an ops table.

    Replaces SELECT * with explicit column lists for better performance
    on wide tables.
    """
    columns = _get_ops_columns(table_setting)
    column_list = ", ".join(f'"{col}"' for col in columns)
    table_expr = _resolve_table_expr(table_setting)

    sql = f"SELECT {column_list} FROM {table_expr}"
    if where_clause:
        sql += f" {where_clause}"
    if extra:
        sql += f" {extra}"
    return sql


def _validate_identifier(identifier: str, field_name: str) -> str:
    """Reject identifiers that do not match expected SQL identifier character set."""
    if not _IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"Invalid {field_name}: '{identifier}'")
    return identifier


def _resolve_table_expr(value: str, fallback_schema: str = "public") -> str:
    """Resolve and validate an ops table setting into a safely quoted identifier."""
    parts = value.split(".")
    if len(parts) == 1:
        table = _validate_identifier(parts[0].strip(), "table name")
        _check_table_allowed(table, fallback_schema)
        return f'"{fallback_schema}"."{table}"'
    if len(parts) == 2:
        schema = _validate_identifier(parts[0].strip(), "schema name")
        table = _validate_identifier(parts[1].strip(), "table name")
        _check_table_allowed(table, schema)
        return f'"{schema}"."{table}"'
    raise ValueError(f"Invalid table setting: '{value}'")


def _check_table_allowed(table: str, schema: str | None = "public") -> None:
    """Enforce PostgreSQL allowlists for ops tables."""
    if settings.pg_allowed_schemas:
        if schema is None:
            raise ValueError("Unqualified table names are not allowed.")
        from app.domains.postgres import _check_schema_allowed

        _check_schema_allowed(schema)
    if settings.pg_allowed_tables:
        from app.domains.postgres import _check_table_allowed as _check_pg_table

        _check_pg_table(table, schema or "")


async def _fetch_ops_rows(
    pool,
    sql: str,
    parameter: str,
) -> list[dict[str, object]]:
    """Validate, enforce allowlists, and execute a bounded ops SQL query."""
    normalized_sql = sql.rstrip(";")
    _validate_sql(normalized_sql)
    await _enforce_query_allowlists(normalized_sql, pool, (parameter,))
    bounded_sql = f"WITH _q AS ({normalized_sql}) SELECT * FROM _q LIMIT {settings.pg_max_rows + 1}"
    rows = await pool.fetch(bounded_sql, parameter)
    return [dict(r) for r in rows[: settings.pg_max_rows]]


async def load_postgres_investigation_context(
    pool,
    transaction_id: str = "",
    case_id: str = "",
) -> tuple[dict[str, object], list[str]]:
    """Fetch transaction/case records needed for an investigation."""
    context: dict[str, object] = {}
    sources: list[str] = []

    pg_queries = []
    result_indexes: dict[str, int] = {}
    if transaction_id:
        result_indexes["transaction"] = len(pg_queries)
        pg_queries.append(
            _fetch_ops_rows(
                pool,
                _build_select_sql(settings.ops_transactions_table, "WHERE transaction_id = $1"),
                transaction_id,
            )
        )
    if case_id:
        result_indexes["case"] = len(pg_queries)
        pg_queries.append(
            _fetch_ops_rows(
                pool,
                _build_select_sql(settings.ops_cases_table, "WHERE id = $1"),
                case_id,
            )
        )

    pg_results = await asyncio.gather(*pg_queries, return_exceptions=True) if pg_queries else []

    if "transaction" in result_indexes:
        rows = pg_results[result_indexes["transaction"]]
        if not isinstance(rows, Exception) and rows:
            context["transaction"] = dict(rows[0])
            sources.append("postgres:transactions")

    if "case" in result_indexes:
        rows = pg_results[result_indexes["case"]]
        if not isinstance(rows, Exception) and rows:
            context["case"] = dict(rows[0])
            sources.append("postgres:cases")

    return context, sources


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="ops.get_investigation_context",
        description=(
            "Gather investigation context for a transaction or case by combining data from "
            "Postgres and Redis. Provide at least one of transaction_id or case_id. "
            "Domain: ops | Read-only | Scope: fraud.ops.investigation.read"
        ),
    )
    @require_scope(
        "fraud.ops.investigation.read",
        domain="ops",
        tool_name="ops.get_investigation_context",
    )
    @audit_tool("ops")
    async def ops_get_investigation_context(transaction_id: str = "", case_id: str = "") -> str:
        if not transaction_id and not case_id:
            return tool_result({"error": "Provide either transaction_id or case_id"})

        context: dict = {"transaction_id": transaction_id, "case_id": case_id, "sources": []}

        # Postgres: transaction / case records (concurrent queries)
        try:
            pool = get_pg_pool()
            pg_context, pg_sources = await load_postgres_investigation_context(
                pool,
                transaction_id=transaction_id,
                case_id=case_id,
            )
            context.update(pg_context)
            context["sources"].extend(pg_sources)

        except Exception:
            context["db_note"] = "Postgres unavailable. Try again later."

        # Redis: cached fraud scores and flags (concurrent queries)
        try:
            client = get_redis()
            lookup = transaction_id or case_id

            # Query Redis concurrently for score and flags
            score_result, flags_result = await asyncio.gather(
                client.get(f"fraud:score:{lookup}"),
                client.smembers(f"fraud:flags:{lookup}"),
                return_exceptions=True,
            )

            if not isinstance(score_result, Exception) and score_result:
                context["cached_score"] = score_result
                context["sources"].append("redis:score")

            if not isinstance(flags_result, Exception) and flags_result:
                context["flags"] = list(flags_result)
                context["sources"].append("redis:flags")

        except Exception:
            context["redis_note"] = "Redis unavailable. Try again later."

        return redact(tool_result(context, default=str))

    @mcp.tool(
        name="ops.run_investigation",
        description=(
            "Run a structured investigation workflow. "
            "Types: 'transaction_review', 'case_triage', 'velocity_check'. "
            "Args: investigation_type, target_id. "
            "Domain: ops | Scope: fraud.ops.investigation.run"
        ),
    )
    @require_scope(
        "fraud.ops.investigation.run",
        domain="ops",
        read_only=False,
        tool_name="ops.run_investigation",
    )
    @audit_tool("ops")
    async def ops_run_investigation(investigation_type: str, target_id: str) -> str:
        valid_types = {"transaction_review", "case_triage", "velocity_check"}
        if investigation_type not in valid_types:
            return tool_result({"error": f"Invalid type. Choose from: {sorted(valid_types)}"})

        result: dict = {
            "investigation_type": investigation_type,
            "target_id": target_id,
            "status": "completed",
            "steps": [],
        }

        try:
            pool = get_pg_pool()

            if investigation_type == "transaction_review":
                rows = await _fetch_ops_rows(
                    pool,
                    _build_select_sql(settings.ops_transactions_table, "WHERE transaction_id = $1"),
                    target_id,
                )
                result["steps"].append({"step": "fetch_transaction", "found": len(rows) > 0})
                if rows:
                    result["transaction"] = dict(rows[0])
                    decisions = await _fetch_ops_rows(
                        pool,
                        _build_select_sql(
                            settings.ops_decisions_table,
                            "WHERE transaction_id = $1",
                            "ORDER BY created_at DESC LIMIT 10",
                        ),
                        target_id,
                    )
                    result["decisions"] = [dict(r) for r in decisions]
                    result["steps"].append({"step": "fetch_decisions", "count": len(decisions)})

            elif investigation_type == "case_triage":
                rows = await _fetch_ops_rows(
                    pool,
                    _build_select_sql(settings.ops_cases_table, "WHERE id = $1"),
                    target_id,
                )
                result["steps"].append({"step": "fetch_case", "found": len(rows) > 0})
                if rows:
                    result["case"] = dict(rows[0])

            elif investigation_type == "velocity_check":
                rows = await _fetch_ops_rows(
                    pool,
                    "SELECT count(*) AS txn_count, "
                    "min(created_at) AS first_txn, max(created_at) AS last_txn "
                    f"FROM {_resolve_table_expr(settings.ops_transactions_table)} "
                    "WHERE card_id = $1 AND created_at > now() - interval '24 hours'",
                    target_id,
                )
                if rows:
                    result["velocity"] = dict(rows[0])
                result["steps"].append({"step": "velocity_check", "completed": True})

        except Exception:
            result["error"] = "Investigation query failed. Contact support for details."
            result["status"] = "partial"

        return redact(tool_result(result, default=str))
