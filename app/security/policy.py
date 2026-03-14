"""Scope-to-tool authorization and policy enforcement."""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

from app.security.auth import get_caller

_audit_logger = structlog.get_logger("audit")


def extract_domain(scope: str) -> str:
    """Extract domain from scope (e.g., 'fraud.redis.read' -> 'redis')."""
    return scope.split(".")[1]


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    domain: str
    scope: str
    read_only: bool = True
    approval_required: bool = False


_policies: dict[str, ToolPolicy] = {}


def register_policy(tool_name: str, policy: ToolPolicy) -> None:
    _policies[tool_name] = policy


def clear_policies() -> None:
    _policies.clear()


def get_policy(tool_name: str) -> ToolPolicy | None:
    return _policies.get(tool_name)


def get_all_policies() -> dict[str, ToolPolicy]:
    return dict(_policies)


def ensure_scope(scope: str, *, domain: str = "", name: str = "resource") -> None:
    """Enforce a scope check outside the tool decorator path."""
    _check_scope(scope, tool=name, domain=domain)


def _check_scope(scope: str, *, domain: str = "", tool: str = "resource") -> None:
    """Centralized scope enforcement used by wrappers and resource checks."""
    caller = get_caller()
    resolved_domain = domain or extract_domain(scope)
    if scope in caller.scopes:
        return

    _audit_logger.warning(
        "authorization_denied",
        tool=tool,
        domain=resolved_domain,
        required_scope=scope,
        caller_sub=caller.sub,
        caller_client=caller.client_id,
        caller_scopes=sorted(caller.scopes),
    )
    raise PermissionError(
        f"Scope '{scope}' required for '{tool}'. Caller '{caller.sub}' has: {sorted(caller.scopes)}"
    )


def require_scope(
    scope: str,
    *,
    domain: str = "",
    read_only: bool = True,
    tool_name: str = "",
) -> Callable:
    """Decorator that enforces scope authorization on a tool function."""

    def decorator(fn: Callable) -> Callable:
        resolved_name = tool_name or fn.__name__
        resolved_domain = domain or extract_domain(scope)
        register_policy(
            resolved_name,
            ToolPolicy(
                domain=resolved_domain,
                scope=scope,
                read_only=read_only,
            ),
        )

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            _check_scope(scope, domain=resolved_domain, tool=resolved_name)
            return await fn(*args, **kwargs)

        return wrapper

    return decorator
