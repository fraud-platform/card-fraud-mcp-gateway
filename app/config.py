"""Gateway configuration from environment variables."""

from __future__ import annotations

import json
from enum import StrEnum

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings


class AppEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    PROD = "prod"


class Settings(BaseSettings):
    model_config = {
        "env_prefix": "GATEWAY_",
        "case_sensitive": False,
        # pydantic-settings key: skip json.loads on list fields so our
        # _parse_list_env validator can handle both CSV and JSON array values.
        "enable_decoding": False,
        "env_file": None,
    }

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Runtime environment — platform convention (no GATEWAY_ prefix)
    app_env: AppEnvironment = Field(default=AppEnvironment.LOCAL, validation_alias="APP_ENV")

    # Auth — platform convention:
    #   Production:  APP_ENV=prod, all env vars set, JWT validated on every request
    #   Local dev:   APP_ENV=local + SECURITY_SKIP_JWT_VALIDATION=true → mock identity injected
    #   CI/agents:   APP_ENV=test, Auth0 M2M token in Authorization header
    skip_jwt_validation: bool = Field(
        default=False, validation_alias="SECURITY_SKIP_JWT_VALIDATION"
    )
    auth0_domain: str = ""
    auth0_audience: str = ""
    auth0_algorithms: list[str] = Field(default=["RS256"])

    # PostgreSQL
    pg_dsn: str = ""
    pg_pool_min: int = 2
    pg_pool_max: int = 10
    pg_statement_timeout_ms: int = 5000
    pg_max_rows: int = 500

    # Redis
    redis_url: str = ""
    redis_max_keys: int = 100
    redis_max_value_bytes: int = 10_000

    # Kafka / Redpanda
    kafka_brokers: str = ""
    kafka_max_messages: int = 10
    kafka_max_payload_bytes: int = 10_000

    # MinIO / S3
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    s3_max_object_bytes: int = 1_000_000

    # Platform
    platform_api_url: str = ""
    platform_api_token: str = ""
    services_file: str = ""  # Path to services.yaml (optional, overrides static inventory)

    # Allowlists — config-driven access control for backend domains
    pg_allowed_schemas: list[str] = Field(default=[])
    pg_allowed_tables: list[str] = Field(default=[])  # format: "schema.table" or just "table"
    redis_allowed_prefixes: list[str] = Field(default=[])
    kafka_allowed_topics: list[str] = Field(default=[])
    kafka_allowed_groups: list[str] = Field(default=[])
    s3_allowed_buckets: list[str] = Field(default=[])
    s3_allowed_prefixes: list[str] = Field(default=[])  # format: "bucket/prefix"

    # Ops domain — table names used by investigation tools
    # Note: Table names only (without schema), schema is determined at query time
    ops_transactions_table: str = "transactions"
    ops_cases_table: str = "transaction_cases"
    ops_decisions_table: str = "transaction_reviews"

    # Ops domain — columns to fetch (explicitly avoids SELECT * on wide tables)
    # Defaults match the fraud_gov schema; override via GATEWAY_OPS_*_COLUMNS env vars
    ops_transactions_columns: list[str] = Field(
        default_factory=lambda: [
            "id",
            "transaction_id",
            "card_id",
            "card_last4",
            "transaction_amount",
            "transaction_currency",
            "merchant_id",
            "merchant_category_code",
            "decision",
            "decision_score",
            "risk_level",
            "transaction_timestamp",
            "created_at",
        ]
    )
    ops_cases_columns: list[str] = Field(
        default_factory=lambda: [
            "id",
            "case_number",
            "case_type",
            "case_status",
            "total_transaction_count",
            "total_transaction_amount",
            "risk_level",
            "created_at",
            "updated_at",
        ]
    )
    ops_decisions_columns: list[str] = Field(
        default_factory=lambda: [
            "id",
            "transaction_id",
            "status",
            "priority",
            "case_id",
            "resolution_code",
            "created_at",
            "updated_at",
        ]
    )

    # Request size limits
    max_request_body_bytes: int = 1_048_576  # 1 MB

    # Observability
    otel_endpoint: str = ""
    otel_service_name: str = "card-fraud-mcp-gateway"

    # Sentry error tracking
    sentry_dsn: str = ""
    sentry_environment: str = ""
    sentry_traces_sample_rate: float = 0.1  # 10% of transactions
    sentry_profiles_sample_rate: float = 0.0  # Profiling disabled by default
    sentry_send_default_pii: bool = False

    # Rate limiting
    rate_limit_rpm: int = 120
    enforce_allowlists: bool = True

    # Metrics
    metrics_enabled: bool = True

    # CORS
    cors_origins: list[str] = Field(default=[])

    @field_validator(
        "auth0_algorithms",
        "pg_allowed_schemas",
        "pg_allowed_tables",
        "redis_allowed_prefixes",
        "kafka_allowed_topics",
        "kafka_allowed_groups",
        "s3_allowed_buckets",
        "s3_allowed_prefixes",
        "cors_origins",
        mode="before",
    )
    @classmethod
    def _parse_list_env(cls, value: object) -> object:
        """Support JSON arrays and comma-separated env vars for list settings."""
        if not isinstance(value, str):
            return value

        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            return json.loads(raw)
        return [item.strip() for item in raw.split(",") if item.strip()]

    @model_validator(mode="after")
    def _validate_jwt_bypass(self) -> Settings:
        if self.skip_jwt_validation and self.app_env != AppEnvironment.LOCAL:
            raise ValueError(
                "SECURITY_SKIP_JWT_VALIDATION=true is only allowed when APP_ENV=local. "
                f"Current APP_ENV={self.app_env}"
            )
        return self

    @property
    def kafka_broker_list(self) -> list[str]:
        return [b.strip() for b in self.kafka_brokers.split(",") if b.strip()]


settings = Settings()
