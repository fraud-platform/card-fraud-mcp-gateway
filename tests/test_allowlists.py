"""Tests for config-driven allowlist enforcement in all backend domains."""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---- Postgres allowlists ----


class TestPostgresAllowlists:
    def test_schema_allowed_when_open(self):
        from app.domains.postgres import _check_schema_allowed

        with (
            patch("app.security.allowlist.settings") as allowlist_settings,
            patch("app.domains.postgres.settings") as mock_s,
        ):
            allowlist_settings.enforce_allowlists = False
            mock_s.pg_allowed_schemas = []
            _check_schema_allowed("anything")  # no error

    def test_schema_allowed_when_in_list(self):
        from app.domains.postgres import _check_schema_allowed

        with patch("app.domains.postgres.settings") as mock_s:
            mock_s.pg_allowed_schemas = ["public", "fraud"]
            _check_schema_allowed("public")  # ok
            _check_schema_allowed("fraud")  # ok

    def test_schema_rejected_when_not_in_list(self):
        from app.domains.postgres import _check_schema_allowed

        with patch("app.domains.postgres.settings") as mock_s:
            mock_s.pg_allowed_schemas = ["public"]
            with pytest.raises(ValueError, match="not in the allowed"):
                _check_schema_allowed("secret_schema")

    def test_table_allowed_when_open(self):
        from app.domains.postgres import _check_table_allowed

        with (
            patch("app.security.allowlist.settings") as allowlist_settings,
            patch("app.domains.postgres.settings") as mock_s,
        ):
            allowlist_settings.enforce_allowlists = False
            mock_s.enforce_allowlists = False
            mock_s.pg_allowed_tables = []
            _check_table_allowed("anything", "public")  # no error

    def test_table_allowed_unqualified(self):
        from app.domains.postgres import _check_table_allowed

        with patch("app.domains.postgres.settings") as mock_s:
            mock_s.pg_allowed_tables = ["transactions"]
            _check_table_allowed("transactions", "public")  # ok

    def test_table_allowed_qualified(self):
        from app.domains.postgres import _check_table_allowed

        with patch("app.domains.postgres.settings") as mock_s:
            mock_s.pg_allowed_tables = ["fraud.transactions"]
            _check_table_allowed("transactions", "fraud")  # ok

    def test_table_rejected(self):
        from app.domains.postgres import _check_table_allowed

        with patch("app.domains.postgres.settings") as mock_s:
            mock_s.pg_allowed_tables = ["transactions"]
            with pytest.raises(ValueError, match="not in the allowed"):
                _check_table_allowed("secret_table", "public")


# ---- Redis allowlists ----


class TestRedisAllowlists:
    def test_prefix_allowed_when_open(self):
        from app.security.allowlist import check_prefix

        with patch("app.security.allowlist.settings") as allowlist_settings:
            allowlist_settings.enforce_allowlists = False
            check_prefix("anything:", [], "Prefix")  # no error

    def test_prefix_allowed_when_matches(self):
        from app.security.allowlist import check_prefix

        check_prefix("fraud:scores", ["fraud:", "cache:"], "Prefix")  # ok

    def test_prefix_rejected(self):
        from app.security.allowlist import check_prefix

        with pytest.raises(ValueError, match="does not match any allowed prefix"):
            check_prefix("secret:", ["fraud:"], "Prefix")

    def test_key_allowed_when_open(self):
        from app.security.allowlist import check_prefix

        with patch("app.security.allowlist.settings") as allowlist_settings:
            allowlist_settings.enforce_allowlists = False
            check_prefix("any:key", [], "Key")  # no error

    def test_key_allowed_when_prefix_matches(self):
        from app.security.allowlist import check_prefix

        check_prefix("fraud:score:tx123", ["fraud:"], "Key")  # ok

    def test_key_rejected(self):
        from app.security.allowlist import check_prefix

        with pytest.raises(ValueError, match="does not match"):
            check_prefix("admin:secret", ["fraud:"], "Key")


# ---- Kafka allowlists ----


class TestKafkaAllowlists:
    def test_topic_allowed_when_open(self):
        from app.domains.kafka import _check_topic_allowed

        with (
            patch("app.security.allowlist.settings") as allowlist_settings,
            patch("app.domains.kafka.settings") as mock_s,
        ):
            allowlist_settings.enforce_allowlists = False
            mock_s.kafka_allowed_topics = []
            _check_topic_allowed("anything")  # no error

    def test_topic_allowed_when_in_list(self):
        from app.domains.kafka import _check_topic_allowed

        with patch("app.domains.kafka.settings") as mock_s:
            mock_s.kafka_allowed_topics = ["fraud-decisions", "fraud-events"]
            _check_topic_allowed("fraud-decisions")  # ok

    def test_topic_rejected(self):
        from app.domains.kafka import _check_topic_allowed

        with patch("app.domains.kafka.settings") as mock_s:
            mock_s.kafka_allowed_topics = ["fraud-decisions"]
            with pytest.raises(ValueError, match="not in the allowed"):
                _check_topic_allowed("internal-secrets")

    def test_group_allowed_when_open(self):
        from app.domains.kafka import _check_group_allowed

        with (
            patch("app.security.allowlist.settings") as allowlist_settings,
            patch("app.domains.kafka.settings") as mock_s,
        ):
            allowlist_settings.enforce_allowlists = False
            mock_s.kafka_allowed_groups = []
            _check_group_allowed("anything")  # no error

    def test_group_allowed_when_in_list(self):
        from app.domains.kafka import _check_group_allowed

        with patch("app.domains.kafka.settings") as mock_s:
            mock_s.kafka_allowed_groups = ["fraud-engine"]
            _check_group_allowed("fraud-engine")  # ok

    def test_group_rejected(self):
        from app.domains.kafka import _check_group_allowed

        with patch("app.domains.kafka.settings") as mock_s:
            mock_s.kafka_allowed_groups = ["fraud-engine"]
            with pytest.raises(ValueError, match="not in the allowed"):
                _check_group_allowed("admin-group")


# ---- S3 / Storage allowlists ----


class TestStorageAllowlists:
    def test_bucket_allowed_when_open(self):
        from app.domains.storage import _check_bucket_allowed

        with (
            patch("app.security.allowlist.settings") as allowlist_settings,
            patch("app.domains.storage.settings") as mock_s,
        ):
            allowlist_settings.enforce_allowlists = False
            mock_s.s3_allowed_buckets = []
            _check_bucket_allowed("anything")  # no error

    def test_bucket_allowed_when_in_list(self):
        from app.domains.storage import _check_bucket_allowed

        with patch("app.domains.storage.settings") as mock_s:
            mock_s.s3_allowed_buckets = ["fraud-rulesets", "fraud-models"]
            _check_bucket_allowed("fraud-rulesets")  # ok

    def test_bucket_rejected(self):
        from app.domains.storage import _check_bucket_allowed

        with patch("app.domains.storage.settings") as mock_s:
            mock_s.s3_allowed_buckets = ["fraud-rulesets"]
            with pytest.raises(ValueError, match="not in the allowed"):
                _check_bucket_allowed("secret-bucket")

    def test_prefix_allowed_when_open(self):
        from app.domains.storage import _check_prefix_allowed

        with (
            patch("app.security.allowlist.settings") as allowlist_settings,
            patch("app.domains.storage.settings") as mock_s,
        ):
            allowlist_settings.enforce_allowlists = False
            mock_s.s3_allowed_prefixes = []
            _check_prefix_allowed("any-bucket", "any/prefix")  # no error

    def test_prefix_allowed_when_matches(self):
        from app.domains.storage import _check_prefix_allowed

        with patch("app.domains.storage.settings") as mock_s:
            mock_s.s3_allowed_prefixes = ["fraud-rulesets/v2/"]
            _check_prefix_allowed("fraud-rulesets", "v2/latest")  # ok

    def test_prefix_rejected(self):
        from app.domains.storage import _check_prefix_allowed

        with patch("app.domains.storage.settings") as mock_s:
            mock_s.s3_allowed_prefixes = ["fraud-rulesets/v2/"]
            with pytest.raises(ValueError, match="not in the allowed"):
                _check_prefix_allowed("fraud-rulesets", "v1/old")

    def test_object_allowed_when_bucket_and_prefix_match(self):
        from app.domains.storage import _check_object_allowed

        with patch("app.domains.storage.settings") as mock_s:
            mock_s.s3_allowed_buckets = ["fraud-rulesets"]
            mock_s.s3_allowed_prefixes = ["fraud-rulesets/v2/"]
            _check_object_allowed("fraud-rulesets", "v2/latest.json")

    def test_object_rejected_when_key_outside_allowed_prefix(self):
        from app.domains.storage import _check_object_allowed

        with patch("app.domains.storage.settings") as mock_s:
            mock_s.s3_allowed_buckets = ["fraud-rulesets"]
            mock_s.s3_allowed_prefixes = ["fraud-rulesets/v2/"]
            with pytest.raises(ValueError, match="allowed prefix"):
                _check_object_allowed("fraud-rulesets", "v1/old.json")
