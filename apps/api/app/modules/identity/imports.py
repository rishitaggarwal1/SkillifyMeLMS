"""Bulk student import from CSV (runs in a Celery worker; see tasks.py).

File format: UTF-8 CSV (a BOM, as saved by Excel, is fine; CRLF or LF line endings). The header
row must contain an email column and a name column (common spellings accepted, case-insensitive);
other columns are ignored. Each data row becomes a student in the organization (and the job's
batch).

Outcome per row:
- created: the person was added to the organization and/or the batch
- skipped: already a member (and already in the batch)
- error:   recorded in import_job_errors (downloadable as CSV) with a machine code:
           invalid_email, missing_name, name_too_long, duplicate_in_file, malformed_row,
           email_in_use (the email belongs to a different identity)

The job is idempotent: re-running it only adds what is missing.
"""

import csv
import io
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.core.storage import ObjectStorage
from app.db.tenancy import set_tenant_context
from app.modules.audit import service as audit
from app.modules.audit.service import AuditActor
from app.modules.identity import events
from app.modules.identity.keycloak_admin import IdentityProviderAdmin, NewUser
from app.modules.identity.models import ImportJobStatus, OrgRole, UserStatus
from app.modules.identity.repository import (
    BatchMemberRepository,
    EnsureUser,
    ImportJobRepository,
    MembershipRepository,
    UserRepository,
)

logger = get_logger(__name__)

EMAIL_HEADERS = frozenset({"email", "email address", "e mail", "email id", "mail"})
NAME_HEADERS = frozenset({"full name", "name", "student name", "fullname"})
MAX_NAME_LENGTH = 200


@dataclass(frozen=True, slots=True)
class ImportRow:
    row_number: int  # physical line in the file (header = 1), as spreadsheets show it
    email: str
    full_name: str


@dataclass(frozen=True, slots=True)
class RowError:
    row_number: int
    code: str
    message: str
    raw: dict[str, str]


class ImportFileError(Exception):
    """The file as a whole can't be processed (the job fails with this message)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ParsedImport:
    rows: list[ImportRow] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    total_rows: int = 0


def _normalize_header(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())


def _validate_row(
    record: list[str],
    line: int,
    *,
    columns: int,
    email_idx: int,
    name_idx: int,
    seen: set[str],
) -> ImportRow | RowError:
    email = record[email_idx].strip() if email_idx < len(record) else ""
    name = record[name_idx].strip() if name_idx < len(record) else ""
    raw = {"email": email, "full_name": name}
    if len(record) != columns:
        return RowError(
            line, "malformed_row", f"Expected {columns} columns, found {len(record)}.", raw
        )
    try:
        email = validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return RowError(line, "invalid_email", "The email address is invalid.", raw)
    if not name:
        return RowError(line, "missing_name", "The name is missing.", raw)
    if len(name) > MAX_NAME_LENGTH:
        return RowError(
            line, "name_too_long", f"Names are limited to {MAX_NAME_LENGTH} characters.", raw
        )
    if email in seen:
        return RowError(
            line,
            "duplicate_in_file",
            "This email appears earlier in the file; this row was ignored.",
            raw,
        )
    return ImportRow(line, email, name)


def parse_csv(data: bytes, *, max_rows: int) -> ParsedImport:
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportFileError(
            "invalid_encoding", "The file is not UTF-8. Save it as 'CSV UTF-8' and try again."
        ) from exc
    reader = csv.reader(io.StringIO(content, newline=""))
    parsed = ParsedImport()
    try:
        header = next(reader, None)
        if header is None or not any(h.strip() for h in header):
            raise ImportFileError("empty_file", "The file is empty.")
        normalized = [_normalize_header(h) for h in header]
        email_idx = next((i for i, h in enumerate(normalized) if h in EMAIL_HEADERS), None)
        name_idx = next((i for i, h in enumerate(normalized) if h in NAME_HEADERS), None)
        if email_idx is None or name_idx is None:
            raise ImportFileError(
                "invalid_header", "The first row must have 'email' and 'full_name' columns."
            )
        seen: set[str] = set()
        for record in reader:
            if not any(cell.strip() for cell in record):
                continue  # blank line
            parsed.total_rows += 1
            if parsed.total_rows > max_rows:
                raise ImportFileError(
                    "too_many_rows", f"The file has more than {max_rows} rows; split it up."
                )
            outcome = _validate_row(
                record,
                reader.line_num,
                columns=len(header),
                email_idx=email_idx,
                name_idx=name_idx,
                seen=seen,
            )
            if isinstance(outcome, RowError):
                parsed.errors.append(outcome)
            else:
                seen.add(outcome.email)
                parsed.rows.append(outcome)
    except csv.Error as exc:
        raise ImportFileError("malformed_file", f"The file is not valid CSV: {exc}") from exc
    if parsed.total_rows == 0:
        raise ImportFileError("empty_file", "The file has a header but no rows.")
    return parsed


def _chunks(rows: Sequence[ImportRow], size: int) -> Iterator[Sequence[ImportRow]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _error_rows(org_id: UUID, job_id: UUID, errors: Sequence[RowError]) -> list[dict[str, Any]]:
    return [
        {
            "import_job_id": job_id,
            "organization_id": org_id,
            "row_number": e.row_number,
            "code": e.code,
            "message": e.message,
            "raw": e.raw,
        }
        for e in errors
    ]


@dataclass
class _ChunkResult:
    created: int = 0
    skipped: int = 0
    errors: list[RowError] = field(default_factory=list)
    to_email: list[str] = field(default_factory=list)  # keycloak ids needing a setup email


async def _process_chunk(
    session: AsyncSession,
    *,
    org_id: UUID,
    batch_id: UUID | None,
    actor_user_id: UUID,
    chunk: Sequence[ImportRow],
    keycloak_ids: dict[str, str],
) -> _ChunkResult:
    result = _ChunkResult()
    ensured = await UserRepository(session).ensure_many(
        [EnsureUser(keycloak_ids[r.email], r.email, r.full_name) for r in chunk]
    )
    by_sub = {e.keycloak_sub: e for e in ensured}
    row_user: dict[int, UUID] = {}
    for row in chunk:
        e = by_sub[keycloak_ids[row.email]]
        if e.email_conflict or e.user_id is None:
            result.errors.append(RowError(
                row.row_number, "email_in_use", "This email is linked to another account.",
                {"email": row.email, "full_name": row.full_name},
            ))  # fmt: skip
        else:
            row_user[row.row_number] = e.user_id
    user_ids = list(dict.fromkeys(row_user.values()))

    memberships = MembershipRepository(session)
    roles = await memberships.roles_for_users(org_id, user_ids)
    new_members = {uid for uid in user_ids if not roles[uid]}
    for uid in new_members:
        await memberships.add(
            user_id=uid, organization_id=org_id, role=OrgRole.STUDENT, created_by=actor_user_id
        )
    added_to_batch: set[UUID] = set()
    if batch_id is not None and user_ids:
        added = await BatchMemberRepository(session).add_many(
            batch_id=batch_id, organization_id=org_id, user_ids=user_ids, added_by=actor_user_id
        )
        events.batch_members_added(
            session, organization_id=org_id, batch_id=batch_id, user_ids=added,
            actor_user_id=actor_user_id, reason="import",
        )  # fmt: skip
        added_to_batch = set(added)
    for uid in user_ids:
        if uid in new_members or uid in added_to_batch:
            result.created += 1
        else:
            result.skipped += 1
    status_by_user = {e.user_id: e.status for e in ensured if e.user_id}
    for row in chunk:
        row_uid = row_user.get(row.row_number)
        if (
            row_uid is not None
            and row_uid in new_members
            and status_by_user.get(row_uid) == UserStatus.INVITED
        ):
            result.to_email.append(keycloak_ids[row.email])
    return result


async def run_import(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    idp: IdentityProviderAdmin,
    job_id: UUID,
    org_id: UUID,
    user_id: UUID,
    chunk_size: int,
    max_rows: int,
    send_emails: bool = True,
) -> None:
    """Process one import job as the admin who started it (their RLS context applies)."""
    log = logger.bind(import_job_id=str(job_id), organization_id=str(org_id))
    actor = AuditActor(user_id=user_id, organization_id=org_id)

    async with sessionmaker() as session, session.begin():
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)
        repo = ImportJobRepository(session)
        job = await repo.get(job_id)
        if job is None or job.status not in (ImportJobStatus.QUEUED, ImportJobStatus.RUNNING):
            log.info("import_skipped", reason="missing_or_finished")
            return
        batch_id, file_key = job.batch_id, job.file_key
        await repo.update(job_id, {"status": ImportJobStatus.RUNNING,
                                   "started_at": datetime.now(UTC)})  # fmt: skip

    async def fail(message: str) -> None:
        async with sessionmaker() as session, session.begin():
            await set_tenant_context(session, organization_id=org_id, user_id=user_id)
            await ImportJobRepository(session).update(
                job_id,
                {"status": ImportJobStatus.FAILED, "error_message": message[:1000],
                 "finished_at": datetime.now(UTC)},
            )  # fmt: skip
            await audit.record(session, actor, action="import.failed", target_type="import_job",
                               target_id=job_id, after={"error": message})  # fmt: skip

    try:
        parsed = parse_csv(await storage.aget_bytes(file_key), max_rows=max_rows)
    except ImportFileError as exc:
        log.info("import_file_rejected", code=exc.code)
        await fail(exc.message)
        return

    async with sessionmaker() as session, session.begin():
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)
        repo = ImportJobRepository(session)
        await repo.add_errors(_error_rows(org_id, job_id, parsed.errors))
        await repo.update(job_id, {"total_rows": parsed.total_rows,
                                   "processed_rows": len(parsed.errors),
                                   "error_count": len(parsed.errors),
                                   "created_count": 0, "skipped_count": 0})  # fmt: skip

    totals = {"processed": len(parsed.errors), "created": 0, "skipped": 0,
              "errors": len(parsed.errors)}  # fmt: skip
    try:
        for chunk in _chunks(parsed.rows, chunk_size):
            # Keycloak first (outside the DB transaction): creates missing accounts in one call.
            keycloak_ids = await idp.ensure_users([NewUser(r.email, r.full_name) for r in chunk])
            async with sessionmaker() as session, session.begin():
                await set_tenant_context(session, organization_id=org_id, user_id=user_id)
                result = await _process_chunk(
                    session, org_id=org_id, batch_id=batch_id, actor_user_id=user_id,
                    chunk=chunk, keycloak_ids=keycloak_ids,
                )  # fmt: skip
                repo = ImportJobRepository(session)
                await repo.add_errors(_error_rows(org_id, job_id, result.errors))
                totals["processed"] += len(chunk)
                totals["created"] += result.created
                totals["skipped"] += result.skipped
                totals["errors"] += len(result.errors)
                await repo.update(job_id, {"processed_rows": totals["processed"],
                                           "created_count": totals["created"],
                                           "skipped_count": totals["skipped"],
                                           "error_count": totals["errors"]})  # fmt: skip
            if send_emails:
                for keycloak_id in result.to_email:
                    try:
                        await idp.send_setup_email(keycloak_id)
                    except Exception:  # an email failure must not fail the import
                        log.warning("import_setup_email_failed", keycloak_id=keycloak_id)
    except Exception as exc:
        log.exception("import_failed")
        await fail(f"The import stopped unexpectedly: {type(exc).__name__}")
        raise

    await _finish(sessionmaker, actor, job_id, totals)
    log.info("import_completed", **totals)


async def _finish(
    sessionmaker: async_sessionmaker[AsyncSession],
    actor: AuditActor,
    job_id: UUID,
    totals: dict[str, int],
) -> None:
    status = (
        ImportJobStatus.SUCCEEDED
        if totals["errors"] == 0
        else ImportJobStatus.COMPLETED_WITH_ERRORS
    )
    async with sessionmaker() as session, session.begin():
        await set_tenant_context(
            session, organization_id=actor.organization_id, user_id=actor.user_id
        )
        await ImportJobRepository(session).update(
            job_id, {"status": status, "finished_at": datetime.now(UTC)}
        )
        await audit.record(session, actor, action="import.completed", target_type="import_job",
                           target_id=job_id, after={"status": status, **totals})  # fmt: skip
