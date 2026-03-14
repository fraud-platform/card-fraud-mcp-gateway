"""PostgreSQL read-only tools — schema discovery and bounded query execution."""

from __future__ import annotations

import json
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.audit import audit_tool, tool_result
from app.backends import get_pg_pool
from app.config import settings
from app.metrics import record_result_truncation
from app.security.allowlist import check_exact
from app.security.policy import require_scope
from app.security.redaction import redact_dict

# ---- SQL Safety ----

_FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
    r"COPY|EXECUTE|CALL|SET|RESET|BEGIN|COMMIT|ROLLBACK|SAVEPOINT|"
    r"LOCK|VACUUM|CLUSTER|REINDEX|DISCARD|COMMENT|SECURITY|OWNER|DO|"
    r"PREPARE|LISTEN|NOTIFY|LOAD|UNLISTEN|CLUSTER)\b",
    re.IGNORECASE,
)
_EXPLAIN_PREFIX = re.compile(r"^\s*EXPLAIN(?:\s*\([^)]*\))?\s+", re.IGNORECASE)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RELATION_CACHE: dict[str, set[tuple[str | None, str]]] = {}
_RELATION_CACHE_MAX = 500

# Regex patterns for extracting table references from SQL without EXPLAIN
# Matches: FROM schema.table, FROM table, JOIN schema.table, JOIN table
_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:(?P<schema>[A-Za-z_][A-Za-z0-9_]*)\.)?(?P<table>[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def _normalize_sql_for_cache(sql: str) -> str:
    """Normalize SQL for deterministic allowlist cache lookup."""
    compact = re.sub(r"\s+", " ", sql.strip())
    return compact.rstrip().rstrip(";")


def _strip_comments_and_literals(sql: str) -> str:
    """Strip SQL comments and quoted literals for safe token checks."""
    output: list[str] = []
    i = 0
    length = len(sql)

    while i < length:
        # Single-line comment
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            i = length if end == -1 else end + 1
            continue

        # Block comment
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = length if end == -1 else end + 2
            continue

        # Single-quoted string
        if sql[i] == "'":
            i += 1
            while i < length:
                if sql[i] == "'":
                    i += 1
                    if i < length and sql[i] == "'":
                        i += 1
                        continue
                    break
                if sql[i] == "\\":
                    i += 2
                    continue
                i += 1
            continue

        # Dollar-quoted string
        if sql[i] == "$":
            tag_end = sql.find("$", i + 1)
            if tag_end == -1:
                output.append(sql[i])
                i += 1
                continue

            # Tag is either "$$" or "$tag$"
            tag = sql[i : tag_end + 1]
            if tag == "$$":
                i = tag_end + 1
                close_at = sql.find("$$", i)
                i = length if close_at == -1 else close_at + 2
                continue
            if _IDENT_RE.fullmatch(sql[i + 1 : tag_end]):
                i = tag_end + 1
                close_tag = tag
                close_at = sql.find(close_tag, i)
                i = length if close_at == -1 else close_at + len(close_tag)
                continue

            output.append(sql[i])
            i += 1
            continue

        output.append(sql[i])
        i += 1

    return "".join(output)


def _contains_multiple_statements(sql: str) -> bool:
    """Return True when SQL contains multiple statements."""
    sanitized = _strip_comments_and_literals(sql)
    segments = [segment for segment in sanitized.split(";") if segment.strip()]
    return len(segments) > 1


def _normalize_start(sql: str) -> str:
    """Remove wrapping spaces/comments and leading parentheses for prefix checks."""
    cleaned = _strip_comments_and_literals(sql).lstrip()
    return cleaned.lstrip("(").upper()


def _validate_sql(sql: str) -> None:
    """Reject non-readonly / multi-statement SQL."""
    stripped = sql.strip().rstrip(";")
    if not stripped:
        raise ValueError("Empty SQL query.")
    sanitized = _strip_comments_and_literals(stripped)
    if _FORBIDDEN_SQL.search(sanitized):
        raise ValueError(
            "Only SELECT queries are allowed. DDL/DML and transaction control are forbidden."
        )
    if _contains_multiple_statements(sanitized):
        raise ValueError("Multiple SQL statements are not allowed.")
    upper = _normalize_start(stripped)
    if not (upper.startswith("SELECT") or upper.startswith("WITH") or upper.startswith("EXPLAIN")):
        raise ValueError("Query must start with SELECT, WITH, or EXPLAIN.")


def _serialize(obj: object) -> Any:
    """JSON-safe serialization of asyncpg result values."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.hex()
    if isinstance(obj, set):
        return list(obj)
    return str(obj)


def _check_schema_allowed(schema: str) -> None:
    """Reject access to schemas not in the allowlist (when configured)."""
    check_exact(schema, settings.pg_allowed_schemas, "Schema")


def _check_table_allowed(table: str, schema: str) -> None:
    """Reject access to tables not in the allowlist (when configured)."""
    allowed = settings.pg_allowed_tables
    if not allowed:
        if settings.enforce_allowlists:
            raise ValueError(f"Table allowlist not configured. Table '{table}' is blocked.")
        return
    if table not in allowed and f"{schema}.{table}" not in allowed:
        raise ValueError(
            f"Table '{schema}.{table}' is not in the allowed list. Allowed: {sorted(allowed)}"
        )


def _strip_explain(sql: str) -> str:
    """Remove the EXPLAIN prefix so allowlist checks inspect the underlying query."""
    return _EXPLAIN_PREFIX.sub("", sql, count=1).strip()


def _extract_tables_from_sql(sql: str) -> set[tuple[str | None, str]] | None:
    """Extract table references from SQL using regex.

    Returns set of relations for simple queries, or None if the query is too
    complex for regex-based extraction (e.g., CTEs, subqueries, UNION),
    requiring EXPLAIN fallback. This eliminates the DB round-trip for common
    simple SELECT queries.
    """
    normalized = sql.upper()

    # EXPLAIN queries need special handling - always use EXPLAIN fallback
    if normalized.startswith("EXPLAIN"):
        return None

    # Check for complex constructs that require EXPLAIN
    complex_patterns = (
        r"\bWITH\s+",  # CTEs (WITH ... AS)
        r"\bUNION\b",  # UNION queries
        r"\bINTERSECT\b",  # INTERSECT queries
        r"\bEXCEPT\b",  # EXCEPT queries
        r"\bLATERAL\b",  # LATERAL joins
        r"\(SELECT\s+",  # Subqueries
    )
    for pattern in complex_patterns:
        if re.search(pattern, normalized):
            return None

    # Extract tables using regex - only works for simple SELECT/FROM/JOIN patterns
    relations: set[tuple[str | None, str]] = set()
    cleaned_sql = _strip_comments_and_literals(sql)

    for match in _TABLE_RE.finditer(cleaned_sql):
        schema = match.group("schema")
        table = match.group("table")
        # Filter out SQL keywords that might match the pattern
        if table.upper() in (
            "SELECT",
            "WHERE",
            "ORDER",
            "GROUP",
            "HAVING",
            "LIMIT",
            "OFFSET",
            "FETCH",
            "FOR",
            "INNER",
            "LEFT",
            "RIGHT",
            "FULL",
            "OUTER",
            "CROSS",
            "NATURAL",
            "AS",
            "ON",
            "USING",
            "AND",
            "OR",
            "NOT",
            "NULL",
            "TRUE",
            "FALSE",
            "CASE",
            "WHEN",
            "THEN",
            "ELSE",
            "END",
            "CAST",
            "EXISTS",
            "IN",
            "BETWEEN",
            "LIKE",
            "IS",
            "VALUES",
            "SET",
            "INTO",
            "DISTINCT",
            "ALL",
            "ANY",
        ):
            continue
        relations.add((schema if schema else None, table))

    # Return None if no tables found (signal to use EXPLAIN fallback)
    # This handles edge cases where regex misses tables in complex queries
    return relations if relations else None


def _collect_relations(node: object, relations: set[tuple[str | None, str]]) -> None:
    """Collect relation references from a PostgreSQL EXPLAIN JSON plan tree."""
    if isinstance(node, dict):
        relation = node.get("Relation Name")
        if isinstance(relation, str):
            schema = node.get("Schema")
            relations.add((schema if isinstance(schema, str) else None, relation))
        for value in node.values():
            _collect_relations(value, relations)
        return

    if isinstance(node, list):
        for item in node:
            _collect_relations(item, relations)


async def _extract_query_relations(
    sql: str,
    pool,
    parameters: tuple[object, ...] = (),
) -> set[tuple[str | None, str]]:
    """Extract relations from SQL query, using regex for simple queries.

    Tries regex-based extraction first (no DB round-trip), falling back to
    EXPLAIN for complex queries (CTEs, subqueries, UNION, etc.).
    """
    # Try regex-based extraction first (avoids DB round-trip)
    relations = _extract_tables_from_sql(sql)
    if relations is not None:
        return relations

    # Fall back to EXPLAIN for complex queries
    plan_payload = await pool.fetchval(
        f"EXPLAIN (VERBOSE, FORMAT JSON) {_strip_explain(sql)}",
        *parameters,
    )
    plan_data = json.loads(plan_payload) if isinstance(plan_payload, str) else plan_payload

    relations: set[tuple[str | None, str]] = set()
    _collect_relations(plan_data, relations)
    return relations


async def _enforce_query_allowlists(
    sql: str,
    pool,
    parameters: tuple[object, ...] = (),
) -> None:
    """Ensure raw SQL only touches relations permitted by the configured allowlists."""
    if settings.enforce_allowlists and not (
        settings.pg_allowed_schemas or settings.pg_allowed_tables
    ):
        raise ValueError("PostgreSQL allowlists are enabled but not configured.")
    if not (settings.pg_allowed_schemas or settings.pg_allowed_tables):
        return

    cache_key = _normalize_sql_for_cache(sql)
    if _FORBIDDEN_SQL.search(cache_key):
        raise ValueError("Query references forbidden SQL keywords. Expected read-only access only.")

    relations = _RELATION_CACHE.get(cache_key)
    if relations is None:
        relations = await _extract_query_relations(sql, pool, parameters)
        if len(_RELATION_CACHE) >= _RELATION_CACHE_MAX:
            # Evict oldest entry (dict preserves insertion order since Python 3.7)
            _RELATION_CACHE.pop(next(iter(_RELATION_CACHE)))
        _RELATION_CACHE[cache_key] = relations

    if not settings.pg_allowed_schemas and not settings.pg_allowed_tables:
        return

    if not relations:
        raise ValueError(
            "Query must reference allowed tables when PostgreSQL allowlists are configured."
        )

    for schema, table in relations:
        if settings.pg_allowed_schemas:
            if schema is None:
                raise ValueError(
                    "Unqualified table references are not allowed when schema "
                    "allowlists are configured."
                )
            _check_schema_allowed(schema)

        if settings.pg_allowed_tables:
            if schema is not None:
                _check_table_allowed(table, schema)
                continue
            if table not in settings.pg_allowed_tables:
                raise ValueError(
                    f"Table '{table}' is not in the allowed list. "
                    f"Allowed: {sorted(settings.pg_allowed_tables)}"
                )


# ---- Tool Registration ----


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="postgres.list_schemas",
        description=(
            "List all non-system schemas in the fraud database. "
            "Domain: postgres | Read-only | Scope: fraud.db.read"
        ),
    )
    @require_scope("fraud.db.read", domain="postgres", tool_name="postgres.list_schemas")
    @audit_tool("postgres")
    async def postgres_list_schemas() -> str:
        pool = get_pg_pool()
        rows = await pool.fetch(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
            "ORDER BY schema_name"
        )
        schemas = [r["schema_name"] for r in rows]
        allowed = settings.pg_allowed_schemas
        if allowed:
            schemas = [s for s in schemas if s in allowed]
        return tool_result({"schemas": schemas})

    @mcp.tool(
        name="postgres.list_tables",
        description=(
            "List tables in a database schema. "
            "Args: schema (default 'public'). "
            "Domain: postgres | Read-only | Scope: fraud.db.read"
        ),
    )
    @require_scope("fraud.db.read", domain="postgres", tool_name="postgres.list_tables")
    @audit_tool("postgres")
    async def postgres_list_tables(schema: str = "public") -> str:
        _check_schema_allowed(schema)
        pool = get_pg_pool()
        rows = await pool.fetch(
            "SELECT table_name, table_type "
            "FROM information_schema.tables "
            "WHERE table_schema = $1 "
            "ORDER BY table_name",
            schema,
        )
        tables = [{"name": r["table_name"], "type": r["table_type"]} for r in rows]
        return tool_result({"schema": schema, "tables": tables, "count": len(tables)})

    @mcp.tool(
        name="postgres.describe_table",
        description=(
            "Describe columns of a table including types, nullability, and defaults. "
            "Args: table, schema (default 'public'). "
            "Domain: postgres | Read-only | Scope: fraud.db.read"
        ),
    )
    @require_scope("fraud.db.read", domain="postgres", tool_name="postgres.describe_table")
    @audit_tool("postgres")
    async def postgres_describe_table(table: str, schema: str = "public") -> str:
        _check_schema_allowed(schema)
        _check_table_allowed(table, schema)
        pool = get_pg_pool()
        rows = await pool.fetch(
            "SELECT column_name, data_type, is_nullable, column_default, "
            "character_maximum_length "
            "FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = $2 "
            "ORDER BY ordinal_position",
            schema,
            table,
        )
        columns = [
            {
                "name": r["column_name"],
                "type": r["data_type"],
                "nullable": r["is_nullable"] == "YES",
                "default": r["column_default"],
                "max_length": r["character_maximum_length"],
            }
            for r in rows
        ]
        return tool_result(
            {"schema": schema, "table": table, "columns": columns, "count": len(columns)}
        )

    @mcp.tool(
        name="postgres.query_readonly",
        description=(
            "Execute a read-only SQL query against the fraud database. "
            f"Only SELECT/WITH/EXPLAIN allowed. Max {settings.pg_max_rows} rows returned. "
            "Args: sql, parameters (optional positional bind values). "
            "Domain: postgres | Read-only | Scope: fraud.db.read"
        ),
    )
    @require_scope("fraud.db.read", domain="postgres", tool_name="postgres.query_readonly")
    @audit_tool("postgres")
    async def postgres_query_readonly(
        sql: str,
        parameters: list[object] | None = None,
    ) -> str:
        _validate_sql(sql)
        pool = get_pg_pool()
        query_parameters = tuple(parameters or ())

        if settings.enforce_allowlists or settings.pg_allowed_schemas or settings.pg_allowed_tables:
            await _enforce_query_allowlists(sql, pool, query_parameters)

        normalized_sql = sql.rstrip(";")
        is_explain = bool(_EXPLAIN_PREFIX.match(normalized_sql))
        bounded_sql = normalized_sql
        if not is_explain:
            # Use a CTE to preserve ORDER BY from the user query, then apply LIMIT.
            bounded_sql = (
                f"WITH _q AS ({normalized_sql}) SELECT * FROM _q LIMIT {settings.pg_max_rows + 1}"
            )
        rows = await pool.fetch(bounded_sql, *query_parameters)

        truncated = len(rows) > settings.pg_max_rows
        if truncated:
            record_result_truncation("postgres", "postgres.query_readonly", "max_rows")
            rows = rows[: settings.pg_max_rows]

        data = [dict(r) for r in rows]
        payload = {
            "rows": data,
            "row_count": len(data),
            "truncated": truncated,
            "max_rows": settings.pg_max_rows,
        }
        return json.dumps(redact_dict(payload), indent=2, default=_serialize)
