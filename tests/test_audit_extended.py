"""Tests for audit module."""

from __future__ import annotations

from app import audit


class TestResultMetadata:
    def test_result_metadata_none(self):
        result = audit._result_metadata(None)
        assert result == {"result_type": "none"}

    def test_result_metadata_str(self):
        result = audit._result_metadata("hello")
        assert result == {"result_type": "str", "result_length": 5}

    def test_result_metadata_list(self):
        result = audit._result_metadata([1, 2, 3])
        assert result == {"result_type": "list", "result_count": 3}

    def test_result_metadata_dict(self):
        result = audit._result_metadata({"a": 1, "b": 2})
        assert result == {"result_type": "dict", "result_keys": 2}

    def test_result_metadata_other(self):
        result = audit._result_metadata(42)
        assert result == {"result_type": "int"}


class TestInitOtel:
    def test_init_otel_no_endpoint(self):
        audit.init_otel("", "test-service")


class TestAuditSanitization:
    def test_safe_args_redacts_sensitive_values(self):
        safe = audit._safe_args(
            {
                "token": "abc123",
                "query": "SELECT * FROM users WHERE password=secret123",
                "nested": {"authorization": "Bearer xyz", "note": "email=user@example.com"},
            }
        )
        assert safe["token"] == "***REDACTED***"
        assert "secret123" not in safe["query"]
        assert safe["nested"]["authorization"] == "***REDACTED***"
        assert "user@example.com" not in safe["nested"]["note"]

    def test_sentry_before_send_redacts_headers_and_user(self):
        event = {
            "request": {
                "headers": {"Authorization": "Bearer secret", "x-request-id": "abc"},
                "data": "password=secret123",
            },
            "user": {"id": "caller-1", "email": "user@example.com", "ip_address": "1.2.3.4"},
        }
        redacted = audit._sentry_before_send(event, {})
        assert redacted is not None
        assert redacted["request"]["headers"]["Authorization"] == "***REDACTED***"
        assert "secret123" not in redacted["request"]["data"]
        assert redacted["user"]["id"] == "caller-1"
        assert redacted["user"]["email"] == "***REDACTED***"
        assert redacted["user"]["ip_address"] == "***REDACTED***"
