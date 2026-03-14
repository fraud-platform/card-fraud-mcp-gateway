"""Tests for secret redaction."""

from app.security.redaction import redact, redact_dict


class TestPatternRedaction:
    def test_password(self):
        result = redact("password=secret123")
        assert "secret123" not in result
        assert "REDACTED" in result

    def test_bearer_token(self):
        result = redact("Bearer eyJhbGciOiJSUzI1NiIs")
        assert "eyJhbGciOiJSUzI1NiIs" not in result
        assert "REDACTED" in result

    def test_card_number(self):
        result = redact("Card: 4111-1111-1111-1111")
        assert "4111-1111-1111-1111" not in result
        assert "REDACTED" in result

    def test_card_number_no_dashes(self):
        result = redact("Card: 4111111111111111")
        assert "4111111111111111" not in result

    def test_ssn(self):
        result = redact("SSN 123-45-6789")
        assert "123-45-6789" not in result
        assert "SSN" in result

    def test_connection_string(self):
        result = redact("postgresql://user:password123@localhost/db")
        assert "password123" not in result

    def test_aws_key(self):
        result = redact("key=AKIAIOSFODNN7EXAMPLE")
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_api_key(self):
        result = redact("api_key=sk-abc123def456")
        assert "sk-abc123def456" not in result


class TestPhoneRedaction:
    def test_international_phone_redacted(self):
        assert "REDACTED_PHONE" in redact("+1 415 555 1234")

    def test_area_code_hyphen_redacted(self):
        assert "REDACTED_PHONE" in redact("415-555-1234")

    def test_parenthesized_area_code_redacted(self):
        assert "REDACTED_PHONE" in redact("(415) 555-1234")

    def test_dot_separated_three_groups_redacted(self):
        assert "REDACTED_PHONE" in redact("415.555.1234")

    def test_local_hyphen_redacted(self):
        assert "REDACTED_PHONE" in redact("555-1234")

    def test_decimal_amount_not_redacted(self):
        # False positive: financial amounts must never be redacted
        assert "REDACTED" not in redact("9999.0000")
        assert "REDACTED" not in redact("12345.6789")
        assert "REDACTED" not in redact("750.50")

    def test_decimal_amount_in_json_not_redacted(self):
        payload = '{"transaction_amount": "9999.0000", "currency": "USD"}'
        assert "REDACTED" not in redact(payload)


class TestDictRedaction:
    def test_sensitive_key(self):
        data = {"username": "admin", "password": "secret", "data": "normal"}
        result = redact_dict(data)
        assert result["password"] == "***REDACTED***"
        assert result["data"] == "normal"
        assert result["username"] == "admin"

    def test_nested_dict(self):
        data = {"config": {"token": "abc123", "host": "localhost"}}
        result = redact_dict(data)
        assert result["config"]["token"] == "***REDACTED***"
        assert result["config"]["host"] == "localhost"

    def test_list_values(self):
        data = {"items": [{"password": "x"}, {"name": "safe"}]}
        result = redact_dict(data)
        assert result["items"][0]["password"] == "***REDACTED***"
        assert result["items"][1]["name"] == "safe"

    def test_string_value_redaction(self):
        data = {"log": "Bearer eyJtoken123"}
        result = redact_dict(data)
        assert "eyJtoken123" not in result["log"]

    def test_non_string_values_unchanged(self):
        data = {"count": 42, "enabled": True}
        result = redact_dict(data)
        assert result == data
