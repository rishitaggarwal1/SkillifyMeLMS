"""Against the real Keycloak from docker-compose (dev realm): tokens it issues validate in the API,
and the issuer is stable no matter which hostname the token was requested through.

The browser reaches Keycloak at http://localhost:<KEYCLOAK_PORT>; containers reach it at
http://keycloak:<KEYCLOAK_PORT>. Keycloak pins the issuer with KC_HOSTNAME, and these tests fail if
the issuer in real tokens ever diverges from the one the API expects (Settings.oidc_issuer).
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import jwt
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.core.auth.jwt import InvalidTokenError, JwtValidator
from app.core.config import Settings
from app.main import create_app, create_jwt_validator
from app.modules.identity.keycloak_admin import KeycloakAdmin

PASSWORD = "Local-Dev-Only-1"  # dev-realm test users only
TEST_CLIENT = "skillifyme-test"


def _token_url(settings: Settings, host: str) -> str:
    return (
        f"http://{host}:{settings.keycloak_port}/realms/{settings.keycloak_realm}"
        "/protocol/openid-connect/token"
    )


async def _password_token(settings: Settings, username: str, *, host: str = "localhost") -> str:
    assert settings.kc_test_client_secret is not None, "KC_TEST_CLIENT_SECRET must be set"
    async with httpx.AsyncClient() as http:
        response = await http.post(
            _token_url(settings, host),
            data={
                "grant_type": "password",
                "client_id": TEST_CLIENT,
                "client_secret": settings.kc_test_client_secret.get_secret_value(),
                "username": username,
                "password": PASSWORD,
                "scope": "openid",
            },
        )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _unverified_claims(token: str) -> dict[str, Any]:
    claims: dict[str, Any] = jwt.decode(token, options={"verify_signature": False})
    return claims


@pytest.fixture
async def real_validator(settings: Settings) -> AsyncIterator[JwtValidator]:
    async with httpx.AsyncClient() as http:
        yield create_jwt_validator(settings, http)


async def test_token_issuer_matches_configured_issuer(settings: Settings) -> None:
    token = await _password_token(settings, "cse.student@demo-college.local")
    claims = _unverified_claims(token)
    assert claims["iss"] == settings.oidc_issuer, (
        "Keycloak's token issuer differs from Settings.oidc_issuer; check KC_HOSTNAME and "
        "KEYCLOAK_PORT so the browser-facing URL and the API's expected issuer agree."
    )


async def test_issuer_is_stable_across_hostnames(settings: Settings) -> None:
    # Same Keycloak, reached through a different hostname (as the BFF/API do over the Docker
    # network). Without KC_HOSTNAME the issuer would follow the request host and break validation.
    via_localhost = _unverified_claims(await _password_token(settings, "admin@demo-college.local"))
    via_ip = _unverified_claims(
        await _password_token(settings, "admin@demo-college.local", host="127.0.0.1")
    )
    assert via_localhost["iss"] == via_ip["iss"] == settings.oidc_issuer


async def test_real_token_validates_with_jwks_fetched_over_another_host(
    settings: Settings,
) -> None:
    # JWKS fetched via 127.0.0.1 (standing in for http://keycloak:<port>), issuer from localhost.
    internal = settings.model_copy(
        update={"keycloak_internal_url": f"http://127.0.0.1:{settings.keycloak_port}"}
    )
    token = await _password_token(settings, "platform.admin@skillifyme.local")
    async with httpx.AsyncClient() as http:
        claims = await create_jwt_validator(internal, http).validate(token)
    assert claims.email == "platform.admin@skillifyme.local"
    assert "platform_admin" in claims.realm_roles
    assert claims.client_id == TEST_CLIENT


async def test_real_token_is_rejected_when_expected_issuer_differs(settings: Settings) -> None:
    token = await _password_token(settings, "cse.student@demo-college.local")
    wrong = settings.model_copy(update={"keycloak_public_url": "http://keycloak.example:1"})
    async with httpx.AsyncClient() as http:
        validator = create_jwt_validator(
            wrong.model_copy(update={"keycloak_internal_url": settings.keycloak_backchannel_url}),
            http,
        )
        with pytest.raises(InvalidTokenError) as excinfo:
            await validator.validate(token)
    assert (excinfo.value.details or {})["reason"] == "issuer_mismatch"


async def test_me_with_a_real_keycloak_token(settings: Settings, migrated_database: None) -> None:
    app = create_app(settings)  # real validator: JWKS from Keycloak
    token = await _password_token(settings, "ece.student@demo-college.local")
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    assert response.json()["user"]["email"] == "ece.student@demo-college.local"


async def test_admin_service_account_can_look_up_users(settings: Settings) -> None:
    async with httpx.AsyncClient() as http:
        admin = KeycloakAdmin(http, settings)
        user = await admin.find_user_by_username("cse.student@demo-college.local")
        missing = await admin.find_user_by_email("nobody@nowhere.test")
    assert user is not None
    assert user["email"] == "cse.student@demo-college.local"
    assert missing is None
