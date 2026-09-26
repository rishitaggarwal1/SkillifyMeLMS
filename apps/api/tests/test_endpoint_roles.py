"""Role checks for every /api/v1 endpoint.

Each route is called as seven callers: anonymous, student, lab_author, instructor and org_admin of
org A, the org_admin of an unrelated org B (acting in B), and a platform admin (acting in A).

Expected: allowed & same org -> 2xx; insufficient permission -> 403; anonymous -> 401. Org B's
admin gets 2xx on collection routes (their own org's data), 404 on routes naming org A's resources,
and 403 where org admins aren't allowed at all.

`test_every_route_is_in_the_matrix` fails when a new endpoint is added without a row here.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from uuid_utils.compat import uuid7

from app.modules.identity.models import Organization, User
from tests.factories import Factory
from tests.fakes import EnqueueRecorder, FakeKeycloakAdmin
from tests.fixtures import AuthHeaders

ROLES = (
    "anonymous",
    "student",
    "lab_author",
    "instructor",
    "org_admin",
    "other_admin",
    "platform_admin",
)
EVERYONE = {"student", "lab_author", "instructor", "org_admin", "other_admin", "platform_admin"}
ADMINS = {"org_admin", "other_admin", "platform_admin"}
STAFF_READ = {"instructor", "org_admin", "other_admin", "platform_admin"}
PLATFORM = {"platform_admin"}


@dataclass
class World:
    factory: Factory
    org: Organization
    users: dict[str, User]
    other_org: Organization


Request = tuple[str, dict[str, Any]]  # (path, httpx request kwargs)
Builder = Callable[[World], Awaitable[Request]]


async def _static(path: str, **kwargs: Any) -> Request:
    return path, kwargs


def at(path: str, **kwargs: Any) -> Builder:
    async def build(_: World) -> Request:
        return path, kwargs

    return build


async def _new_org(w: World) -> Request:
    return f"/api/v1/organizations/{(await w.factory.org()).id}", {}


async def _batch(w: World) -> Request:
    return f"/api/v1/batches/{(await w.factory.batch(w.org)).id}", {}


async def _batch_members(w: World) -> Request:
    batch = await w.factory.batch(w.org)
    return f"/api/v1/batches/{batch.id}/members", {}


async def _add_batch_member(w: World) -> Request:
    batch = await w.factory.batch(w.org)
    student = await w.factory.member(w.org, "student")
    return f"/api/v1/batches/{batch.id}/members", {"json": {"user_ids": [str(student.id)]}}


async def _remove_batch_member(w: World) -> Request:
    batch = await w.factory.batch(w.org)
    student = await w.factory.member(w.org, "student")
    await w.factory.add_to_batch(batch, student)
    return f"/api/v1/batches/{batch.id}/members/{student.id}", {}


async def _member(w: World) -> Request:
    return f"/api/v1/members/{(await w.factory.member(w.org, 'student')).id}", {}


async def _patch_member(w: World) -> Request:
    path, _ = await _member(w)
    return path, {"json": {"roles": ["student", "instructor"]}}


async def _invitation(w: World) -> Request:
    user = await w.factory.user(status="invited")
    await w.factory.member(w.org, "student", user=user)
    inv = await w.factory.invitation(w.org, user=user)
    return f"/api/v1/invitations/{inv.id}", {}


async def _resend(w: World) -> Request:
    path, _ = await _invitation(w)
    return f"{path}/resend", {}


async def _import(w: World) -> Request:
    return f"/api/v1/imports/{(await w.factory.import_job(w.org)).id}", {}


async def _import_errors(w: World) -> Request:
    path, _ = await _import(w)
    return f"{path}/errors.csv", {}


def _invite_body(_: World) -> Awaitable[Request]:
    return _static(
        "/api/v1/invitations",
        json={"email": f"m-{uuid7().hex[-8:]}@college.test", "roles": ["student"]},
    )


def _org_body(_: World) -> Awaitable[Request]:
    return _static("/api/v1/organizations", json={"name": "M", "slug": f"m-{uuid7().hex[-10:]}"})


def _batch_body(_: World) -> Awaitable[Request]:
    return _static("/api/v1/batches", json={"name": f"B {uuid7().hex[-8:]}"})


def _import_body(_: World) -> Awaitable[Request]:
    return _static(
        "/api/v1/imports",
        files={"file": ("s.csv", b"email,full_name\na@college.test,A\n", "text/csv")},
    )


@dataclass(frozen=True)
class Route:
    method: str
    template: str  # the route's path template (matched against the app's routes)
    allowed: set[str]
    build: Builder
    names_resource: bool = False  # True: org B's admin gets 404 instead of 2xx


MATRIX = [
    Route("GET", "/api/v1/me", EVERYONE, at("/api/v1/me")),
    # --- Organizations: platform admins only.
    Route("POST", "/api/v1/organizations", PLATFORM, _org_body),
    Route("GET", "/api/v1/organizations", PLATFORM, at("/api/v1/organizations")),
    Route("GET", "/api/v1/organizations/current", EVERYONE, at("/api/v1/organizations/current")),
    Route("GET", "/api/v1/organizations/{organization_id}", PLATFORM, _new_org),
    Route(
        "PATCH",
        "/api/v1/organizations/{organization_id}",
        PLATFORM,
        lambda w: _with_json(_new_org(w), {"name": "Renamed"}),
    ),
    Route("DELETE", "/api/v1/organizations/{organization_id}", PLATFORM, _new_org),
    # --- batches
    Route("POST", "/api/v1/batches", ADMINS, _batch_body),
    Route("GET", "/api/v1/batches", STAFF_READ, at("/api/v1/batches")),
    Route("GET", "/api/v1/batches/{batch_id}", STAFF_READ, _batch, names_resource=True),
    Route(
        "PATCH",
        "/api/v1/batches/{batch_id}",
        ADMINS,
        lambda w: _with_json(_batch(w), {"description": "x"}),
        names_resource=True,
    ),
    Route("DELETE", "/api/v1/batches/{batch_id}", ADMINS, _batch, names_resource=True),
    Route(
        "GET", "/api/v1/batches/{batch_id}/members", STAFF_READ, _batch_members, names_resource=True
    ),
    Route(
        "POST", "/api/v1/batches/{batch_id}/members", ADMINS, _add_batch_member, names_resource=True
    ),
    Route(
        "DELETE",
        "/api/v1/batches/{batch_id}/members/{user_id}",
        ADMINS,
        _remove_batch_member,
        names_resource=True,
    ),
    # --- members
    Route("GET", "/api/v1/members", STAFF_READ, at("/api/v1/members")),
    Route("GET", "/api/v1/members/{user_id}", STAFF_READ, _member, names_resource=True),
    Route("PATCH", "/api/v1/members/{user_id}", ADMINS, _patch_member, names_resource=True),
    Route("DELETE", "/api/v1/members/{user_id}", ADMINS, _member, names_resource=True),
    # --- invitations
    Route("POST", "/api/v1/invitations", ADMINS, _invite_body),
    Route("GET", "/api/v1/invitations", ADMINS, at("/api/v1/invitations")),
    Route(
        "DELETE", "/api/v1/invitations/{invitation_id}", ADMINS, _invitation, names_resource=True
    ),
    Route(
        "POST", "/api/v1/invitations/{invitation_id}/resend", ADMINS, _resend, names_resource=True
    ),
    # --- CSV imports.
    Route("POST", "/api/v1/imports", ADMINS, _import_body),
    Route("GET", "/api/v1/imports", ADMINS, at("/api/v1/imports")),
    Route("GET", "/api/v1/imports/{job_id}", ADMINS, _import, names_resource=True),
    Route(
        "GET", "/api/v1/imports/{job_id}/errors.csv", ADMINS, _import_errors, names_resource=True
    ),
    # --- audit
    Route("GET", "/api/v1/audit-log", ADMINS, at("/api/v1/audit-log")),
]


async def _with_json(request: Awaitable[Request], body: dict[str, Any]) -> Request:
    path, kwargs = await request
    return path, {**kwargs, "json": body}


def expected_status(route: Route, role: str) -> str:
    if role == "anonymous":
        return "401"
    if role not in route.allowed:
        return "403"
    if role == "other_admin" and route.names_resource:
        return "404"
    return "2xx"


@pytest.fixture
async def world(factory: Factory) -> World:
    org, other_org = await factory.org(), await factory.org()
    users = {
        "student": await factory.member(org, "student"),
        "lab_author": await factory.member(org, "lab_author"),
        "instructor": await factory.member(org, "instructor"),
        "org_admin": await factory.member(org, "org_admin"),
        "other_admin": await factory.member(other_org, "org_admin"),
        "platform_admin": await factory.user(),
    }
    return World(factory=factory, org=org, users=users, other_org=other_org)


def _headers(world: World, role: str, auth_headers: AuthHeaders) -> dict[str, str]:
    if role == "anonymous":
        return {}
    if role == "other_admin":
        return auth_headers(world.users[role], org=world.other_org.id)
    return auth_headers(
        world.users[role], org=world.org.id, platform_admin=role == "platform_admin"
    )


@pytest.mark.usefixtures("fake_idp", "enqueued")
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("route", MATRIX, ids=[f"{r.method} {r.template}" for r in MATRIX])
async def test_role_matrix(
    client: AsyncClient, world: World, auth_headers: AuthHeaders, route: Route, role: str
) -> None:
    path, kwargs = await route.build(world)
    response = await client.request(
        route.method, path, headers=_headers(world, role, auth_headers), **kwargs
    )
    expected = expected_status(route, role)
    actual = "2xx" if 200 <= response.status_code < 300 else str(response.status_code)
    assert actual == expected, (route.method, path, role, response.text[:300])


def test_every_route_is_in_the_matrix(app: FastAPI) -> None:
    declared = {(r.method, r.template) for r in MATRIX}
    # From the OpenAPI spec: the public API surface (FastAPI includes routers lazily, so
    # app.routes does not list them).
    actual = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/v1")
        for method in operations
    }
    assert actual - declared == set(), "add these endpoints to MATRIX with their allowed roles"
    assert declared - actual == set(), "MATRIX lists endpoints that no longer exist"


# Silence "unused" for fixtures only used via usefixtures.
_ = (FakeKeycloakAdmin, EnqueueRecorder)
