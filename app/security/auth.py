"""JWT authentication and caller identity context."""

from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from starlette.requests import Request

from app.config import settings
from app.constants import JWKS_CACHE_TTL_SECONDS

_auth_context: contextvars.ContextVar[CallerIdentity] = contextvars.ContextVar("auth_context")
_request_context: contextvars.ContextVar[dict[str, str | None] | None] = contextvars.ContextVar(
    "request_context",
    default=None,
)


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    sub: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    client_id: str = ""
    email: str = ""
    raw_claims: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_anonymous(self) -> bool:
        return self.sub == "anonymous"


_LOCAL_IDENTITY = CallerIdentity(
    sub="local-dev",
    scopes=frozenset(
        {
            "fraud.platform.read",
            "fraud.db.read",
            "fraud.redis.read",
            "fraud.kafka.read",
            "fraud.storage.read",
            "fraud.ops.investigation.read",
            "fraud.ops.investigation.run",
        }
    ),
    client_id="local-dev",
)

# ---- JWKS cache (with TTL) ----
_jwks_cache: dict[str, Any] | None = None
_jwks_cached_at: float = 0.0
_jwks_lock = asyncio.Lock()


async def _fetch_jwks(force_refresh: bool = False) -> dict[str, Any]:
    """Fetch JWKS with thread-safe caching to prevent thundering herd."""
    global _jwks_cache, _jwks_cached_at
    import time

    now = time.monotonic()
    # Fast path: return cached if still valid
    if (
        not force_refresh
        and _jwks_cache is not None
        and (now - _jwks_cached_at) < JWKS_CACHE_TTL_SECONDS
    ):
        return _jwks_cache

    # Slow path: acquire lock and check cache again (double-checked locking)
    async with _jwks_lock:
        # Re-check after acquiring lock in case another request refreshed it
        if (
            not force_refresh
            and _jwks_cache is not None
            and (now - _jwks_cached_at) < JWKS_CACHE_TTL_SECONDS
        ):
            return _jwks_cache

        url = f"https://{settings.auth0_domain}/.well-known/jwks.json"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            _jwks_cache = resp.json()
            _jwks_cached_at = now
            return _jwks_cache


async def validate_token(token: str) -> CallerIdentity:
    """Validate a JWT and return caller identity."""
    jwks = await _fetch_jwks()
    unverified_header = jwt.get_unverified_header(token)

    rsa_key: dict[str, Any] = {}
    for key in jwks.get("keys", []):
        if key["kid"] == unverified_header.get("kid"):
            rsa_key = {k: key[k] for k in ("kty", "kid", "use", "n", "e") if k in key}
            break

    if not rsa_key:
        # A key rotation may have happened since cache was built. Refresh once on miss
        # before rejecting to avoid an unnecessary auth outage.
        jwks = await _fetch_jwks(force_refresh=True)
        for key in jwks.get("keys", []):
            if key["kid"] == unverified_header.get("kid"):
                rsa_key = {k: key[k] for k in ("kty", "kid", "use", "n", "e") if k in key}
                break

    if not rsa_key:
        raise PermissionError("Unable to find appropriate signing key")

    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(rsa_key)
    claims = jwt.decode(
        token,
        public_key,
        algorithms=settings.auth0_algorithms,
        audience=settings.auth0_audience,
        issuer=f"https://{settings.auth0_domain}/",
    )

    scope_str = claims.get("scope", "")
    scopes = frozenset(s.strip() for s in scope_str.split() if s.strip())

    return CallerIdentity(
        sub=claims.get("sub", "unknown"),
        scopes=scopes,
        client_id=claims.get("azp", ""),
        email=claims.get("email", ""),
        raw_claims=claims,
    )


def set_request_context(
    request_id: str | None, source_ip: str | None
) -> contextvars.Token[dict[str, str | None]]:
    """Store request correlation metadata for audit logging."""
    return _request_context.set({"request_id": request_id, "source_ip": source_ip})


def reset_request_context(token: contextvars.Token[dict[str, str | None]]) -> None:
    """Restore the previous request correlation context."""
    _request_context.reset(token)


def get_request_context() -> dict[str, str | None]:
    """Get request correlation metadata for audit logging."""
    return _request_context.get() or {"request_id": None, "source_ip": None}


_BEARER_PREFIX = "Bearer "


async def authenticate_request(request: Request) -> CallerIdentity:
    """Authenticate an HTTP request.

    Local dev (APP_ENV=local + SECURITY_SKIP_JWT_VALIDATION=true): returns mock identity.
    All other environments: requires a valid Auth0 Bearer JWT.
    """
    if settings.skip_jwt_validation:
        return _LOCAL_IDENTITY

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith(_BEARER_PREFIX):
        raise PermissionError("Missing or invalid Authorization header")

    token = auth_header[len(_BEARER_PREFIX) :]
    return await validate_token(token)


def set_caller(identity: CallerIdentity) -> contextvars.Token[CallerIdentity]:
    return _auth_context.set(identity)


def get_caller() -> CallerIdentity:
    try:
        return _auth_context.get()
    except LookupError:
        if settings.skip_jwt_validation:
            return _LOCAL_IDENTITY
        raise PermissionError("No authenticated caller in context") from None
