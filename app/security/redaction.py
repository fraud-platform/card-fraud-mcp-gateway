"""Redaction of secrets and sensitive patterns from tool outputs."""

from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS = re.compile(
    r"(?ix)"
    r"(?P<kv>(?P<kv_key>password|passwd|pwd|secret|token|api[_-]?key|authorization)"
    r"\s*[=:]\s*)(?P<kv_val>\S+)"
    r"|(?P<bearer>bearer\s+[A-Za-z0-9\-._~+/]+=*)"
    r"|(?P<email>[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
    r"|(?P<phone>"
    r"\b(?:"
    r"(?:\+?\d{1,3}[-.\s])?(?:\(\d{2,4}\)|\d{2,4})[-.\s]\d{3,4}[-.\s]\d{4}"
    r"|\(\d{2,4}\)[-.\s]?\d{3,4}[-.\s]?\d{4}"
    r"|\d{3,4}[-\s]\d{4}"
    r")\b"
    r")"
    r"|(?P<pem>-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----)"
    r"|(?P<cc>"
    r"\b(?:"
    r"4\d{3}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}"
    r"|5[1-5]\d{2}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}"
    r"|3[47]\d{13}"
    r"|6(?:011|5[0-9]{2})\d{12}"
    r")\b"
    r")"
    r"|(?P<cvv>\b(?:cvv|cvv2|cvc)\s*[=:]\s*\d{3,4}\b)"
    r"|(?P<ssn>\b\d{3}-\d{2}-\d{4}\b)"
    r"|(?P<aws>\bAKIA[0-9A-Z]{16}\b)"
    r"|(?P<conn>://[^:]+:[^@]+@)",
    re.DOTALL,
)

_SENSITIVE_KEYS = frozenset(
    {"password", "secret", "token", "api_key", "apikey", "credential", "auth"}
)


def redact(text: str) -> str:
    """Remove sensitive patterns from text output."""

    def _repl(match: re.Match[str]) -> str:
        if match.group("kv"):
            return f"{match.group('kv_key')}=***REDACTED***"
        if match.group("bearer"):
            return "Bearer ***REDACTED***"
        if match.group("kv_val"):
            return "***REDACTED***"
        if match.group("email"):
            return "***REDACTED_EMAIL***"
        if match.group("phone"):
            return "***REDACTED_PHONE***"
        if match.group("pem"):
            return "***REDACTED_PEM***"
        if match.group("cc"):
            return "***REDACTED_CARD***"
        if match.group("cvv"):
            return "cvv=***REDACTED_CVV***"
        if match.group("ssn"):
            return "***REDACTED_SSN***"
        if match.group("aws"):
            return "***REDACTED_AWS_KEY***"
        if match.group("conn"):
            return "://***:***@"
        return "***REDACTED***"

    return _SECRET_PATTERNS.sub(_repl, text)


def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive values in a dictionary."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        if any(s in key.lower() for s in _SENSITIVE_KEYS):
            result[key] = "***REDACTED***"
        elif isinstance(value, str):
            result[key] = redact(value)
        elif isinstance(value, dict):
            result[key] = redact_dict(value)
        elif isinstance(value, list):
            result[key] = [
                redact_dict(v) if isinstance(v, dict) else redact(v) if isinstance(v, str) else v
                for v in value
            ]
        else:
            result[key] = value
    return result
