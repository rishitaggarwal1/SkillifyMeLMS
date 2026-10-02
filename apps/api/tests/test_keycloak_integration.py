"""Against the real Keycloak from docker-compose (realm from infra/keycloak, dev users from
`make seed`): tokens it issues validate in the API, and the issuer is stable no matter which
hostname the token was requested through.

The browser reaches Keycloak at KEYCLOAK_PUBLIC_URL; containers reach it at KEYCLOAK_INTERNAL_URL
(http://keycloak:<port>). Keycloak pins the issuer with KC_HOSTNAME, and these tests fail if the
issuer in real tokens ever diverges from the one the API expects (Settings.oidc_issuer). The dev
users come from `make seed` (SEED_DEV_USERS=true, password DEV_USER_PASSWORD).
"""

import asyncio
import os
import urllib.parse
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from asgi_lifespan import LifespanManager
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient
from uuid_utils.compat import uuid7

from app.core.auth.jwt import InvalidTokenError, JwtValidator
from app.core.config import Settings
from app.main import create_app, create_jwt_validator
from app.modules.identity.keycloak_admin import KeycloakAdmin, NewUser

TEST_CLIENT = "skillifyme-test"


def _other_host(base_url: str) -> str:
    """The same Keycloak through another hostname (localhost <-> 127.0.0.1)."""
    parts = urllib.parse.urlsplit(base_url)
    other = "127.0.0.1" if parts.hostname == "localhost" else "localhost"
    return parts._replace(netloc=f"{other}:{parts.port}" if parts.port else other).geturl()


def _token_url(settings: Settings, base_url: str | None = None) -> str:
    base = (base_url or settings.keycloak_base_url).rstrip("/")
    return f"{base}/realms/{settings.keycloak_realm}/protocol/openid-connect/token"


def _dev_password(settings: Settings) -> str:
    assert settings.dev_user_password is not None, (
        "DEV_USER_PASSWORD must be set (see .env.example)"
    )
    return settings.dev_user_password.get_secret_value()


async def _password_token(settings: Settings, username: str, *, base_url: str | None = None) -> str:
    assert settings.kc_test_client_secret is not None, "KC_TEST_CLIENT_SECRET must be set"
    async with httpx.AsyncClient() as http:
        response = await http.post(
            _token_url(settings, base_url),
            data={
                "grant_type": "password",
                "client_id": TEST_CLIENT,
                "client_secret": settings.kc_test_client_secret.get_secret_value(),
                "username": username,
                "password": _dev_password(settings),
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
        "KEYCLOAK_PUBLIC_URL so the browser-facing URL and the API's expected issuer agree."
    )


async def test_issuer_is_stable_across_hostnames(settings: Settings) -> None:
    # Same Keycloak, reached through a different hostname (as the BFF/API do over the Docker
    # network). Without KC_HOSTNAME the issuer would follow the request host and break validation.
    via_public = _unverified_claims(await _password_token(settings, "admin@demo-college.local"))
    via_other = _unverified_claims(
        await _password_token(
            settings, "admin@demo-college.local", base_url=_other_host(settings.keycloak_base_url)
        )
    )
    assert via_public["iss"] == via_other["iss"] == settings.oidc_issuer


async def test_real_token_validates_with_jwks_fetched_over_another_host(
    settings: Settings,
) -> None:
    # JWKS fetched via another hostname (standing in for http://keycloak:<port>), issuer from the
    # public URL.
    internal = settings.model_copy(
        update={"keycloak_internal_url": _other_host(settings.keycloak_base_url)}
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


def _mailpit_url() -> str:
    port = os.environ.get("MAILPIT_UI_PORT") or dotenv_values(
        Path(__file__).resolve().parents[3] / ".env"
    ).get("MAILPIT_UI_PORT")
    assert port, "MAILPIT_UI_PORT must be set"
    return f"http://localhost:{port}"


async def test_bulk_create_and_setup_email(settings: Settings) -> None:
    emails = [f"kc-it-{uuid7().hex[-10:]}@college.test" for _ in range(2)]
    async with httpx.AsyncClient() as http:
        admin = KeycloakAdmin(http, settings)
        created = await admin.ensure_users([NewUser(e, "Integration Test") for e in emails])
        again = await admin.ensure_users([NewUser(e, "Integration Test") for e in emails])
        try:
            await admin.send_setup_email(created[emails[0]])
            async with asyncio.timeout(15):
                while True:
                    found = await http.get(
                        f"{_mailpit_url()}/api/v1/search", params={"query": f"to:{emails[0]}"}
                    )
                    if found.json().get("messages_count", 0) > 0:
                        break
                    await asyncio.sleep(0.5)
        finally:
            headers = await admin._auth_header()
            for kc_id in created.values():
                await http.delete(f"{admin.base}/users/{kc_id}", headers=headers)

    assert set(created) == set(emails)
    assert again == created  # partialImport skips existing users; ids resolve the same


async def test_disable_blocks_password_login_and_refresh(settings: Settings) -> None:
    """set_user_enabled against real Keycloak: disabling refuses new logins and revokes the
    refresh token; enabling allows logins again."""
    assert settings.kc_test_client_secret is not None
    email = f"kc-disable-{uuid7().hex[-10:]}@college.test"
    password = f"It-{uuid7().hex}"  # throwaway credential for this test user only
    token_url = _token_url(settings)
    client = {
        "client_id": TEST_CLIENT,
        "client_secret": settings.kc_test_client_secret.get_secret_value(),
    }

    async with httpx.AsyncClient() as http:
        admin = KeycloakAdmin(http, settings)
        kc_id = (await admin.ensure_users([NewUser(email, "Disable Test")]))[email]
        headers = await admin._auth_header()
        try:
            await http.put(
                f"{admin.base}/users/{kc_id}",
                json={"emailVerified": True, "requiredActions": []},
                headers=headers,
            )
            reset = await http.put(
                f"{admin.base}/users/{kc_id}/reset-password",
                json={"type": "password", "value": password, "temporary": False},
                headers=headers,
            )
            assert reset.status_code == 204, reset.text

            async def login() -> httpx.Response:
                return await http.post(
                    token_url,
                    data={**client, "grant_type": "password", "username": email,
                          "password": password, "scope": "openid"},
                )  # fmt: skip

            first = await login()
            assert first.status_code == 200, first.text
            refresh_token = first.json()["refresh_token"]

            await admin.set_user_enabled(kc_id, enabled=False)
            assert (await login()).status_code == 400  # invalid_grant: account disabled
            refreshed = await http.post(
                token_url,
                data={**client, "grant_type": "refresh_token", "refresh_token": refresh_token},
            )
            assert refreshed.status_code == 400  # the session was ended

            await admin.set_user_enabled(kc_id, enabled=True)
            assert (await login()).status_code == 200
        finally:
            await http.delete(f"{admin.base}/users/{kc_id}", headers=headers)


async def test_demo_seed_account_preparation(settings: Settings) -> None:
    """prepare_login and grant_realm_role (used by `make seed-demo`) against real Keycloak: the
    account can sign in with the set password and carries the platform_admin role."""
    assert settings.kc_test_client_secret is not None
    email = f"kc-demo-{uuid7().hex[-10:]}@college.test"
    password = f"Demo-{uuid7().hex}"  # throwaway credential for this test user only
    async with httpx.AsyncClient() as http:
        admin = KeycloakAdmin(http, settings)
        kc_id = (await admin.ensure_users([NewUser(email, "Demo Test")]))[email]
        try:
            await admin.prepare_login(kc_id, full_name="Priya Sharma", password=password)
            await admin.grant_realm_role(kc_id, "platform_admin")
            await admin.grant_realm_role(kc_id, "platform_admin")  # idempotent
            response = await http.post(
                _token_url(settings),
                data={
                    "grant_type": "password", "client_id": TEST_CLIENT,
                    "client_secret": settings.kc_test_client_secret.get_secret_value(),
                    "username": email, "password": password, "scope": "openid",
                },
            )  # fmt: skip
            assert response.status_code == 200, response.status_code
            claims = _unverified_claims(response.json()["access_token"])
            assert "platform_admin" in claims["realm_access"]["roles"]
            assert claims["name"] == "Priya Sharma"
        finally:
            await http.delete(f"{admin.base}/users/{kc_id}", headers=await admin._auth_header())
