"""infra/keycloak/realm.py: the realm rendered from settings, and the idempotent sync.

The sync is checked twice over: against a recording fake (every request it makes, so the test can
prove it never deletes, never sends a client secret and never touches brute-force state), and
against the real Keycloak from docker-compose (a second run changes nothing)."""

import copy
import importlib.util
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[3]


def _load_realm_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("kc_realm", REPO / "infra/keycloak/realm.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["kc_realm"] = module
    spec.loader.exec_module(module)
    return module


realm_py = _load_realm_module()

SERVER_ENV = {
    "KEYCLOAK_REALM": "skillifyme",
    "WEB_ORIGIN": "https://dev.example.test",
    "KC_WEB_CLIENT_SECRET": "web-secret-from-env",
    "KC_ADMIN_CLIENT_SECRET": "admin-secret-from-env",
    "SMTP_HOST": "smtp.example.test",
    "SMTP_PORT": "587",
    "SMTP_FROM": "no-reply@example.test",
}


# ------------------------------------------------------------------------------------------ render


def test_server_render_has_no_dev_clients_users_or_placeholders() -> None:
    realm = realm_py.render(SERVER_ENV)
    dumped = json.dumps(realm)
    assert "${" not in dumped
    assert {c["clientId"] for c in realm["clients"]} == {
        "skillifyme-web",
        "skillifyme-api",
        "skillifyme-admin",
    }
    # Only the admin client's service account; dev users are created by `make seed`.
    assert [u["username"] for u in realm["users"]] == ["service-account-skillifyme-admin"]
    assert realm["bruteForceProtected"] is True  # on unless explicitly turned off
    web = next(c for c in realm["clients"] if c["clientId"] == "skillifyme-web")
    assert all(uri.startswith("https://dev.example.test") for uri in web["redirectUris"])
    assert web["webOrigins"] == ["https://dev.example.test"]
    assert realm["smtpServer"]["host"] == "smtp.example.test"
    assert "auth" not in realm["smtpServer"]
    assert "localhost" not in dumped


def test_render_fails_closed_and_names_every_missing_setting() -> None:
    env = {k: v for k, v in SERVER_ENV.items() if k not in {"WEB_ORIGIN", "SMTP_FROM"}}
    with pytest.raises(realm_py.RealmError) as excinfo:
        realm_py.render(env)
    assert "SMTP_FROM" in str(excinfo.value)
    assert "WEB_ORIGIN" in str(excinfo.value)
    # An empty value counts as missing.
    with pytest.raises(realm_py.RealmError, match="KC_WEB_CLIENT_SECRET"):
        realm_py.render({**SERVER_ENV, "KC_WEB_CLIENT_SECRET": ""})


def test_render_rejects_bad_booleans_and_non_http_origins() -> None:
    with pytest.raises(realm_py.RealmError, match="KEYCLOAK_BRUTE_FORCE"):
        realm_py.render({**SERVER_ENV, "KEYCLOAK_BRUTE_FORCE": "maybe"})
    with pytest.raises(realm_py.RealmError, match="WEB_ORIGIN"):
        realm_py.render({**SERVER_ENV, "WEB_ORIGIN": "dev.example.test"})


def test_dev_clients_are_opt_in_and_need_their_secret() -> None:
    with pytest.raises(realm_py.RealmError, match="KC_TEST_CLIENT_SECRET"):
        realm_py.render({**SERVER_ENV, "KEYCLOAK_DEV_CLIENTS": "true"})
    realm = realm_py.render(
        {**SERVER_ENV, "KEYCLOAK_DEV_CLIENTS": "true", "KC_TEST_CLIENT_SECRET": "t"}
    )
    test_client = next(c for c in realm["clients"] if c["clientId"] == "skillifyme-test")
    assert test_client["directAccessGrantsEnabled"] is True


def test_smtp_auth_and_tls_follow_settings() -> None:
    realm = realm_py.render(
        {**SERVER_ENV, "SMTP_USER": "mailer", "SMTP_PASSWORD": "pw", "SMTP_STARTTLS": "true"}
    )
    smtp = realm["smtpServer"]
    assert (smtp["auth"], smtp["user"], smtp["password"]) == ("true", "mailer", "pw")
    assert (smtp["starttls"], smtp["ssl"]) == ("true", "false")
    assert (
        realm_py.render({**SERVER_ENV, "KEYCLOAK_BRUTE_FORCE": "false"})["bruteForceProtected"]
        is False
    )


# ------------------------------------------------------------------------------------------ sync


class FakeKeycloak:
    """The admin API paths the sync uses, over in-memory state; records every request."""

    def __init__(self, desired: dict[str, Any]) -> None:
        self.requests: list[tuple[str, str, Any]] = []
        self.realm = {k: copy.deepcopy(v) for k, v in desired.items() if not isinstance(v, list)}
        self.realm.pop("roles", None)
        self.roles = {r["name"] for r in desired["roles"]["realm"]}
        self.clients: dict[str, dict[str, Any]] = {}
        for index, client in enumerate(desired["clients"]):
            self.clients[f"c{index}"] = {**copy.deepcopy(client), "id": f"c{index}"}
        self.clients["rm"] = {"id": "rm", "clientId": "realm-management"}
        names = ("manage-users", "view-users", "query-users", "realm-admin")
        self.available = [{"id": f"r-{n}", "name": n} for n in names]
        self.granted: list[dict[str, str]] = [
            r for r in self.available if r["name"] in {"manage-users", "view-users", "query-users"}
        ]

    def client(self, client_id: str) -> dict[str, Any]:
        return next(c for c in self.clients.values() if c["clientId"] == client_id)

    def __call__(  # noqa: PLR0911, PLR0912 - one branch per admin API route
        self, method: str, path: str, body: Any = None
    ) -> tuple[int, Any]:
        self.requests.append((method, path, copy.deepcopy(body)))
        url = urllib.parse.urlsplit(path)
        query = dict(urllib.parse.parse_qsl(url.query))
        parts = url.path.removeprefix("/admin/realms/skillifyme").strip("/").split("/")
        parts = [p for p in parts if p]
        match method, parts:
            case "GET", []:
                realm = copy.deepcopy(self.realm)
                if "password" in realm.get("smtpServer", {}):
                    realm["smtpServer"]["password"] = realm_py.SECRET_MASK
                return 200, realm
            case "PUT", []:
                smtp = body.get("smtpServer")
                if smtp and smtp.get("password") == realm_py.SECRET_MASK:
                    smtp["password"] = self.realm["smtpServer"].get("password")
                self.realm.update(body)
                return 204, None
            case "GET", ["roles", name]:
                return (200, {"name": name}) if name in self.roles else (404, None)
            case "POST", ["roles"]:
                self.roles.add(body["name"])
                return 201, None
            case "GET", ["clients"]:
                found = [c for c in self.clients.values() if c["clientId"] == query["clientId"]]
                return 200, copy.deepcopy(found)
            case "POST", ["clients"]:
                new_id = f"c{len(self.clients)}"
                self.clients[new_id] = {**body, "id": new_id}
                return 201, None
            case "PUT", ["clients", cid]:
                self.clients[cid] = {**body, "secret": self.clients[cid].get("secret")}
                return 204, None
            case "POST", ["clients", cid, "protocol-mappers", "models"]:
                self.clients[cid].setdefault("protocolMappers", []).append(body)
                return 201, None
            case "GET", ["clients", _, "service-account-user"]:
                return 200, {"id": "sa"}
            case "GET", ["users", "sa", "role-mappings", "clients", "rm"]:
                return 200, copy.deepcopy(self.granted)
            case "GET", ["users", "sa", "role-mappings", "clients", "rm", "available"]:
                names = {r["name"] for r in self.granted}
                return 200, [r for r in self.available if r["name"] not in names]
            case "POST", ["users", "sa", "role-mappings", "clients", "rm"]:
                self.granted += body
                return 204, None
        raise AssertionError(f"unexpected request {method} {path}")


def _desired() -> dict[str, Any]:
    desired: dict[str, Any] = realm_py.render(
        {
            **SERVER_ENV,
            "KEYCLOAK_DEV_CLIENTS": "true",
            "KC_TEST_CLIENT_SECRET": "test-secret-from-env",
            "SMTP_USER": "mailer",
            "SMTP_PASSWORD": "smtp-pw-from-env",
        }
    )
    return desired


def _drifted_server() -> FakeKeycloak:
    """A realm imported long ago from older settings, with secrets that differ from .env."""
    server = FakeKeycloak(_desired())
    server.realm.update(bruteForceProtected=False, failureFactor=30, displayName="Old name")
    server.realm["smtpServer"].update(host="old-smtp.example.test", password="stored-smtp-pw")
    server.roles.clear()
    web = server.client("skillifyme-web")
    web.update(redirectUris=["http://old.example.test/*"], webOrigins=["http://old.example.test"])
    web["protocolMappers"] = []
    web["secret"] = "existing-web-secret"
    server.client("skillifyme-admin")["secret"] = "existing-admin-secret"
    test_client = server.client("skillifyme-test")
    del server.clients[test_client["id"]]
    server.granted = []
    return server


def test_sync_repairs_drift_then_a_second_run_changes_nothing() -> None:
    server = _drifted_server()
    desired = _desired()

    changes = realm_py.sync(desired, server)
    assert "realm.bruteForceProtected" in changes
    assert "realm.smtpServer" in changes
    assert "role platform_admin created" in changes
    assert "client skillifyme-web.redirectUris" in changes
    assert "client skillifyme-web mapper api-audience added" in changes
    assert "client skillifyme-test created" in changes
    assert any("granted realm-management:manage-users" in c for c in changes)
    web = server.client("skillifyme-web")
    assert sorted(web["redirectUris"]) == sorted(
        next(c for c in desired["clients"] if c["clientId"] == "skillifyme-web")["redirectUris"]
    )
    # Existing secrets are untouched; the stored SMTP password survives the SMTP update.
    assert web["secret"] == "existing-web-secret"
    assert server.client("skillifyme-admin")["secret"] == "existing-admin-secret"
    assert server.realm["smtpServer"]["password"] == "stored-smtp-pw"
    assert server.realm["smtpServer"]["host"] == "smtp.example.test"

    snapshot = copy.deepcopy((server.realm, server.clients, server.roles, server.granted))
    server.requests.clear()
    assert realm_py.sync(desired, server) == []
    assert (server.realm, server.clients, server.roles, server.granted) == snapshot
    assert {method for method, _, _ in server.requests} == {"GET"}


def test_sync_never_deletes_rotates_secrets_or_touches_brute_force_state() -> None:
    server = _drifted_server()
    realm_py.sync(_desired(), server)
    realm_py.sync(_desired(), server)
    assert server.requests
    for method, path, body in server.requests:
        assert method in {"GET", "PUT", "POST"}, (method, path)
        assert not re.search(r"attack-detection|client-secret|/credentials", path), path
        if method == "PUT" and "/clients/" in path:
            assert "secret" not in body, path
        if body and "smtpServer" in body:
            assert body["smtpServer"].get("password") in {None, realm_py.SECRET_MASK}
    # The only POST /clients is the missing dev client being created.
    created = [
        b["clientId"] for m, p, b in server.requests if m == "POST" and p.endswith("/clients")
    ]
    assert created == ["skillifyme-test"]


def test_sync_sends_the_smtp_password_only_when_asked() -> None:
    server = _drifted_server()
    realm_py.sync(_desired(), server, update_smtp_password=True)
    assert server.realm["smtpServer"]["password"] == "smtp-pw-from-env"


def test_sync_refuses_a_missing_realm() -> None:
    def call(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        return 404, None

    with pytest.raises(realm_py.RealmError, match="doesn't exist"):
        realm_py.sync(_desired(), call)


# ------------------------------------------------------------------------------------------ real


def _local_env() -> dict[str, str]:
    values = {k: v for k, v in dotenv_values(REPO / ".env").items() if v is not None}
    return {**values, **{k: v for k, v in os.environ.items() if k in values}}


def test_sync_against_the_real_keycloak_is_idempotent() -> None:
    env = _local_env()
    base_url = env["KEYCLOAK_INTERNAL_URL"]
    desired = realm_py.render(env)
    token = realm_py.admin_token(base_url, env["KEYCLOAK_ADMIN"], env["KEYCLOAK_ADMIN_PASSWORD"])
    call = realm_py.http_request(base_url, token)
    realm_py.sync(desired, call)  # brings a realm from older settings up to date
    before = call("GET", "/admin/realms/" + desired["realm"], None)[1]
    assert realm_py.sync(desired, call) == []
    after = call("GET", "/admin/realms/" + desired["realm"], None)[1]
    assert before == after
