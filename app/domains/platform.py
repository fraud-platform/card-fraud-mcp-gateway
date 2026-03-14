"""Platform control-plane tools — service inventory, status, health, ownership."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

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
    candidates: list[Path] = []
    if settings.services_file:
        candidates.append(Path(settings.services_file))

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


def _load_inventory_from_file(path: Path) -> dict[str, dict] | None:
    """Load inventory shape from a platform services.yaml file."""
    import yaml

    with path.open() as f:
        data = yaml.safe_load(f)
    if isinstance(data, dict) and "services" in data and isinstance(data["services"], list):
        return {
            svc["name"]: {k: v for k, v in svc.items() if k != "name"}
            for svc in data["services"]
            if isinstance(svc, dict) and "name" in svc
        }
    return None


def _load_inventory_and_source() -> tuple[dict[str, dict], str]:
    """Load service inventory and indicate which source was used."""
    for path in _inventory_candidates():
        if not path.exists():
            continue
        try:
            inventory = _load_inventory_from_file(path)
        except Exception:
            continue
        if inventory:
            return inventory, str(path)

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


def register(mcp: FastMCP) -> None:
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
