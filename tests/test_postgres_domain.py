"""Tests for postgres domain helpers."""

from __future__ import annotations

import pytest

from app.domains import postgres


class TestPostgresHelpers:
    def test_check_schema_allowed_with_allowlist(self, monkeypatch):
        monkeypatch.setattr(postgres.settings, "pg_allowed_schemas", ["allowed_schema"])
        postgres._check_schema_allowed("allowed_schema")

    def test_check_table_allowed_with_allowlist(self, monkeypatch):
        monkeypatch.setattr(postgres.settings, "pg_allowed_tables", ["allowed_table"])
        postgres._check_table_allowed("allowed_table", "public")

    def test_check_table_allowed_qualified(self, monkeypatch):
        monkeypatch.setattr(postgres.settings, "pg_allowed_tables", ["schema.table"])
        postgres._check_table_allowed("table", "schema")


class TestNormalizeSql:
    def test_normalize_sql_for_cache(self):
        result = postgres._normalize_sql_for_cache("SELECT 1")
        assert "SELECT" in result

    def test_strip_comments_and_literals(self):
        result = postgres._strip_comments_and_literals("-- comment\nSELECT 1")
        assert "--" not in result

    def test_contains_multiple_statements(self):
        assert postgres._contains_multiple_statements("SELECT 1; SELECT 2") is True
        assert postgres._contains_multiple_statements("SELECT 1") is False

    def test_normalize_start(self):
        result = postgres._normalize_start("  SELECT 1")
        assert result.startswith("SELECT")

    def test_validate_sql_valid(self):
        postgres._validate_sql("SELECT * FROM users WHERE id = 1")

    def test_validate_sql_invalid_dml(self):
        with pytest.raises(ValueError, match="SELECT"):
            postgres._validate_sql("DROP TABLE users")


class TestStripExplain:
    def test_strip_explain(self):
        result = postgres._strip_explain("EXPLAIN SELECT 1")
        assert "EXPLAIN" not in result

    def test_strip_explain_analyze(self):
        result = postgres._strip_explain("EXPLAIN ANALYZE SELECT 1")
        assert "EXPLAIN" not in result
