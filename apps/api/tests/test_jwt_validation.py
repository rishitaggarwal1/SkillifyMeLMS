"""Access-token validation rules, with a local signing key (no Keycloak needed)."""

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import jwt
import pytest

from app.core.auth.jwt import ExpiredTokenError, InvalidTokenError, JwksCache, JwtValidator
from app.core.config import Settings
from tests.auth import SigningKey, StaticJwksSource, TokenFactory, make_validator


@pytest.fixture
def key() -> SigningKey:
    return SigningKey(kid="k1")


@pytest.fixture
def source(key: SigningKey) -> StaticJwksSource:
    return StaticJwksSource(key)


@pytest.fixture
def tokens(settings: Settings, key: SigningKey) -> TokenFactory:
    return TokenFactory(key=key, issuer=settings.oidc_issuer)


@pytest.fixture
def validator(settings: Settings, source: StaticJwksSource) -> JwtValidator:
    return make_validator(settings, source)


async def _reason(validator: JwtValidator, token: str) -> Any:
    with pytest.raises(InvalidTokenError) as excinfo:
        await validator.validate(token)
    return (excinfo.value.details or {}).get("reason")


async def test_valid_token(validator: JwtValidator, tokens: TokenFactory) -> None:
    claims = await validator.validate(
        tokens.token(sub="abc", email="a@b.test", realm_access={"roles": ["platform_admin", "x"]})
    )
    assert claims.sub == "abc"
    assert claims.email == "a@b.test"
    assert claims.email_verified is True
    assert claims.client_id == "skillifyme-web"
    assert claims.realm_roles == {"platform_admin", "x"}


async def test_expired_token(validator: JwtValidator, tokens: TokenFactory) -> None:
    past = int(time.time()) - 600
    with pytest.raises(ExpiredTokenError):
        await validator.validate(tokens.token(iat=past, exp=past + 60))


async def test_token_not_yet_valid(validator: JwtValidator, tokens: TokenFactory) -> None:
    assert await _reason(validator, tokens.token(nbf=int(time.time()) + 600)) == (
        "verification_failed"
    )


async def test_issuer_must_match_exactly(
    settings: Settings, validator: JwtValidator, tokens: TokenFactory
) -> None:
    # e.g. a token minted via another hostname if Keycloak's issuer were not pinned (KC_HOSTNAME).
    other_host = settings.oidc_issuer.replace("localhost", "127.0.0.1")
    assert await _reason(validator, tokens.token(iss=other_host)) == "issuer_mismatch"
    assert await _reason(validator, tokens.token(iss=settings.oidc_issuer + "/")) == (
        "issuer_mismatch"
    )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"aud": "some-other-api"}, "audience_mismatch"),
        ({"azp": "unknown-client"}, "client_not_allowed"),
        ({"typ": "ID"}, "not_an_access_token"),
        ({"typ": "Refresh"}, "not_an_access_token"),
        ({"email": None}, "missing_email"),
        ({"sub": None}, "verification_failed"),
        ({"exp": None}, "verification_failed"),
    ],
)
async def test_claim_rules(
    validator: JwtValidator, tokens: TokenFactory, overrides: dict[str, Any], reason: str
) -> None:
    assert await _reason(validator, tokens.token(**overrides)) == reason


async def test_alg_none_is_rejected(validator: JwtValidator, tokens: TokenFactory) -> None:
    token = jwt.encode(tokens.claims(), "", algorithm="none", headers={"kid": "k1"})
    assert await _reason(validator, token) == "unsupported_algorithm_or_kid"


async def test_hmac_with_public_key_is_rejected(
    validator: JwtValidator, tokens: TokenFactory, key: SigningKey
) -> None:
    # Classic algorithm confusion: HS256 "signed" with the public RSA key as the HMAC secret.
    # (PyJWT refuses to produce this, so build it by hand the way an attacker would.)
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    signing_input = (
        f"{b64(json.dumps({'alg': 'HS256', 'kid': 'k1', 'typ': 'JWT'}).encode())}."
        f"{b64(json.dumps(tokens.claims()).encode())}"
    )
    signature = hmac.new(key.public_pem, signing_input.encode(), hashlib.sha256).digest()
    token = f"{signing_input}.{b64(signature)}"
    assert await _reason(validator, token) == "unsupported_algorithm_or_kid"


async def test_signature_from_another_key_is_rejected(
    validator: JwtValidator, tokens: TokenFactory
) -> None:
    impostor = SigningKey(kid="k1")  # same kid, different key
    assert await _reason(validator, tokens.token(key=impostor)) == "verification_failed"


async def test_missing_kid_and_garbage(validator: JwtValidator, tokens: TokenFactory) -> None:
    no_kid = jwt.encode(tokens.claims(), tokens.key.private_pem, algorithm="RS256")
    assert await _reason(validator, no_kid) == "unsupported_algorithm_or_kid"
    assert await _reason(validator, "not.a.jwt") == "malformed"


# ---------------------------------------------------------------------------- JWKS caching


async def test_keys_are_cached(
    validator: JwtValidator, tokens: TokenFactory, source: StaticJwksSource
) -> None:
    for _ in range(5):
        await validator.validate(tokens.token())
    assert source.fetches == 1


async def test_key_rotation_refetches_once(settings: Settings, key: SigningKey) -> None:
    source = StaticJwksSource(key)
    now = [1000.0]
    validator = make_validator(settings, source, clock=lambda: now[0], cooldown=30)
    tokens = TokenFactory(key=key, issuer=settings.oidc_issuer)
    await validator.validate(tokens.token())

    rotated = SigningKey(kid="k2")
    source.keys.append(rotated)
    now[0] += 31  # outside the cooldown window
    await validator.validate(tokens.token(key=rotated))  # unknown kid -> one refetch -> accepted
    assert source.fetches == 2


async def test_unknown_kid_refetch_is_rate_limited(settings: Settings, key: SigningKey) -> None:
    source = StaticJwksSource(key)
    now = [1000.0]
    validator = make_validator(settings, source, clock=lambda: now[0], cooldown=30)
    tokens = TokenFactory(key=key, issuer=settings.oidc_issuer)
    await validator.validate(tokens.token())

    junk = SigningKey(kid="attacker")
    for _ in range(10):
        now[0] += 1
        assert await _reason(validator, tokens.token(key=junk)) == "unknown_key"
    assert source.fetches == 1  # no refetch storm inside the cooldown window


async def test_keys_expire_after_ttl(settings: Settings, key: SigningKey) -> None:
    source = StaticJwksSource(key)
    now = [0.0]
    jwks = JwksCache(source, ttl_seconds=600, refetch_cooldown_seconds=30, clock=lambda: now[0])
    await jwks.get_key("k1")
    now[0] += 601
    await jwks.get_key("k1")
    assert source.fetches == 2


class _DownSource:
    async def fetch(self) -> dict[str, Any]:
        raise httpx.ConnectError("keycloak down")


async def test_keycloak_unreachable_without_cached_keys(settings: Settings) -> None:
    jwks = JwksCache(_DownSource(), ttl_seconds=600, refetch_cooldown_seconds=0)
    with pytest.raises(InvalidTokenError) as excinfo:
        await jwks.get_key("k1")
    assert (excinfo.value.details or {})["reason"] == "keys_unavailable"


async def test_encryption_keys_are_ignored(settings: Settings, key: SigningKey) -> None:
    class _WithEncKey(StaticJwksSource):
        async def fetch(self) -> dict[str, Any]:
            data = await super().fetch()
            enc = {**SigningKey(kid="enc-1").jwk(), "use": "enc", "alg": "RSA-OAEP"}
            return {"keys": [*data["keys"], enc]}

    jwks = JwksCache(_WithEncKey(key), ttl_seconds=600, refetch_cooldown_seconds=0)
    await jwks.get_key("k1")
    with pytest.raises(InvalidTokenError):
        await jwks.get_key("enc-1")


def test_issuer_and_urls_derive_from_keycloak_port(settings: Settings) -> None:
    custom = settings.model_copy(
        update={
            "keycloak_port": 9123,
            "keycloak_public_url": None,
            "keycloak_internal_url": "http://keycloak:9123",
            "keycloak_realm": "demo",
        }
    )
    assert custom.oidc_issuer == "http://localhost:9123/realms/demo"
    # Browser-facing issuer, but keys fetched over the Docker network.
    assert custom.oidc_jwks_url == "http://keycloak:9123/realms/demo/protocol/openid-connect/certs"
    assert custom.keycloak_admin_api_url == "http://keycloak:9123/admin/realms/demo"
