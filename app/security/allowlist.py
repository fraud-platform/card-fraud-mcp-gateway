"""Reusable allowlist validation helpers for security boundaries."""

from __future__ import annotations

from app.config import settings


def _enforce_allowlist() -> bool:
    """Whether empty allowlists should be treated as blocked."""
    return bool(getattr(settings, "enforce_allowlists", False))


def check_exact(
    item: str,
    allowed: list[str] | None,
    item_type: str = "item",
    allowed_desc: str = "allowed list",
) -> None:
    """Reject items not in the allowlist (exact match)."""
    if _enforce_allowlist() and not allowed:
        raise ValueError(f"{item_type} '{item}' denied: allowlist not configured.")
    if not allowed:
        return
    if item not in allowed:
        raise ValueError(
            f"{item_type} '{item}' is not in the {allowed_desc}. Allowed: {sorted(allowed)}"
        )


def check_prefix(
    item: str,
    allowed: list[str] | None,
    item_type: str = "item",
) -> None:
    """Reject items whose prefix is not in the allowlist."""
    if _enforce_allowlist() and not allowed:
        raise ValueError(f"{item_type} '{item}' denied: allowlist not configured.")
    if not allowed:
        return
    if not any(item.startswith(a) for a in allowed):
        raise ValueError(
            f"{item_type} '{item}' does not match any allowed prefix. "
            f"Allowed prefixes: {sorted(allowed)}"
        )


def check_path_prefix(
    bucket: str,
    prefix: str,
    allowed: list[str] | None,
) -> None:
    """Reject paths not under any allowed bucket/prefix."""
    if _enforce_allowlist() and not allowed:
        raise ValueError("Path not allowed: allowlist not configured.")
    if not allowed:
        return
    path = f"{bucket}/{prefix}" if prefix else f"{bucket}/"
    if not any(path.startswith(a) for a in allowed):
        raise ValueError(
            f"Path '{path}' is not in the allowed prefix list. Allowed: {sorted(allowed)}"
        )


def filter_by_allowlist(
    items: list[str],
    allowed: list[str] | None,
) -> list[str]:
    """Filter items by allowlist (exact match)."""
    if _enforce_allowlist() and not allowed:
        return []
    if not allowed:
        return items
    return [item for item in items if item in allowed]
