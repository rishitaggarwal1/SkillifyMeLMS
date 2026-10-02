"""Access-token validation against Keycloak's signing keys (JWKS).

- Only asymmetric algorithms are accepted, and the algorithm must match the key's type, so
  `alg=none` and HS256-with-the-public-key ("algorithm confusion") are both rejected.
- `iss` must equal the configured issuer exactly (from KEYCLOAK_PUBLIC_URL; Keycloak pins it with
  KC_HOSTNAME), `aud` must include the API audience, `azp` must be an allowed client, and `typ` must
  be "Bearer" so ID/refresh tokens can't be replayed as access tokens.
- Keys are cached in-process with a TTL. An unknown `kid` triggers one refetch (key rotation), at
  most once per cooldown window, so a flood of junk tokens can't hammer Keycloak.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import jwt
from jwt import PyJWK, PyJWKSet

from app.core.errors import AuthenticationError
from app.core.logging import get_logger

logger = get_logger(__name__)

ALLOWED_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256"})


class InvalidTokenError(AuthenticationError):
    code = "invalid_token"
    message = "The access token is invalid."


class ExpiredTokenError(AuthenticationError):
    code = "token_expired"
    message = "The access token has expired."


class JwksSource(Protocol):
    async def fetch(self) -> dict[str, Any]: ...


class HttpJwksSource:
    def __init__(self, http: httpx.AsyncClient, url: str) -> None:
        self.http = http
        self.url = url

    async def fetch(self) -> dict[str, Any]:
        response = await self.http.get(self.url, timeout=5.0)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data


class JwksCache:
    def __init__(
        self,
        source: JwksSource,
        *,
        ttl_seconds: float,
        refetch_cooldown_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.source = source
        self.ttl = ttl_seconds
        self.cooldown = refetch_cooldown_seconds
        self.clock = clock
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float | None = None
        self._lock = asyncio.Lock()

    async def get_key(self, kid: str) -> PyJWK:
        if self._is_stale() or (kid not in self._keys and self._cooldown_elapsed()):
            await self._refresh(kid)
        key = self._keys.get(kid)
        if key is None:
            raise InvalidTokenError(details={"reason": "unknown_key"})
        return key

    def _is_stale(self) -> bool:
        return self._fetched_at is None or self.clock() - self._fetched_at >= self.ttl

    def _cooldown_elapsed(self) -> bool:
        return self._fetched_at is None or self.clock() - self._fetched_at >= self.cooldown

    async def _refresh(self, kid: str) -> None:
        async with self._lock:
            # Another request may have refreshed while we waited for the lock.
            if not self._is_stale() and (kid in self._keys or not self._cooldown_elapsed()):
                return
            try:
                data = await self.source.fetch()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("jwks_fetch_failed", error=repr(exc))
                if not self._keys:
                    raise InvalidTokenError(details={"reason": "keys_unavailable"}) from exc
                return  # keep serving the last known keys
            signing = [k for k in data.get("keys", []) if k.get("use", "sig") == "sig"]
            keyset = PyJWKSet.from_dict({"keys": signing})
            self._keys = {k.key_id: k for k in keyset.keys if k.key_id}
            self._fetched_at = self.clock()
            logger.info("jwks_refreshed", key_ids=sorted(self._keys))


@dataclass(frozen=True, slots=True)
class TokenClaims:
    sub: str
    email: str
    email_verified: bool
    name: str
    client_id: str
    realm_roles: frozenset[str]
    raw: dict[str, Any] = field(repr=False)


class JwtValidator:
    def __init__(
        self,
        jwks: JwksCache,
        *,
        issuer: str,
        audience: str,
        allowed_clients: frozenset[str],
        leeway_seconds: int,
    ) -> None:
        self.jwks = jwks
        self.issuer = issuer
        self.audience = audience
        self.allowed_clients = allowed_clients
        self.leeway = leeway_seconds

    async def validate(self, token: str) -> TokenClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.DecodeError as exc:
            raise InvalidTokenError(details={"reason": "malformed"}) from exc
        alg, kid = header.get("alg"), header.get("kid")
        if alg not in ALLOWED_ALGORITHMS or not isinstance(kid, str):
            raise InvalidTokenError(details={"reason": "unsupported_algorithm_or_kid"})

        key = await self.jwks.get_key(kid)
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[alg],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise ExpiredTokenError from exc
        except jwt.InvalidIssuerError as exc:
            raise InvalidTokenError(details={"reason": "issuer_mismatch"}) from exc
        except jwt.InvalidAudienceError as exc:
            raise InvalidTokenError(details={"reason": "audience_mismatch"}) from exc
        except jwt.PyJWTError as exc:
            raise InvalidTokenError(details={"reason": "verification_failed"}) from exc

        if claims.get("typ", "Bearer") != "Bearer":
            raise InvalidTokenError(details={"reason": "not_an_access_token"})
        client_id = claims.get("azp")
        if client_id not in self.allowed_clients:
            raise InvalidTokenError(details={"reason": "client_not_allowed"})
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            raise InvalidTokenError(details={"reason": "missing_email"})

        roles = claims.get("realm_access", {}).get("roles", [])
        name = claims.get("name") or " ".join(
            p for p in (claims.get("given_name"), claims.get("family_name")) if p
        )
        return TokenClaims(
            sub=str(claims["sub"]),
            email=email,
            email_verified=bool(claims.get("email_verified", False)),
            name=str(name or ""),
            client_id=str(client_id),
            realm_roles=frozenset(r for r in roles if isinstance(r, str)),
            raw=claims,
        )
