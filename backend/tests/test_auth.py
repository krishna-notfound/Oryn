import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth.supabase import (
    AuthenticatedUser,
    AuthError,
    SupabaseAuthenticator,
    current_user,
)
from app.config import Settings

SECRET = "x" * 48
SUPABASE_URL = "https://proj.supabase.co"
USER_ID = "7b0c3b5e-6a52-4f0e-9a63-0d8f8f1b2c11"


def _claims(**overrides: Any) -> dict[str, Any]:
    claims = {
        "sub": USER_ID,
        "email": "a@example.com",
        "aud": "authenticated",
        "iss": f"{SUPABASE_URL}/auth/v1",
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    return claims


def _hs256_authenticator() -> SupabaseAuthenticator:
    return SupabaseAuthenticator(
        Settings(supabase_url=SUPABASE_URL, supabase_jwt_secret=SecretStr(SECRET))
    )


async def test_valid_hs256_token_yields_user() -> None:
    token = jwt.encode(_claims(), SECRET, algorithm="HS256")
    user = await _hs256_authenticator().verify(token)
    assert user == AuthenticatedUser(user_id=USER_ID, email="a@example.com")


@pytest.mark.parametrize(
    "token",
    [
        jwt.encode(_claims(exp=int(time.time()) - 3600), SECRET, algorithm="HS256"),
        jwt.encode(_claims(aud="anon"), SECRET, algorithm="HS256"),
        jwt.encode(_claims(iss="https://evil.example/auth/v1"), SECRET, algorithm="HS256"),
        jwt.encode(_claims(sub="not-a-uuid"), SECRET, algorithm="HS256"),
        jwt.encode(_claims(), "y" * 48, algorithm="HS256"),
        jwt.encode(_claims(), None, algorithm="none"),
        "garbage",
        "",
    ],
    ids=[
        "expired",
        "wrong-audience",
        "wrong-issuer",
        "bad-sub",
        "bad-signature",
        "alg-none",
        "garbage",
        "empty",
    ],
)
async def test_invalid_tokens_raise_auth_error(token: str) -> None:
    with pytest.raises(AuthError):
        await _hs256_authenticator().verify(token)


async def test_unconfigured_authenticator_rejects_everything() -> None:
    token = jwt.encode(_claims(), SECRET, algorithm="HS256")
    with pytest.raises(AuthError):
        await SupabaseAuthenticator(Settings()).verify(token)


class _StubSigningKey:
    def __init__(self, key: Any) -> None:
        self.key = key


class _StubJwksClient:
    def __init__(self, key: Any) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str) -> _StubSigningKey:
        return _StubSigningKey(self._key)


def _rsa_authenticator(public_key: Any) -> SupabaseAuthenticator:
    return SupabaseAuthenticator(
        Settings(supabase_url=SUPABASE_URL),
        jwks_client=_StubJwksClient(public_key),  # type: ignore[arg-type]
    )


async def test_jwks_path_verifies_rs256_token() -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(_claims(), private, algorithm="RS256", headers={"kid": "k1"})
    user = await _rsa_authenticator(private.public_key()).verify(token)
    assert user.user_id == USER_ID


async def test_jwks_path_rejects_token_signed_by_another_key() -> None:
    signer = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(_claims(), signer, algorithm="RS256")
    with pytest.raises(AuthError):
        await _rsa_authenticator(other.public_key()).verify(token)


async def test_jwks_path_rejects_hs256_token_downgrade() -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(_claims(), SECRET, algorithm="HS256")
    with pytest.raises(AuthError):
        await _rsa_authenticator(private.public_key()).verify(token)


def _rest_app() -> FastAPI:
    app = FastAPI()
    app.state.authenticator = _hs256_authenticator()

    @app.get("/me")
    async def me(user: AuthenticatedUser = Depends(current_user)) -> dict[str, str]:  # noqa: B008
        return {"user_id": user.user_id}

    return app


def test_current_user_dependency_accepts_bearer_token() -> None:
    token = jwt.encode(_claims(), SECRET, algorithm="HS256")
    response = TestClient(_rest_app()).get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200 and response.json() == {"user_id": USER_ID}


def test_current_user_dependency_rejects_missing_and_bad_tokens() -> None:
    client = TestClient(_rest_app())
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer nope"}).status_code == 401
