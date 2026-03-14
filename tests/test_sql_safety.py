"""Tests for SQL safety validation in the Postgres domain."""

import pytest

from app.domains.postgres import _validate_sql


class TestAllowedQueries:
    def test_simple_select(self):
        _validate_sql("SELECT 1")

    def test_select_with_where(self):
        _validate_sql("SELECT * FROM transactions WHERE id = '123'")

    def test_with_cte(self):
        _validate_sql("WITH t AS (SELECT 1) SELECT * FROM t")

    def test_explain(self):
        _validate_sql("EXPLAIN SELECT * FROM transactions")

    def test_trailing_semicolon(self):
        _validate_sql("SELECT 1;")

    def test_subquery(self):
        _validate_sql("SELECT * FROM (SELECT id FROM transactions) AS sub")


class TestRejectedQueries:
    def test_insert(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _validate_sql("INSERT INTO foo VALUES (1)")

    def test_update(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _validate_sql("UPDATE foo SET bar = 1")

    def test_delete(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _validate_sql("DELETE FROM foo")

    def test_drop_table(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _validate_sql("DROP TABLE foo")

    def test_alter_table(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _validate_sql("ALTER TABLE foo ADD COLUMN bar int")

    def test_truncate(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _validate_sql("TRUNCATE foo")

    def test_create_table(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _validate_sql("CREATE TABLE foo (id int)")

    def test_grant(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _validate_sql("GRANT ALL ON foo TO public")

    def test_multi_statement(self):
        with pytest.raises(ValueError):
            _validate_sql("SELECT 1; DROP TABLE foo")

    def test_multi_statement_safe_content(self):
        with pytest.raises(ValueError, match="Multiple"):
            _validate_sql("SELECT 1; SELECT 2")

    def test_empty_query(self):
        with pytest.raises(ValueError, match="Empty"):
            _validate_sql("")

    def test_begin_transaction(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _validate_sql("BEGIN; SELECT 1; COMMIT")

    def test_copy(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _validate_sql("COPY foo TO '/tmp/out.csv'")
