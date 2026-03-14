"""Auth0 bootstrap for card-fraud-mcp-gateway (idempotent).

Provisions the Auth0 API resource server, all gateway scopes, and an M2M
application used by CI runners and agent clients. Safe to re-run.

Required Doppler secrets (AUTH0_MGMT_*):
    AUTH0_MGMT_DOMAIN          e.g. your-tenant.us.auth0.com
    AUTH0_MGMT_CLIENT_ID       Management M2M client ID (shared across platform)
    AUTH0_MGMT_CLIENT_SECRET   Management M2M client secret

Gateway config (GATEWAY_AUTH0_*):
    GATEWAY_AUTH0_AUDIENCE     e.g. https://card-fraud-mcp-gateway

Usage:
    uv run auth0-bootstrap --yes --verbose
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

import httpx

# ── Gateway resource definitions ──────────────────────────────────────────────

_API_NAME = os.environ.get("GATEWAY_AUTH0_API_NAME", "Card Fraud MCP Gateway API")
_M2M_APP_NAME = os.environ.get("GATEWAY_AUTH0_M2M_APP_NAME", "Card Fraud MCP Gateway M2M")

_SCOPES = [
    ("fraud.platform.read", "Read platform inventory, service health, and ownership"),
    ("fraud.db.read", "Read PostgreSQL schemas, tables, and run bounded SELECT queries"),
    ("fraud.redis.read", "Read Redis key prefixes and values"),
    ("fraud.kafka.read", "Read Kafka topics, consumer groups, and preview messages"),
    ("fraud.storage.read", "Read MinIO/S3 buckets and objects"),
    ("fraud.ops.investigation.read", "Read ops investigation context and case data"),
    ("fraud.ops.investigation.run", "Run ops investigation queries against fraud tables"),
]

_DOPPLER_PROJECT = "card-fraud-mcp-gateway"


# ── Doppler sync ──────────────────────────────────────────────────────────────


def sync_to_doppler(secrets: dict[str, str], config: str = "local") -> None:
    """Sync secrets to Doppler. Runs for both local and test configs."""
    pairs = [f"{k}={v}" for k, v in secrets.items()]
    for cfg in (config, "test") if config == "local" else (config,):
        result = subprocess.run(  # nosec
            ["doppler", "secrets", "set", "--project", _DOPPLER_PROJECT, "--config", cfg, *pairs],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"  ✓ Synced {list(secrets.keys())} to Doppler ({cfg})")
        else:
            print(f"  ⚠ Doppler sync failed ({cfg}): {result.stderr.strip()}", file=sys.stderr)


# ── Auth0 Management API client ───────────────────────────────────────────────


@dataclass
class Settings:
    mgmt_domain: str = field(default_factory=lambda: _require("AUTH0_MGMT_DOMAIN"))
    mgmt_client_id: str = field(default_factory=lambda: _require("AUTH0_MGMT_CLIENT_ID"))
    mgmt_client_secret: str = field(default_factory=lambda: _require("AUTH0_MGMT_CLIENT_SECRET"))
    audience: str = field(default_factory=lambda: _require("GATEWAY_AUTH0_AUDIENCE"))


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        print(f"Error: required env var {name!r} is not set.", file=sys.stderr)
        print("Run via: uv run auth0-bootstrap --yes --verbose", file=sys.stderr)
        sys.exit(1)
    return val


class Auth0Mgmt:
    """Minimal Auth0 Management API client with retry."""

    def __init__(self, domain: str, token: str) -> None:
        self._base = f"https://{domain}/api/v2"
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _req(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self._base}{path}"
        for attempt in range(3):
            try:
                resp = httpx.request(method, url, headers=self._headers, timeout=15, **kwargs)
                if resp.status_code == 429:
                    time.sleep(2**attempt)
                    continue
                return resp
            except httpx.RequestError as exc:
                if attempt == 2:
                    raise
                print(f"  ⚠ Request error (attempt {attempt + 1}): {exc}")
                time.sleep(1)
        raise RuntimeError(f"Failed after 3 attempts: {method} {path}")

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self._req("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self._req("POST", path, **kwargs)

    def patch(self, path: str, **kwargs) -> httpx.Response:
        return self._req("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs) -> httpx.Response:
        return self._req("DELETE", path, **kwargs)


def get_mgmt_token(s: Settings) -> str:
    resp = httpx.post(
        f"https://{s.mgmt_domain}/oauth/token",
        json={
            "grant_type": "client_credentials",
            "client_id": s.mgmt_client_id,
            "client_secret": s.mgmt_client_secret,
            "audience": f"https://{s.mgmt_domain}/api/v2/",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ── Resource helpers ──────────────────────────────────────────────────────────


def upsert_api(mgmt: Auth0Mgmt, audience: str, name: str, verbose: bool) -> str:
    """Create or update the MCP gateway API resource server. Returns resource server ID."""
    resp = mgmt.get("/resource-servers", params={"per_page": 100})
    resp.raise_for_status()
    existing = {rs["identifier"]: rs for rs in resp.json()}

    scope_payload = [{"value": v, "description": d} for v, d in _SCOPES]

    if audience in existing:
        rs_id = existing[audience]["id"]
        mgmt.patch(
            f"/resource-servers/{rs_id}",
            json={
                "name": name,
                "scopes": scope_payload,
                "enforce_policies": True,
                "token_dialect": "access_token_authz",
            },
        ).raise_for_status()
        print(f"  ✓ Updated API: {name} ({audience})")
        if verbose:
            print(f"    id={rs_id}, scopes={[s[0] for s in _SCOPES]}")
        return rs_id

    resp = mgmt.post(
        "/resource-servers",
        json={
            "name": name,
            "identifier": audience,
            "scopes": scope_payload,
            "signing_alg": "RS256",
            "enforce_policies": True,
            "token_dialect": "access_token_authz",
        },
    )
    resp.raise_for_status()
    rs_id = resp.json()["id"]
    print(f"  ✓ Created API: {name} ({audience})")
    if verbose:
        print(f"    id={rs_id}, scopes={[s[0] for s in _SCOPES]}")
    return rs_id


def upsert_m2m_client(
    mgmt: Auth0Mgmt, app_name: str, audience: str, verbose: bool
) -> tuple[str, str | None]:
    """Create or update the M2M application. Returns (client_id, client_secret_or_None)."""
    resp = mgmt.get("/clients", params={"per_page": 100, "app_type": "non_interactive"})
    resp.raise_for_status()
    existing = {c["name"]: c for c in resp.json()}

    if app_name in existing:
        client_id = existing[app_name]["client_id"]
        mgmt.patch(
            f"/clients/{client_id}",
            json={
                "description": "MCP gateway M2M — used by CI runners and agent clients",
            },
        ).raise_for_status()
        print(f"  ✓ Updated M2M app: {app_name} (client_id already in Doppler)")
        if verbose:
            print(f"    client_id={client_id}")
        return client_id, None  # Secret not retrievable after creation

    resp = mgmt.post(
        "/clients",
        json={
            "name": app_name,
            "app_type": "non_interactive",
            "description": "MCP gateway M2M — used by CI runners and agent clients",
            "grant_types": ["client_credentials"],
        },
    )
    resp.raise_for_status()
    data = resp.json()
    client_id = data["client_id"]
    client_secret = data["client_secret"]
    print(f"  ✓ Created M2M app: {app_name}")
    if verbose:
        print(f"    client_id={client_id}")
    return client_id, client_secret


def upsert_client_grant(mgmt: Auth0Mgmt, client_id: str, audience: str, verbose: bool) -> None:
    """Grant the M2M client access to the API with all scopes."""
    resp = mgmt.get("/client-grants", params={"client_id": client_id})
    resp.raise_for_status()
    grants = resp.json()
    all_scopes = [s[0] for s in _SCOPES]

    existing = next((g for g in grants if g["audience"] == audience), None)
    if existing:
        mgmt.patch(
            f"/client-grants/{existing['id']}", json={"scope": all_scopes}
        ).raise_for_status()
        print(f"  ✓ Updated client grant: {len(all_scopes)} scopes")
    else:
        mgmt.post(
            "/client-grants",
            json={
                "client_id": client_id,
                "audience": audience,
                "scope": all_scopes,
            },
        ).raise_for_status()
        print(f"  ✓ Created client grant: {len(all_scopes)} scopes")

    if verbose:
        print(f"    scopes={all_scopes}")


# ── Main ──────────────────────────────────────────────────────────────────────


def main(yes: bool = False, verbose: bool = False) -> None:
    s = Settings()

    print()
    print("Card Fraud MCP Gateway — Auth0 Bootstrap")
    print("=" * 60)
    print(f"  Tenant:   {s.mgmt_domain}")
    print(f"  Audience: {s.audience}")
    print(f"  API name: {_API_NAME}")
    print(f"  M2M app:  {_M2M_APP_NAME}")
    print(f"  Scopes:   {len(_SCOPES)}")
    print()

    if not yes:
        confirm = input("Proceed? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            sys.exit(0)

    print("Getting management token...")
    token = get_mgmt_token(s)
    mgmt = Auth0Mgmt(s.mgmt_domain, token)
    print("  ✓ Token acquired")
    print()

    print("[1/3] API Resource Server")
    upsert_api(mgmt, s.audience, _API_NAME, verbose)

    print()
    print("[2/3] M2M Application")
    client_id, client_secret = upsert_m2m_client(mgmt, _M2M_APP_NAME, s.audience, verbose)

    print()
    print("[3/3] Client Grant")
    upsert_client_grant(mgmt, client_id, s.audience, verbose)

    if client_secret:
        print()
        print("[+] Syncing credentials to Doppler")
        sync_to_doppler(
            {
                "GATEWAY_AUTH0_CLIENT_ID": client_id,
                "GATEWAY_AUTH0_CLIENT_SECRET": client_secret,
            }
        )

    print()
    print("Bootstrap complete.")
    print()
    print("Next steps:")
    print("  1. Enable RBAC on the API in Auth0 Dashboard:")
    print("     Applications → APIs → Card Fraud MCP Gateway API → Settings")
    print("     → Enable RBAC + Add Permissions in Access Token")
    print("  2. Verify: uv run auth0-verify --verbose")
    print("  3. Start gateway: uv run doppler-local")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bootstrap Auth0 resources for the MCP gateway.")
    parser.add_argument("--yes", "-y", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    main(yes=args.yes, verbose=args.verbose)
