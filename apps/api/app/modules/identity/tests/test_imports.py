"""CSV student import: parsing rules, the background job (real Postgres + MinIO, fake Keycloak), the
upload API and the downloadable error report."""

import csv
import io
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from uuid_utils.compat import uuid7

from app.core.config import Settings
from app.modules.identity.imports import ImportFileError, parse_csv, run_import
from app.modules.identity.service import _csv_safe
from app.modules.identity.tests.conftest import OrgSetup
from tests.factories import Factory
from tests.fakes import EnqueueRecorder, FakeKeycloakAdmin

# ---------------------------------------------------------------------------- parsing


def _csv(*rows: str, bom: bool = False, crlf: bool = False) -> bytes:
    text = ("\r\n" if crlf else "\n").join(rows) + "\n"
    return (("﻿" if bom else "") + text).encode()


def test_parses_valid_rows_with_bom_crlf_and_extra_columns() -> None:
    parsed = parse_csv(
        _csv(
            "Roll No,Full Name,E-mail,Branch",
            "1,Asha Rao,ASHA@College.test,CSE",
            "",
            '2,"Rao, Vikram",vikram@college.test,ECE',
            bom=True,
            crlf=True,
        ),
        max_rows=100,
    )
    assert [(r.row_number, r.email, r.full_name) for r in parsed.rows] == [
        (2, "asha@college.test", "Asha Rao"),
        (4, "vikram@college.test", "Rao, Vikram"),
    ]
    assert parsed.errors == []
    assert parsed.total_rows == 2


def test_row_level_errors() -> None:
    parsed = parse_csv(
        _csv(
            "email,full_name",
            "ok@college.test,Okay",
            "not-an-email,Bad Email",
            "noname@college.test,",
            "OK@college.test,Duplicate Of Row 2",
            "extra@college.test,Too,Many",
            f"long@college.test,{'x' * 201}",
        ),
        max_rows=100,
    )
    assert [r.email for r in parsed.rows] == ["ok@college.test"]
    assert [(e.row_number, e.code) for e in parsed.errors] == [
        (3, "invalid_email"),
        (4, "missing_name"),
        (5, "duplicate_in_file"),
        (6, "malformed_row"),
        (7, "name_too_long"),
    ]
    assert parsed.errors[0].raw == {"email": "not-an-email", "full_name": "Bad Email"}
    assert parsed.total_rows == 6


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "empty_file"),
        (b"email,full_name\n", "empty_file"),
        (b"name,phone\nA,1\n", "invalid_header"),
        ("email,full_name\na@b.test,Ä\n".encode("latin-1"), "invalid_encoding"),
        (b"email,full_name\n" + b"a@b.test,x\n" * 6, "too_many_rows"),
    ],
)
def test_file_level_errors(data: bytes, code: str) -> None:
    with pytest.raises(ImportFileError) as excinfo:
        parse_csv(data, max_rows=5)
    assert excinfo.value.code == code


# ---------------------------------------------------------------------------- job


async def _upload(
    client: AsyncClient, setup: OrgSetup, data: bytes, *, batch: UUID | None = None
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/imports",
        headers=setup.h(setup.admin),
        files={"file": ("students.csv", data, "text/csv")},
        data={"batch_id": str(batch)} if batch else {},
    )
    assert response.status_code == 202, response.text
    body: dict[str, object] = response.json()
    return body


async def _run(app: FastAPI, settings: Settings, job: tuple[UUID, UUID, UUID]) -> None:
    job_id, org_id, user_id = job
    await run_import(
        sessionmaker=app.state.sessionmaker,
        storage=app.state.storage,
        idp=app.state.keycloak_admin,
        job_id=job_id,
        org_id=org_id,
        user_id=user_id,
        chunk_size=2,  # several chunks even for small files
        max_rows=settings.import_max_rows,
    )


async def test_import_job_end_to_end(
    app: FastAPI,
    settings: Settings,
    client: AsyncClient,
    org_setup: OrgSetup,
    factory: Factory,
    fake_idp: FakeKeycloakAdmin,
    enqueued: EnqueueRecorder,
) -> None:
    tag = uuid7().hex[-6:]
    elsewhere = await factory.member(await factory.org(), "student")  # exists in another org
    fake_idp.add_existing(elsewhere.email, elsewhere.keycloak_sub)
    fake_idp.add_existing(org_setup.student.email, org_setup.student.keycloak_sub)
    conflict = await factory.user()  # same email, different identity
    fake_idp.add_existing(conflict.email, f"kc-other-{tag}")
    data = _csv(
        "email,full_name",
        f"new1.{tag}@college.test,New One",
        f"new2.{tag}@college.test,New Two",
        f"{org_setup.student.email},Already In Batch",
        f"{elsewhere.email},From Elsewhere",
        f"{conflict.email},Identity Clash",
        "broken-email,Broken",
        f"new1.{tag}@college.test,Duplicate",
    )

    job = await _upload(client, org_setup, data, batch=org_setup.batch.id)
    [queued] = enqueued.jobs
    await _run(app, settings, queued)
    status = (
        await client.get(f"/api/v1/imports/{job['id']}", headers=org_setup.h(org_setup.admin))
    ).json()
    members = await client.get(
        "/api/v1/members",
        headers=org_setup.h(org_setup.admin),
        params={"batch_id": str(org_setup.batch.id), "limit": 100},
    )

    assert job["status"] == "queued"
    assert queued[0] == UUID(str(job["id"]))
    assert status["status"] == "completed_with_errors"
    assert (status["total_rows"], status["processed_rows"]) == (7, 7)
    assert (status["created_count"], status["skipped_count"], status["error_count"]) == (3, 1, 3)
    in_batch = {m["user"]["email"] for m in members.json()["items"]}
    assert {f"new1.{tag}@college.test", f"new2.{tag}@college.test", elsewhere.email} <= in_batch
    assert conflict.email not in in_batch
    # Setup emails only for brand-new accounts.
    assert sorted(fake_idp.setup_emails) == sorted(
        [fake_idp.users[f"new1.{tag}@college.test"], fake_idp.users[f"new2.{tag}@college.test"]]
    )

    report = await client.get(
        f"/api/v1/imports/{job['id']}/errors.csv", headers=org_setup.h(org_setup.admin)
    )
    rows = list(csv.DictReader(io.StringIO(report.text)))
    assert report.headers["content-type"].startswith("text/csv")
    assert [(r["row"], r["code"]) for r in rows] == [
        ("6", "email_in_use"),  # found in Keycloak, but the email belongs to another identity
        ("7", "invalid_email"),
        ("8", "duplicate_in_file"),
    ]


async def test_rerunning_a_job_is_idempotent(
    app: FastAPI,
    settings: Settings,
    client: AsyncClient,
    org_setup: OrgSetup,
    fake_idp: FakeKeycloakAdmin,
    enqueued: EnqueueRecorder,
) -> None:
    tag = uuid7().hex[-6:]
    first = await _upload(client, org_setup, _csv("email,full_name", f"a.{tag}@college.test,A"),
                          batch=org_setup.batch.id)  # fmt: skip
    await _run(app, settings, enqueued.jobs[-1])
    second = await _upload(client, org_setup, _csv("email,full_name", f"a.{tag}@college.test,A"),
                           batch=org_setup.batch.id)  # fmt: skip
    await _run(app, settings, enqueued.jobs[-1])
    h = org_setup.h(org_setup.admin)
    one = (await client.get(f"/api/v1/imports/{first['id']}", headers=h)).json()
    two = (await client.get(f"/api/v1/imports/{second['id']}", headers=h)).json()

    assert (one["created_count"], one["skipped_count"], one["status"]) == (1, 0, "succeeded")
    assert (two["created_count"], two["skipped_count"], two["status"]) == (0, 1, "succeeded")
    assert len(fake_idp.setup_emails) == 1


async def test_bad_file_fails_the_job(
    app: FastAPI,
    settings: Settings,
    client: AsyncClient,
    org_setup: OrgSetup,
    fake_idp: FakeKeycloakAdmin,
    enqueued: EnqueueRecorder,
) -> None:
    job = await _upload(client, org_setup, b"phone,city\n1,Pune\n")
    await _run(app, settings, enqueued.jobs[-1])
    status = (
        await client.get(f"/api/v1/imports/{job['id']}", headers=org_setup.h(org_setup.admin))
    ).json()
    assert status["status"] == "failed"
    assert "email" in status["error_message"]


async def test_email_failures_do_not_fail_the_import(
    app: FastAPI,
    settings: Settings,
    client: AsyncClient,
    org_setup: OrgSetup,
    fake_idp: FakeKeycloakAdmin,
    enqueued: EnqueueRecorder,
) -> None:
    fake_idp.fail_emails = True
    job = await _upload(
        client, org_setup, _csv("email,full_name", f"m.{uuid7().hex[-6:]}@college.test,M")
    )
    await _run(app, settings, enqueued.jobs[-1])
    status = (
        await client.get(f"/api/v1/imports/{job['id']}", headers=org_setup.h(org_setup.admin))
    ).json()
    assert (status["status"], status["created_count"]) == ("succeeded", 1)


# ---------------------------------------------------------------------------- upload validation


@pytest.mark.parametrize(
    ("files", "code"),
    [
        ({"file": ("s.csv", b"", "text/csv")}, "empty_file"),
        ({"file": ("s.pdf", b"%PDF-1.7", "application/pdf")}, "unsupported_file_type"),
    ],
)
async def test_upload_validation(
    client: AsyncClient,
    org_setup: OrgSetup,
    enqueued: EnqueueRecorder,
    files: dict[str, tuple[str, bytes, str]],
    code: str,
) -> None:
    response = await client.post(
        "/api/v1/imports", headers=org_setup.h(org_setup.admin), files=files
    )
    assert (response.status_code, response.json()["error"]["code"]) == (422, code)
    assert enqueued.jobs == []


async def test_upload_too_large(
    app: FastAPI, client: AsyncClient, org_setup: OrgSetup, enqueued: EnqueueRecorder
) -> None:
    limit = app.state.settings.import_max_bytes
    big = b"email,full_name\n" + b"a" * limit
    response = await client.post(
        "/api/v1/imports",
        headers=org_setup.h(org_setup.admin),
        files={"file": ("big.csv", big, "text/csv")},
    )
    assert (response.status_code, response.json()["error"]["code"]) == (422, "file_too_large")


def test_error_report_escapes_formulas() -> None:
    assert _csv_safe('=HYPERLINK("http://evil")') == '\'=HYPERLINK("http://evil")'
    assert _csv_safe("+1") == "'+1"
    assert _csv_safe("@SUM(A1)") == "'@SUM(A1)"
    assert _csv_safe("normal@college.test") == "normal@college.test"
