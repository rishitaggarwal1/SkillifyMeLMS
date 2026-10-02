"""`make seed-demo` (app/cli/seed_demo.py): the guards, the credentials file, and a full run
against the test database and the real dev Keycloak, twice (idempotent), plus --reset.

The full run uses its own email domain, so it never touches the demo logins of `make seed-demo`
on this machine (their passwords live in .secrets/demo-credentials.txt)."""

import argparse
import asyncio
import stat
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uuid_utils.compat import uuid7

from app.cli import seed_demo
from app.core.config import Settings
from app.modules.identity.keycloak_admin import KeycloakAdmin


def _args(*flags: str) -> argparse.Namespace:
    return seed_demo.parse_args(list(flags))


@pytest.mark.parametrize(
    ("environment", "flags", "refused"),
    [
        ("local", (), False),
        ("local", ("--reset",), False),
        ("local", ("--rotate-passwords",), False),
        ("staging", (), False),  # plain "ensure" runs on a server
        ("staging", ("--reset",), True),
        ("staging", ("--rotate-passwords",), True),
        ("staging", ("--reset", "--i-know-this-is-not-local"), False),
        ("test", ("--rotate-passwords",), True),
        ("production", (), True),
        ("production", ("--i-know-this-is-not-local",), True),
    ],
)
def test_refusals(
    settings: Settings, environment: str, flags: tuple[str, ...], refused: bool
) -> None:
    configured = settings.model_copy(update={"environment": environment})
    assert (seed_demo.refusal(configured, _args(*flags)) is not None) is refused


def test_generated_passwords_are_strong_and_distinct() -> None:
    passwords = {seed_demo.generate_password() for _ in range(50)}
    assert len(passwords) == 50
    for p in passwords:
        assert len(p) == 20
        assert any(c.islower() for c in p)
        assert any(c.isupper() for c in p)
        assert any(c.isdigit() for c in p)
        assert any(not c.isalnum() for c in p)


def test_credentials_file_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "demo-credentials.txt"
    passwords = {u.email: f"pw-{i}" for i, u in enumerate(seed_demo.USERS)}
    seed_demo.write_credentials(path, passwords, "https://portal.example.test")
    assert seed_demo.read_credentials(path) == passwords
    content = path.read_text(encoding="utf-8")
    assert "https://portal.example.test" in content
    assert content.startswith("# SkillifyMe demo logins")
    if sys.platform != "win32":  # Windows has no POSIX permission bits
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert seed_demo.read_credentials(tmp_path / "missing.txt") == {}


@pytest.fixture
async def demo_domain(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[Path]:
    """Demo users on a throwaway domain, a temporary credentials file; Keycloak users removed
    afterwards."""
    domain = f"seed-{uuid7().hex[-8:]}.test"
    monkeypatch.setattr(seed_demo, "DOMAIN", domain)
    path = tmp_path / "demo-credentials.txt"
    monkeypatch.setenv("DEMO_CREDENTIALS_FILE", str(path))
    yield path
    async with httpx.AsyncClient() as http:
        admin = KeycloakAdmin(http, settings)
        headers = await admin._auth_header()
        for user in seed_demo.USERS:
            found = await admin.find_user_by_email(user.email)
            if found:
                await http.delete(f"{admin.base}/users/{found['id']}", headers=headers)


async def _progress(owner: async_sessionmaker[AsyncSession], domain: str) -> dict[str, int]:
    async with owner() as s:
        rows = await s.execute(
            text(
                "SELECT u.full_name, e.progress_percent FROM enrollments e "
                "JOIN users u ON u.id = e.user_id JOIN courses c ON c.id = e.course_id "
                "WHERE c.slug = 'python-foundations' AND u.email LIKE :pattern"
            ),
            {"pattern": f"%@{domain}"},
        )
        return {name: int(pct) for name, pct in rows}


async def _submissions(owner: async_sessionmaker[AsyncSession], domain: str) -> tuple[int, int]:
    async with owner() as s:
        row = (
            await s.execute(
                text(
                    "SELECT count(*), count(g.id) FROM assignment_submissions sub "
                    "JOIN users u ON u.id = sub.user_id "
                    "LEFT JOIN assignment_grades g ON g.submission_id = sub.id "
                    "WHERE u.email LIKE :pattern"
                ),
                {"pattern": f"%@{domain}"},
            )
        ).one()
        return int(row[0]), int(row[1])


EXPECTED = {
    "Aarav Patel": 100, "Priya Sharma": 83, "Sneha Reddy": 66, "Ananya Iyer": 50,
    "Rohan Gupta": 33, "Vikram Singh": 16, "Kavya Nair": 0, "Arjun Mehta": 0,
}  # fmt: skip


async def test_full_run_is_idempotent_and_reset_restores(
    settings: Settings,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
    demo_domain: Path,
) -> None:
    lines: list[str] = []
    await seed_demo.run(settings, _args(), lines.append)
    domain = seed_demo.DOMAIN
    assert await _progress(owner_sessionmaker, domain) == EXPECTED
    assert await _submissions(owner_sessionmaker, domain) == (3, 1)
    first = await asyncio.to_thread(demo_domain.read_text, encoding="utf-8")
    passwords = seed_demo.read_credentials(demo_domain)
    assert len(passwords) == 12
    assert len(set(passwords.values())) == 12
    # Passwords never reach the output.
    assert not any(p in line for line in lines for p in passwords.values())

    await seed_demo.run(settings, _args(), lines.append)  # nothing changes on a rerun
    assert await asyncio.to_thread(demo_domain.read_text, encoding="utf-8") == first
    assert await _progress(owner_sessionmaker, domain) == EXPECTED
    assert await _submissions(owner_sessionmaker, domain) == (3, 1)
    async with owner_sessionmaker() as s:
        courses = await s.scalar(
            text("SELECT count(*) FROM courses WHERE slug = 'python-foundations'")
        )
    assert courses == 1

    # Someone used the demo: a student completed more. --reset puts the plan back.
    async with owner_sessionmaker() as s, s.begin():
        await s.execute(
            text(
                "UPDATE enrollments SET progress_percent = 100 FROM users u "
                "WHERE u.id = enrollments.user_id AND u.email = :email"
            ),
            {"email": f"demo.student8@{domain}"},
        )
    await seed_demo.run(settings, _args("--reset"), lines.append)
    assert await _progress(owner_sessionmaker, domain) == EXPECTED
    assert seed_demo.read_credentials(demo_domain) == passwords  # reset keeps the logins

    await seed_demo.run(settings, _args("--rotate-passwords"), lines.append)
    rotated = seed_demo.read_credentials(demo_domain)
    assert set(rotated) == set(passwords)
    assert not set(rotated.values()) & set(passwords.values())
