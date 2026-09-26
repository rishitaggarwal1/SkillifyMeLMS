"""Test-only token issuing: an RSA key pair standing in for Keycloak's signing key, a JWKS source
serving its public half, and a factory for access tokens shaped exactly like Keycloak's."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.core.auth.jwt import JwksCache, JwtValidator
from app.core.config import Settings


@dataclass
class SigningKey:
    kid: str
    private_key: rsa.RSAPrivateKey = field(
        default_factory=lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )

    @property
    def private_pem(self) -> bytes:
        return self.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    @property
    def public_pem(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    def jwk(self) -> dict[str, Any]:
        data: dict[str, Any] = RSAAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        return {**data, "kid": self.kid, "use": "sig", "alg": "RS256"}


class StaticJwksSource:
    """Serves whatever keys the test puts in `keys`; counts fetches."""

    def __init__(self, *keys: SigningKey) -> None:
        self.keys = list(keys)
        self.fetches = 0

    async def fetch(self) -> dict[str, Any]:
        self.fetches += 1
        return {"keys": [k.jwk() for k in self.keys]}


@dataclass
class TokenFactory:
    key: SigningKey
    issuer: str
    audience: str = "skillifyme-api"
    client_id: str = "skillifyme-web"

    def claims(self, **overrides: Any) -> dict[str, Any]:
        now = int(time.time())
        base: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.audience,
            "azp": self.client_id,
            "typ": "Bearer",
            "sub": "test-sub",
            "email": "user@example.test",
            "email_verified": True,
            "name": "Test User",
            "iat": now,
            "exp": now + 300,
            "realm_access": {"roles": []},
        }
        base.update(overrides)
        return {k: v for k, v in base.items() if v is not None}

    def token(
        self,
        *,
        key: SigningKey | None = None,
        algorithm: str = "RS256",
        headers: dict[str, Any] | None = None,
        **claim_overrides: Any,
    ) -> str:
        signer = key or self.key
        return jwt.encode(
            self.claims(**claim_overrides),
            signer.private_pem,
            algorithm=algorithm,
            headers={"kid": signer.kid, **(headers or {})},
        )


def make_validator(
    settings: Settings,
    source: StaticJwksSource,
    *,
    clock: Callable[[], float] = time.monotonic,
    cooldown: float = 0.0,
) -> JwtValidator:
    jwks = JwksCache(source, ttl_seconds=600, refetch_cooldown_seconds=cooldown, clock=clock)
    return JwtValidator(
        jwks,
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        allowed_clients=frozenset(settings.oidc_allowed_clients),
        leeway_seconds=0,
    )
