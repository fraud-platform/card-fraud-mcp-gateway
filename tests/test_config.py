"""Tests for Settings configuration."""

from app.config import Settings


def test_defaults():
    s = Settings()
    assert s.port == 8000
    assert s.skip_jwt_validation is True  # Set to true in conftest (APP_ENV=local)
    assert s.app_env.value == "local"
    assert s.pg_max_rows == 500
    assert s.rate_limit_rpm == 120


def test_kafka_broker_list_parsing():
    s = Settings(kafka_brokers="broker1:9092,broker2:9092")
    assert s.kafka_broker_list == ["broker1:9092", "broker2:9092"]


def test_kafka_broker_list_empty():
    s = Settings(kafka_brokers="")
    assert s.kafka_broker_list == []


def test_kafka_broker_list_whitespace():
    s = Settings(kafka_brokers=" broker1:9092 , broker2:9092 , ")
    assert s.kafka_broker_list == ["broker1:9092", "broker2:9092"]


def test_allowlist_defaults_are_empty():
    s = Settings()
    assert s.pg_allowed_schemas == []
    assert s.pg_allowed_tables == []
    assert s.redis_allowed_prefixes == []
    assert s.kafka_allowed_topics == []
    assert s.kafka_allowed_groups == []
    assert s.s3_allowed_buckets == []
    assert s.s3_allowed_prefixes == []


def test_max_request_body_bytes_default():
    s = Settings()
    assert s.max_request_body_bytes == 1_048_576


def test_allowlist_values_set():
    s = Settings(
        pg_allowed_schemas=["public", "fraud"],
        kafka_allowed_topics=["fraud-decisions"],
    )
    assert s.pg_allowed_schemas == ["public", "fraud"]
    assert s.kafka_allowed_topics == ["fraud-decisions"]


def test_allowlist_values_parse_from_csv_env(monkeypatch):
    monkeypatch.setenv("GATEWAY_PG_ALLOWED_SCHEMAS", "public,fraud")
    monkeypatch.setenv("GATEWAY_REDIS_ALLOWED_PREFIXES", "fraud:,cache:")
    monkeypatch.setenv("GATEWAY_S3_ALLOWED_BUCKETS", "fraud-rulesets,fraud-models")

    s = Settings()

    assert s.pg_allowed_schemas == ["public", "fraud"]
    assert s.redis_allowed_prefixes == ["fraud:", "cache:"]
    assert s.s3_allowed_buckets == ["fraud-rulesets", "fraud-models"]
