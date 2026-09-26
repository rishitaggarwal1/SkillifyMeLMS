"""Keycloak Admin REST API client, authenticated as the `skillifyme-admin` service account
(realm-management: manage-users / view-users / query-users). Used for seeding, invitations and CSV
imports. Talks to Keycloak over the backchannel (KEYCLOAK_INTERNAL_URL)."""

import time
from typing import Any

import httpx

from app.core.config import Settings


class KeycloakAdminError(Exception):
    pass


class KeycloakAdmin:
    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        if settings.kc_admin_client_secret is None:
            msg = "KC_ADMIN_CLIENT_SECRET is not configured"
            raise KeycloakAdminError(msg)
        self.http = http
        self.base = settings.keycloak_admin_api_url
        self.token_url = settings.oidc_token_url
        self.client_id = settings.kc_admin_client_id
        self.client_secret = settings.kc_admin_client_secret.get_secret_value()
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
