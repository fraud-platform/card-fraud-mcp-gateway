"""Platform control-plane tools — service inventory, status, health, ownership."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer

from app.audit import audit_tool, tool_result
from app.config import AppEnvironment, settings
from app.security.policy import require_scope

# Built-in service inventory — used when services_file is not configured.
_DEFAULT_INVENTORY: dict[str, dict] = {
    "card-fraud-platform": {"type": "infrastructure", "owner": "platform-team", "port": 8080},
    "card-fraud-api": {"type": "api", "owner": "api-team", "port": 8081},
    "card-fraud-engine": {"type": "service", "owner": "engine-team", "port": 8082},
    "card-fraud-dashboard": {"type": "frontend", "owner": "frontend-team", "port": 3000},
    "card-fraud-ops-analyst-agent": {"type": "agent", "owner": "ops-team", "port": 8083},
    "card-fraud-mcp-gateway": {"type": "gateway", "owner": "platform-team", "port": 8000},
}


def _inventory_candidates() -> list[Path]:
    """Candidate control-plane inventory files, in precedence order."""
    if settings.services_file:
        # An explicit override is authoritative, including when it is missing.
        return [Path(settings.services_file)]

    candidates: list[Path] = []
    repo_root = Path(__file__).resolve().parents[2]
    candidates.append(repo_root.parent / "card-fraud-platform" / "control-plane" / "services.yaml")
    candidates.append(repo_root / "control-plane" / "services.yaml")

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _load_inventory_from_file(path: Path) -> dict[str, dict]:
    """Load and normalize the canonical mapping-shaped services contract.

    A configured/present contract is authoritative: malformed content raises instead
    of silently reverting to the stale built-in inventory.
    """
    import yaml

    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        raise ValueError("Platform service contract must define services as a mapping")

    inventory: dict[str, dict] = {}
    for name, raw in data["services"].items():
        if not isinstance(name, str) or not name.strip() or not isinstance(raw, dict):
            raise ValueError("Platform service names and metadata must be mappings")
        required = ("owner", "runtime", "port", "container", "health", "auth_model")
        missing = [key for key in required if key not in raw]
        if missing:
            raise ValueError(f"Service {name!r} missing required fields: {', '.join(missing)}")
        if (
            not isinstance(raw["owner"], str)
            or not raw["owner"].strip()
            or not isinstance(raw["runtime"], str)
            or not isinstance(raw["container"], str)
        ):
            raise ValueError(f"Service {name!r} has invalid owner/runtime/container")
        if isinstance(raw["port"], bool) or not isinstance(raw["port"], int) or raw["port"] <= 0:
            raise ValueError(f"Service {name!r} has invalid port")
        health = raw["health"]
        if not isinstance(health, dict) or not isinstance(health.get("path"), str):
            raise ValueError(f"Service {name!r} has invalid health metadata")

        # Keep the contract's metadata intact while exposing the stable type field used
        # by gateway tools.
        info = dict(raw)
        info.setdefault("type", raw["runtime"])
        inventory[name] = info
    if not inventory:
        raise ValueError("Platform service contract contains no services")
    return inventory


def _load_inventory_and_source() -> tuple[dict[str, dict], str]:
    """Load service inventory and indicate which source was used."""
    for path in _inventory_candidates():
        if not path.exists():
            continue
        try:
            return _load_inventory_from_file(path), str(path)
        except (OSError, ValueError, TypeError) as exc:
            # A file that exists is an explicit contract and must fail closed.
            raise RuntimeError(f"Invalid platform service contract: {path}") from exc

    if settings.app_env == AppEnvironment.PROD:
        raise RuntimeError(
            "Platform service contract not found. Set GATEWAY_SERVICES_FILE or mount "
            "card-fraud-platform/control-plane/services.yaml in production."
        )
    return _DEFAULT_INVENTORY, "static-default"


def _load_inventory() -> dict[str, dict]:
    """Load service inventory from services_file (YAML) or use built-in inventory."""
    inventory, _source = _load_inventory_and_source()
    return inventory


_SERVICE_INVENTORY, _INVENTORY_SOURCE = _load_inventory_and_source()


def _fetch_service_endpoint(service_name: str, suffix: str) -> str:
    """Resolve a URL path for a platform service."""
    return f"/api/v1/services/{quote(service_name, safe='')}/{suffix}"


def register(mcp: MCPServer) -> None:
    @mcp.tool(
        name="platform.inventory",
        description=(
            "List all services in the card-fraud platform with type, owner, and port. "
            "Domain: platform | Read-only | Scope: fraud.platform.read"
        ),
    )
    @require_scope("fraud.platform.read", domain="platform", tool_name="platform.inventory")
    @audit_tool("platform")
    async def platform_inventory() -> str:
        services = [{"name": name, **info} for name, info in _SERVICE_INVENTORY.items()]
        return tool_result(
            {"services": services, "count": len(services), "inventory_source": _INVENTORY_SOURCE}
        )

    @mcp.tool(
        name="platform.service_status",
        description=(
            "Get the operational status of a specific platform service. "
            "Args: service_name. Domain: platform | Read-only | Scope: fraud.platform.read"
        ),
    )
    @require_scope("fraud.platform.read", domain="platform", tool_name="platform.service_status")
    @audit_tool("platform")
    async def platform_service_status(service_name: str) -> str:
        if service_name not in _SERVICE_INVENTORY:
            return tool_result(
                {"error": f"Unknown service: {service_name}", "known": list(_SERVICE_INVENTORY)}
            )
        info = _SERVICE_INVENTORY[service_name]
        try:
            from app.backends import get_platform_client

            client = get_platform_client()
            resp = await client.get(_fetch_service_endpoint(service_name, "status"))
            if resp.status_code == 200:
                status_data = resp.json()
            else:
                status_data = {"status": "unreachable", "http": resp.status_code}
        except Exception:
            status_data = {
                "status": "unknown",
                "reason": "Platform API not configured or unreachable",
            }
        return tool_result({"service": service_name, **info, **status_data})

    @mcp.tool(
        name="platform.service_health",
        description=(
            "Detailed health check of a platform service. "
            "Args: service_name. Domain: platform | Read-only | Scope: fraud.platform.read"
        ),
    )
    @require_scope("fraud.platform.read", domain="platform", tool_name="platform.service_health")
    @audit_tool("platform")
    async def platform_service_health(service_name: str) -> str:
        if service_name not in _SERVICE_INVENTORY:
            return tool_result({"error": f"Unknown service: {service_name}"})
        info = _SERVICE_INVENTORY[service_name]
        try:
            from app.backends import get_platform_client

            client = get_platform_client()
            resp = await client.get(_fetch_service_endpoint(service_name, "health"))
            if resp.status_code == 200:
                health = resp.json()
            else:
                health = {"healthy": False, "http": resp.status_code}
        except Exception:
            health = {
                "healthy": False,
                "reason": "Platform API not configured or unreachable",
            }
        return tool_result({"service": service_name, **info, "health": health})

    @mcp.tool(
        name="platform.ownership_summary",
        description=(
            "Ownership and responsibility summary across all platform services. "
            "Domain: platform | Read-only | Scope: fraud.platform.read"
        ),
    )
    @require_scope("fraud.platform.read", domain="platform", tool_name="platform.ownership_summary")
    @audit_tool("platform")
    async def platform_ownership_summary() -> str:
        by_owner: dict[str, list[str]] = {}
        by_type: dict[str, list[str]] = {}
        for name, info in _SERVICE_INVENTORY.items():
            by_owner.setdefault(info["owner"], []).append(name)
            by_type.setdefault(info["type"], []).append(name)
        return tool_result(
            {
                "by_owner": by_owner,
                "by_type": by_type,
                "total_services": len(_SERVICE_INVENTORY),
                "inventory_source": _INVENTORY_SOURCE,
            }
        )
