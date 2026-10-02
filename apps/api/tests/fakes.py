"""In-memory stand-ins for external services (Keycloak admin API, Celery enqueueing).

Real Keycloak is exercised separately in tests/test_keycloak_integration.py."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from uuid_utils.compat import uuid7

from app.modules.identity.keycloak_admin import KeycloakAdminError, NewUser


@dataclass
class FakeKeycloakAdmin:
    users: dict[str, str] = field(default_factory=dict)  # email -> keycloak id
    setup_emails: list[str] = field(default_factory=list)  # keycloak ids emailed
    disabled: set[str] = field(default_factory=set)  # keycloak ids that can't sign in
    fail_emails: bool = False
    fail_admin: bool = False  # set_user_enabled raises (Keycloak unreachable)

    def reset(self) -> None:
        self.users.clear()
        self.setup_emails.clear()
        self.disabled.clear()
        self.fail_emails = False
        self.fail_admin = False

    def add_existing(self, email: str, keycloak_id: str) -> None:
        self.users[email.lower()] = keycloak_id

    async def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        kc_id = self.users.get(email.lower())
        return {"id": kc_id, "email": email.lower()} if kc_id else None

    async def ensure_users(self, users: Sequence[NewUser]) -> dict[str, str]:
        for u in users:
            self.users.setdefault(u.email.lower(), f"kc-{uuid7().hex}")
        return {u.email.lower(): self.users[u.email.lower()] for u in users}

    async def send_setup_email(self, keycloak_id: str) -> None:
        if self.fail_emails:
            msg = "smtp down"
            raise ConnectionError(msg)
        self.setup_emails.append(keycloak_id)

    async def set_user_enabled(self, keycloak_id: str, *, enabled: bool) -> None:
        if self.fail_admin:
            msg = "keycloak down"
            raise KeycloakAdminError(msg)
        if enabled:
            self.disabled.discard(keycloak_id)
        else:
            self.disabled.add(keycloak_id)


@dataclass
class EnqueueRecorder:
    jobs: list[tuple[UUID, UUID, UUID]] = field(default_factory=list)

    def __call__(self, job_id: UUID, org_id: UUID, user_id: UUID) -> None:
        self.jobs.append((job_id, org_id, user_id))


@dataclass
class RecordingJobQueue:
    """Records background jobs instead of sending them to Celery; tests run them with
    `tests.jobs.run_jobs`."""

    sent: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    def send(self, task: str, *args: str) -> None:
        self.sent.append((task, args))

    def take(self) -> list[tuple[str, tuple[str, ...]]]:
        jobs, self.sent = self.sent, []
        return jobs
