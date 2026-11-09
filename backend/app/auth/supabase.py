"""Supabase access-token verification.

Supabase signs user JWTs either with a shared HS256 secret (legacy projects) or
with an asymmetric key published at the project's JWKS endpoint (current projects, ES256;
verified live). A project mid-migration can hold both kinds, so the key source is chosen
per token from its `alg` header, and each source only ever accepts its own algorithms
(no algorithm confusion). Every failure becomes
a bare `AuthError`: the reason is logged, never returned to the client.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings

log = logging.getLogger(__name__)

AUDIENCE = "authenticated"
_ASYMMETRIC_ALGORITHMS = ["RS256", "ES256"]
_CLOCK_SKEW_S = 5
_JWKS_TTL_S = 3600
_JWKS_TIMEOUT_S = 5


class AuthError(Exception):
    """The token could not be verified. Carries no detail by design."""


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user_id: str
    email: str | None = None


class Authenticator(Protocol):
    async def verify(self, token: str) -> AuthenticatedUser: ...


class SupabaseAuthenticator:
    def __init__(self, settings: Settings, *, jwks_client: jwt.PyJWKClient | None = None) -> None:
        base = settings.supabase_url.rstrip("/")
        secret = settings.supabase_jwt_secret
        self._secret = secret.get_secret_value() if secret else None
        # Without a configured project the issuer is unknown; only the shared secret then
        # vouches for the token.
        self._issuer = f"{base}/auth/v1" if base else None
        self._jwks = jwks_client
        if self._jwks is None and base:
            self._jwks = jwt.PyJWKClient(
                f"{base}/auth/v1/.well-known/jwks.json",
                lifespan=_JWKS_TTL_S,
                timeout=_JWKS_TIMEOUT_S,
            )

    async def verify(self, token: str) -> AuthenticatedUser:
        try:
            claims = await self._decode(token)
            return AuthenticatedUser(
                user_id=str(uuid.UUID(claims["sub"])),  # also keeps odd ids out of query filters
                email=claims.get("email"),
            )
        except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
            log.warning("token rejected", extra={"reason": type(exc).__name__})
            raise AuthError from None

    async def _decode(self, token: str) -> dict[str, Any]:
        key: Any
        if jwt.get_unverified_header(token).get("alg") == "HS256" and self._secret is not None:
            key = self._secret
            algorithms = ["HS256"]
        elif self._jwks is not None:
            # PyJWKClient does blocking HTTP on a cache miss.
            signing_key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
            key = signing_key.key
            algorithms = _ASYMMETRIC_ALGORITHMS
        else:
            log.error("auth is not configured: set SUPABASE_URL or SUPABASE_JWT_SECRET")
            raise AuthError
        required = ["exp", "sub", "aud"] + (["iss"] if self._issuer else [])
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=AUDIENCE,
            issuer=self._issuer,
            leeway=_CLOCK_SKEW_S,
            options={"require": required},
        )
        return claims


_bearer = HTTPBearer(auto_error=False)


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthenticatedUser:
    """FastAPI dependency for REST routes: `Depends(current_user)`."""
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    authenticator: Authenticator = request.app.state.authenticator
    try:
        return await authenticator.verify(credentials.credentials)
    except AuthError:
        raise unauthorized from None
