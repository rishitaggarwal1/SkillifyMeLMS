"""The SkillifyMe Keycloak realm, from settings (standard library only).

    python realm.py render --out /import/skillifyme-realm.json
    python realm.py sync --url http://keycloak:8080 [--update-smtp-password]

**render** turns `realm.template.json` into the realm Keycloak imports at startup. Every `${NAME}`
comes from the environment (.env); a missing one stops the render (fail closed). Settings:

- KEYCLOAK_REALM, WEB_ORIGIN, KC_WEB_CLIENT_SECRET, KC_ADMIN_CLIENT_SECRET (required)
- SMTP_HOST, SMTP_PORT, SMTP_FROM (required); SMTP_FROM_NAME, SMTP_USER, SMTP_PASSWORD,
  SMTP_STARTTLS, SMTP_SSL (optional; a user turns SMTP auth on)
- KEYCLOAK_BRUTE_FORCE: true | false (default true: production realms must protect logins)
- KEYCLOAK_DEV_CLIENTS: true adds `realm.dev-clients.json` (password-grant test client; needs
  KC_TEST_CLIENT_SECRET). Default false: servers never get it.
- KEYCLOAK_DISPLAY_NAME (default "SkillifyMe Portal")

**sync** updates a realm that already exists (Keycloak only imports a realm it doesn't have, so
template changes never reach a running server otherwise). It signs in to the master realm as
KEYCLOAK_ADMIN and makes the realm match the rendered settings: realm options, brute-force
protection, SMTP, the platform_admin role, the clients' redirect URIs, web origins and flags, and
missing protocol mappers or service-account roles. It is idempotent (a second run changes
nothing) and deliberately limited:

- it never deletes anything (users, clients, roles, mappers);
- it never sends or regenerates a client secret (existing clients keep theirs);
- it never touches brute-force state (no attack-detection calls), only the settings;
- the SMTP password is sent only with --update-smtp-password (Keycloak never returns it, so a
  change can't be detected).
"""

import argparse
import copy
import json
import os
import string
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "realm.template.json"
DEV_CLIENTS = HERE / "realm.dev-clients.json"

# What Keycloak returns (and accepts back) in place of a stored secret; not a secret itself.
SECRET_MASK = "**********"  # noqa: S105
TRUE = {"1", "true", "yes", "on"}
FALSE = {"0", "false", "no", "off", ""}

# Realm options the sync keeps in line with the template (everything else is left alone).
REALM_FIELDS = (
    "displayName", "sslRequired", "registrationAllowed", "loginWithEmailAllowed",
    "duplicateEmailsAllowed", "resetPasswordAllowed", "verifyEmail", "bruteForceProtected",
    "permanentLockout", "failureFactor", "waitIncrementSeconds", "maxFailureWaitSeconds",
    "quickLoginCheckMilliSeconds", "minimumQuickLoginWaitSeconds", "maxDeltaTimeSeconds",
    "accessTokenLifespan", "ssoSessionIdleTimeout", "ssoSessionMaxLifespan",
)  # fmt: skip
CLIENT_FIELDS = (
    "name", "enabled", "publicClient", "bearerOnly", "standardFlowEnabled",
    "implicitFlowEnabled", "directAccessGrantsEnabled", "serviceAccountsEnabled",
    "redirectUris", "webOrigins",
)  # fmt: skip


class RealmError(Exception):
    pass


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in TRUE:
        return True
    if value in FALSE:
        return False
    msg = f"{name} must be true or false, not {raw!r}"
    raise RealmError(msg)


def _substitute(node: Any, values: Mapping[str, str], missing: set[str]) -> Any:
    if isinstance(node, str):
        template = string.Template(node)
        for name in template.get_identifiers():
            if name not in values:
                missing.add(name)
        return template.safe_substitute(values)
    if isinstance(node, list):
        return [_substitute(item, values, missing) for item in node]
    if isinstance(node, dict):
        return {key: _substitute(value, values, missing) for key, value in node.items()}
    return node


def render(env: Mapping[str, str]) -> dict[str, Any]:
    """The realm for these settings; raises RealmError naming every missing or bad setting."""
    dev_clients = _bool(env, "KEYCLOAK_DEV_CLIENTS", default=False)
    brute_force = _bool(env, "KEYCLOAK_BRUTE_FORCE", default=True)
    values = {
        "KEYCLOAK_DISPLAY_NAME": "SkillifyMe Portal",
        "SMTP_FROM_NAME": "SkillifyMe",
        **{k: v for k, v in env.items() if v != ""},
        "KEYCLOAK_BRUTE_FORCE": "true" if brute_force else "false",
    }
    missing: set[str] = set()
    realm: dict[str, Any] = _substitute(json.loads(TEMPLATE.read_text(encoding="utf-8")), values, missing)
    if dev_clients:
        realm["clients"] += _substitute(
            json.loads(DEV_CLIENTS.read_text(encoding="utf-8")), values, missing
        )
    if missing:
        msg = f"missing settings for the realm: {', '.join(sorted(missing))}"
        raise RealmError(msg)
    if not values.get("WEB_ORIGIN", "").startswith(("http://", "https://")):
        msg = "WEB_ORIGIN must be an http(s) origin"
        raise RealmError(msg)
    realm["bruteForceProtected"] = brute_force
    smtp = realm["smtpServer"]
    if user := env.get("SMTP_USER"):
        smtp.update({"auth": "true", "user": user, "password": env.get("SMTP_PASSWORD", "")})
    smtp["starttls"] = "true" if _bool(env, "SMTP_STARTTLS", default=False) else "false"
    smtp["ssl"] = "true" if _bool(env, "SMTP_SSL", default=False) else "false"
    return realm


# ============================================================================ sync

Request = Callable[[str, str, Any], tuple[int, Any]]


def http_request(base_url: str, token: str | None) -> Request:
    def call(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(  # noqa: S310 - the configured Keycloak URL
            f"{base_url.rstrip('/')}{path}", data=data, method=method
        )
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            return exc.code, None

    return call


def admin_token(base_url: str, username: str, password: str) -> str:
    form = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": username,
            "password": password,
        }
    ).encode()
    request = urllib.request.Request(  # noqa: S310
        f"{base_url.rstrip('/')}/realms/master/protocol/openid-connect/token", data=form
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return str(json.loads(response.read())["access_token"])
    except urllib.error.HTTPError as exc:
        msg = f"Keycloak admin sign-in failed (HTTP {exc.code})"
        raise RealmError(msg) from exc


def _expect(status: int, ok: tuple[int, ...], what: str) -> None:
    if status not in ok:
        msg = f"{what} failed (HTTP {status})"
        raise RealmError(msg)


def _same(current: Any, desired: Any) -> bool:
    # Keycloak stores redirect URIs and web origins as sets: their order isn't stable.
    if isinstance(current, list) and isinstance(desired, list):
        return sorted(map(str, current)) == sorted(map(str, desired))
    return bool(current == desired)


def _smtp_without_secrets(smtp: Mapping[str, Any]) -> dict[str, str]:
    return {k: str(v) for k, v in smtp.items() if k != "password"}


def sync(
    desired: Mapping[str, Any], call: Request, *, update_smtp_password: bool = False
) -> list[str]:
    """Make the existing realm match `desired` (see the module docstring). Returns what changed."""
    realm = desired["realm"]
    base = f"/admin/realms/{urllib.parse.quote(realm)}"
    changes: list[str] = []

    status, current = call("GET", base, None)
    if status == 404:  # noqa: PLR2004
        msg = f"realm {realm!r} doesn't exist; Keycloak imports it on first start"
        raise RealmError(msg)
    _expect(status, (200,), "reading the realm")
    update = {f: desired[f] for f in REALM_FIELDS if f in desired and current.get(f) != desired[f]}
    smtp = desired.get("smtpServer", {})
    if _smtp_without_secrets(current.get("smtpServer") or {}) != _smtp_without_secrets(smtp) or (
        update_smtp_password and "password" in smtp
    ):
        update["smtpServer"] = dict(smtp) if update_smtp_password else _smtp_without_secrets(smtp)
        if not update_smtp_password and "password" in smtp:
            # Keycloak keeps the stored password when it receives its mask; leaving the key out
            # would erase it.
            update["smtpServer"]["password"] = SECRET_MASK
    if update:
        _expect(call("PUT", base, update)[0], (204,), "updating the realm")
        changes += [f"realm.{key}" for key in sorted(update)]

    for role in desired.get("roles", {}).get("realm", []):
        status, _ = call("GET", f"{base}/roles/{urllib.parse.quote(role['name'])}", None)
        if status == 404:  # noqa: PLR2004
            _expect(call("POST", f"{base}/roles", role)[0], (201,), f"creating role {role['name']}")
            changes.append(f"role {role['name']} created")

    for client in desired.get("clients", []):
        changes += _sync_client(base, client, call)
    for user in desired.get("users", []):
        changes += _sync_service_account_roles(base, user, call)
    return changes


def _sync_client(base: str, client: Mapping[str, Any], call: Request) -> list[str]:
    client_id = client["clientId"]
    status, found = call("GET", f"{base}/clients?clientId={urllib.parse.quote(client_id)}", None)
    _expect(status, (200,), f"looking up client {client_id}")
    if not found:
        # A missing client is created as the template says (its secret from settings): this is
        # creation, never rotation of an existing secret.
        _expect(call("POST", f"{base}/clients", client)[0], (201,), f"creating {client_id}")
        return [f"client {client_id} created"]
    current = found[0]
    changes: list[str] = []
    updated = copy.deepcopy(current)
    for field in CLIENT_FIELDS:
        if field in client and not _same(current.get(field), client[field]):
            updated[field] = client[field]
            changes.append(f"client {client_id}.{field}")
    attributes = dict(current.get("attributes") or {})
    for key, value in (client.get("attributes") or {}).items():
        if attributes.get(key) != value:
            attributes[key] = value
            changes.append(f"client {client_id}.attributes.{key}")
    if changes:
        updated["attributes"] = attributes
        updated.pop("secret", None)  # never sent: the existing secret stays as it is
        _expect(
            call("PUT", f"{base}/clients/{current['id']}", updated)[0],
            (204,),
            f"updating {client_id}",
        )
    names = {m.get("name") for m in current.get("protocolMappers") or []}
    for mapper in client.get("protocolMappers") or []:
        if mapper["name"] not in names:
            path = f"{base}/clients/{current['id']}/protocol-mappers/models"
            _expect(call("POST", path, mapper)[0], (201,), f"adding mapper {mapper['name']}")
            changes.append(f"client {client_id} mapper {mapper['name']} added")
    return changes


def _sync_service_account_roles(base: str, user: Mapping[str, Any], call: Request) -> list[str]:
    """The service account's client roles (e.g. realm-management: manage-users); adds only."""
    client_id = user.get("serviceAccountClientId")
    if not client_id:
        return []
    status, clients = call("GET", f"{base}/clients?clientId={urllib.parse.quote(client_id)}", None)
    _expect(status, (200,), f"looking up client {client_id}")
    if not clients:
        return []
    status, account = call("GET", f"{base}/clients/{clients[0]['id']}/service-account-user", None)
    _expect(status, (200,), f"reading the {client_id} service account")
    changes: list[str] = []
    for owner, wanted in (user.get("clientRoles") or {}).items():
        status, owners = call("GET", f"{base}/clients?clientId={urllib.parse.quote(owner)}", None)
        _expect(status, (200,), f"looking up client {owner}")
        mappings = f"{base}/users/{account['id']}/role-mappings/clients/{owners[0]['id']}"
        status, have = call("GET", mappings, None)
        _expect(status, (200,), "reading role mappings")
        have_names = {r["name"] for r in have or []}
        missing = [name for name in wanted if name not in have_names]
        if not missing:
            continue
        status, available = call("GET", f"{mappings}/available", None)
        _expect(status, (200,), "listing assignable roles")
        roles = [r for r in available or [] if r["name"] in missing]
        _expect(call("POST", mappings, roles)[0], (204,), f"granting {owner} roles")
        changes += [f"{client_id} granted {owner}:{r['name']}" for r in roles]
    return changes


# ============================================================================ command line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    render_cmd = commands.add_parser("render", help="write the realm file Keycloak imports")
    render_cmd.add_argument("--out", required=True, type=Path)
    sync_cmd = commands.add_parser("sync", help="update an existing realm to match the settings")
    sync_cmd.add_argument("--url", required=True, help="Keycloak base URL (internal)")
    sync_cmd.add_argument("--update-smtp-password", action="store_true")
    args = parser.parse_args(argv)
    try:
        realm = render(os.environ)
        if args.command == "render":
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(realm, indent=2), encoding="utf-8")
            print(f"Rendered realm {realm['realm']!r} -> {args.out}")  # noqa: T201
            return 0
        user, password = os.environ.get("KEYCLOAK_ADMIN"), os.environ.get("KEYCLOAK_ADMIN_PASSWORD")
        if not user or not password:
            print("error: KEYCLOAK_ADMIN and KEYCLOAK_ADMIN_PASSWORD are required", file=sys.stderr)  # noqa: T201
            return 1
        token = admin_token(args.url, user, password)
        changes = sync(
            realm, http_request(args.url, token), update_smtp_password=args.update_smtp_password
        )
        print("\n".join(changes) if changes else "Realm already up to date.")  # noqa: T201
        return 0
    except RealmError as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201
        return 1


if __name__ == "__main__":
    sys.exit(main())
