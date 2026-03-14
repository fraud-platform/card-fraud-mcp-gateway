"""Auth0 verification for card-fraud-mcp-gateway (read-only).

Checks that all required Auth0 resources are present and correctly configured.
Exits 0 if everything is in order, 1 if any check fails.

Usage:
    uv run auth0-verify
    uv run auth0-verify --verbose
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import httpx

_EXPECTED_SCOPES = [
    "fraud.platform.read",
    "fraud.db.read",
    "fraud.redis.read",
    "fraud.kafka.read",
    "fraud.storage.read",
    "fraud.ops.investigation.read",
    "fraud.ops.investigation.run",
]

_M2M_APP_NAME = os.environ.get("GATEWAY_AUTH0_M2M_APP_NAME", "Card Fraud MCP Gateway M2M")
_API_NAME = os.environ.get("GATEWAY_AUTH0_API_NAME", "Card Fraud MCP Gateway API")


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        print(f"Error: required env var {name!r} is not set.", file=sys.stderr)
        sys.exit(1)
    return val


def _get_token(domain: str, client_id: str, client_secret: str) -> str:
    resp = httpx.post(
        f"https://{domain}/oauth/token",
        json={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "audience": f"https://{domain}/api/v2/",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _api_get(base: str, path: str, token: str, **kwargs) -> httpx.Response:
    for attempt in range(3):
        resp = httpx.get(
            f"{base}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
            **kwargs,
        )
        if resp.status_code == 429:
            time.sleep(2**attempt)
            continue
        return resp
    raise RuntimeError(f"Rate-limited after 3 attempts: {path}")


def _check(label: str, ok: bool, detail: str = "") -> bool:
    icon = "✓" if ok else "✗"
    line = f"  {icon} {label}"
    if detail and (not ok or os.environ.get("VERBOSE")):
        line += f"\n      {detail}"
    print(line)
    return ok


def main(verbose: bool = False) -> None:
    if verbose:
        os.environ["VERBOSE"] = "1"

    domain = _require("AUTH0_MGMT_DOMAIN")
    client_id = _require("AUTH0_MGMT_CLIENT_ID")
    client_secret = _require("AUTH0_MGMT_CLIENT_SECRET")
    audience = _require("GATEWAY_AUTH0_AUDIENCE")

    print()
    print("Card Fraud MCP Gateway — Auth0 Verification")
    print("=" * 60)
    print(f"  Tenant:   {domain}")
    print(f"  Audience: {audience}")
    print()

    print("Getting management token...")
    try:
        token = _get_token(domain, client_id, client_secret)
        print("  ✓ Token acquired")
    except Exception as exc:
        print(f"  ✗ Failed to get management token: {exc}", file=sys.stderr)
        sys.exit(1)

    base = f"https://{domain}/api/v2"
    failures = 0

    # ── Check API resource server ──────────────────────────────────────────
    print()
    print("[1] API Resource Server")
    resp = _api_get(base, "/resource-servers", token, params={"per_page": 100})
    resp.raise_for_status()
    apis = {rs["identifier"]: rs for rs in resp.json()}
    api_exists = audience in apis

    if not _check(f"API exists: {_API_NAME}", api_exists, f"expected identifier={audience}"):
        failures += 1
    else:
        rs = apis[audience]
        existing_scopes = {s["value"] for s in rs.get("scopes", [])}
        missing = [s for s in _EXPECTED_SCOPES if s not in existing_scopes]
        if not _check(
            f"All {len(_EXPECTED_SCOPES)} scopes present",
            not missing,
            f"missing: {missing}" if missing else "",
        ):
            failures += 1
        if verbose:
            for s in _EXPECTED_SCOPES:
                icon = "✓" if s in existing_scopes else "✗"
                print(f"      {icon} {s}")

    # ── Check M2M application ──────────────────────────────────────────────
    print()
    print("[2] M2M Application")
    resp = _api_get(
        base, "/clients", token, params={"per_page": 100, "app_type": "non_interactive"}
    )
    resp.raise_for_status()
    clients = {c["name"]: c for c in resp.json()}
    m2m_exists = _M2M_APP_NAME in clients

    if not _check(f"M2M app exists: {_M2M_APP_NAME}", m2m_exists):
        failures += 1
        m2m_client_id = None
    else:
        m2m_client_id = clients[_M2M_APP_NAME]["client_id"]
        if verbose:
            print(f"      client_id={m2m_client_id}")

    # ── Check client grant ─────────────────────────────────────────────────
    print()
    print("[3] Client Grant")
    if m2m_client_id:
        resp = _api_get(base, "/client-grants", token, params={"client_id": m2m_client_id})
        resp.raise_for_status()
        grants = resp.json()
        grant = next((g for g in grants if g["audience"] == audience), None)

        if not _check("Client grant exists", grant is not None):
            failures += 1
        else:
            granted_scopes = set(grant.get("scope", []))
            missing = [s for s in _EXPECTED_SCOPES if s not in granted_scopes]
            if not _check(
                f"Grant covers all {len(_EXPECTED_SCOPES)} scopes",
                not missing,
                f"missing: {missing}" if missing else "",
            ):
                failures += 1
    else:
        print("  ⚠ Skipped (M2M app not found)")
        failures += 1

    # ── Summary ───────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    if failures == 0:
        print("Result: ALL CHECKS PASSED")
        print()
        print("Run the gateway:  uv run doppler-local")
    else:
        print(f"Result: {failures} CHECK(S) FAILED")
        print()
        print("Run bootstrap to fix:  uv run auth0-bootstrap --yes --verbose")

    print()
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify Auth0 resources for the MCP gateway.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    main(verbose=args.verbose)
