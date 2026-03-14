"""Tests for ops domain helpers."""

from __future__ import annotations

import pytest

from app.domains import ops


class TestValidateIdentifier:
    def test_valid_identifier(self):
        result = ops._validate_identifier("valid_name", "field")
        assert result == "valid_name"

    def test_valid_identifier_with_numbers(self):
        result = ops._validate_identifier("valid_name123", "field")
        assert result == "valid_name123"

    def test_valid_identifier_underscore_prefix(self):
        result = ops._validate_identifier("_underscore", "field")
        assert result == "_underscore"

    def test_invalid_identifier_starts_with_number(self):
        with pytest.raises(ValueError, match="Invalid field.*"):
            ops._validate_identifier("123invalid", "field")

    def test_invalid_identifier_special_chars(self):
        with pytest.raises(ValueError, match="Invalid field.*"):
            ops._validate_identifier("invalid-name", "field")


class TestResolveTableExpr:
    def test_resolve_table_expr_simple(self, monkeypatch):
        monkeypatch.setattr(ops.settings, "pg_allowed_schemas", None)
        monkeypatch.setattr(ops.settings, "pg_allowed_tables", None)
        result = ops._resolve_table_expr("my_table")
        assert result == '"public"."my_table"'

    def test_resolve_table_expr_with_schema(self, monkeypatch):
        monkeypatch.setattr(ops.settings, "pg_allowed_schemas", None)
        monkeypatch.setattr(ops.settings, "pg_allowed_tables", None)
        result = ops._resolve_table_expr("my_schema.my_table")
        assert result == '"my_schema"."my_table"'

    def test_resolve_table_expr_invalid_too_many_parts(self, monkeypatch):
        monkeypatch.setattr(ops.settings, "pg_allowed_schemas", None)
        monkeypatch.setattr(ops.settings, "pg_allowed_tables", None)
        with pytest.raises(ValueError, match="Invalid table setting"):
            ops._resolve_table_expr("a.b.c")


class TestCheckTableAllowed:
    def test_check_table_allowed_no_settings(self, monkeypatch):
        monkeypatch.setattr(ops.settings, "pg_allowed_schemas", None)
        monkeypatch.setattr(ops.settings, "pg_allowed_tables", None)
        ops._check_table_allowed("my_table", "public")
