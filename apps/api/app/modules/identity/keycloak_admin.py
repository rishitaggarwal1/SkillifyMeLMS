"""Keycloak Admin REST API client, authenticated as the `skillifyme-admin` service account
(realm-management: manage-users / view-users / query-users). Used for seeding, invitations and CSV
imports. Talks to Keycloak over the backchannel (KEYCLOAK_INTERNAL_URL)."""

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import Settings


class KeycloakAdminError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class NewUser:
    email: str
    full_name: str


class IdentityProviderAdmin(Protocol):
    """What the identity service needs from Keycloak (a fake implements it in tests)."""

    async def find_user_by_email(self, email: str) -> dict[str, Any] | None: ...

    async def ensure_users(self, users: Sequence[NewUser]) -> dict[str, str]:
        """Create missing users (existing ones are left alone); returns lowercased email -> id."""
        ...

    async def send_setup_email(self, keycloak_id: str) -> None:
        """Ask Keycloak to email a link to verify the address and set a password."""
        ...

    async def set_user_enabled(self, keycloak_id: str, *, enabled: bool) -> None:
        """Allow or block sign-in. Disabling also ends the user's existing Keycloak sessions."""
        ...


def _split_name(full_name: str) -> tuple[str, str]:
    first, _, last = full_name.strip().partition(" ")
    return first or full_name, last


class KeycloakAdmin:
    # Invitation links stay valid for 7 days.
    SETUP_EMAIL_LIFESPAN_SECONDS = 7 * 24 * 3600
    # Parallel user creations per call: keeps imports fast without flooding Keycloak.
    CREATE_CONCURRENCY = 8

    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        if settings.kc_admin_client_secret is None:
            msg = "KC_ADMIN_CLIENT_SECRET is not configured"
            raise KeycloakAdminError(msg)
        self.http = http
        self.base = settings.keycloak_admin_api_url
        self.token_url = settings.oidc_token_url
        self.client_id = settings.kc_admin_client_id
        self.client_secret = settings.kc_admin_client_secret.get_secret_value()
        self.web_client_id = "skillifyme-web"
        self.redirect_uri = f"{settings.web_origin}/" if settings.web_origin else None
        self._token: str | None = None
        self._token_expires_at = 0.0

    async def _auth_header(self) -> dict[str, str]:
        if self._token is None or time.monotonic() >= self._token_expires_at:
            response = await self.http.post(
                self.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=10.0,
            )
            if response.status_code != httpx.codes.OK:
                msg = f"Service-account login failed ({response.status_code})"
                raise KeycloakAdminError(msg)
            body = response.json()
            self._token = str(body["access_token"])
            # Refresh 30s before expiry.
            self._token_expires_at = time.monotonic() + max(int(body["expires_in"]) - 30, 1)
        return {"Authorization": f"Bearer {self._token}"}

    async def _get(self, path: str, params: dict[str, str]) -> Any:
        response = await self.http.get(
            f"{self.base}{path}", params=params, headers=await self._auth_header(), timeout=10.0
        )
        if response.status_code != httpx.codes.OK:
            msg = f"GET {path} failed ({response.status_code})"
            raise KeycloakAdminError(msg)
        return response.json()

    async def find_user_by_username(self, username: str) -> dict[str, Any] | None:
        users = await self._get("/users", {"username": username, "exact": "true"})
        return users[0] if users else None

    async def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        users = await self._get("/users", {"email": email, "exact": "true"})
        return users[0] if users else None

    async def ensure_users(self, users: Sequence[NewUser]) -> dict[str, str]:
        """Create missing users (existing ones are left untouched) and resolve every email to its
        Keycloak id. Uses POST /users per user, which needs only `manage-users` (the bulk
        partialImport endpoint would require realm-admin rights), with bounded concurrency."""
        semaphore = asyncio.Semaphore(self.CREATE_CONCURRENCY)
        headers = await self._auth_header()

        async def ensure(user: NewUser) -> tuple[str, str]:
            email = user.email.lower()
            first, last = _split_name(user.full_name)
            async with semaphore:
                response = await self.http.post(
                    f"{self.base}/users",
                    json={
                        "username": email,
                        "email": email,
                        "firstName": first,
                        "lastName": last,
                        "enabled": True,
                        "emailVerified": False,
                    },
                    headers=headers,
                    timeout=15.0,
                )
                if response.status_code == httpx.codes.CREATED:
                    return email, response.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
                if response.status_code == httpx.codes.CONFLICT:
                    found = await self.find_user_by_email(email)
                    if found is not None:
                        return email, str(found["id"])
                msg = f"Could not create or find {email} ({response.status_code})"
                raise KeycloakAdminError(msg)

        unique = list({u.email.lower(): u for u in users}.values())
        return dict(await asyncio.gather(*(ensure(u) for u in unique)))

    async def send_setup_email(self, keycloak_id: str) -> None:
        params = {"lifespan": str(self.SETUP_EMAIL_LIFESPAN_SECONDS)}
        if self.redirect_uri:
            params |= {"client_id": self.web_client_id, "redirect_uri": self.redirect_uri}
        response = await self.http.put(
            f"{self.base}/users/{keycloak_id}/execute-actions-email",
            params=params,
            json=["VERIFY_EMAIL", "UPDATE_PASSWORD"],
            headers=await self._auth_header(),
            timeout=15.0,
        )
        if response.status_code not in (httpx.codes.OK, httpx.codes.NO_CONTENT):
            msg = f"execute-actions-email failed ({response.status_code}): {response.text[:200]}"
            raise KeycloakAdminError(msg)

    async def set_user_enabled(self, keycloak_id: str, *, enabled: bool) -> None:
        headers = await self._auth_header()
        # PUT /users/{id} merges the representation: only `enabled` changes.
        response = await self.http.put(
            f"{self.base}/users/{keycloak_id}",
            json={"enabled": enabled},
            headers=headers,
            timeout=10.0,
        )
        if response.status_code not in (httpx.codes.OK, httpx.codes.NO_CONTENT):
            msg = f"Updating user {keycloak_id} failed ({response.status_code})"
            raise KeycloakAdminError(msg)
        if not enabled:
            # Refresh tokens stop working at once; access tokens expire within their short TTL,
            # and the API already refuses disabled users.
            response = await self.http.post(
                f"{self.base}/users/{keycloak_id}/logout", headers=headers, timeout=10.0
            )
            if response.status_code not in (httpx.codes.OK, httpx.codes.NO_CONTENT):
                msg = f"Ending sessions of {keycloak_id} failed ({response.status_code})"
                raise KeycloakAdminError(msg)
