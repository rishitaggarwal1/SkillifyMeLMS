"""Seed-only code stays out of the running app.

`app/cli` holds operator tools (seeds, exports). Nothing the API or the workers load (app/api
and app/modules, apart from their tests) may import them; in particular
`app.cli.demo_progress`, which writes watch progress directly. Its guard refuses to run outside
the demo seed unless ENVIRONMENT is local."""

import ast
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import demo_assessments, demo_progress

APP = Path(__file__).resolve().parents[1] / "app"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_app_code_never_imports_cli_tools() -> None:
    offenders = [
        f"{path.relative_to(APP.parent)}: {name}"
        for folder in ("api", "modules")
        for path in sorted((APP / folder).rglob("*.py"))
        if "tests" not in path.relative_to(APP).parts  # module tests aren't loaded by the app
        for name in sorted(_imports(path))
        if name == "app.cli" or name.startswith("app.cli.")
    ]
    assert offenders == [], "app/api and app/modules must not import app/cli:\n" + "\n".join(
        offenders
    )


class _Staging:
    environment = "staging"


async def test_mark_video_watched_refuses_outside_the_seed(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    monkeypatch.setattr(demo_progress, "get_settings", _Staging)
    with pytest.raises(demo_progress.SeedOnlyError):
        await demo_progress.mark_video_watched(db_session, uuid4(), uuid4())
    # Inside the seed's context the guard lets it through (here it then finds no enrollment).
    with demo_progress.seed_context(), pytest.raises(LookupError):
        await demo_progress.mark_video_watched(db_session, uuid4(), uuid4())


def test_watched_bitmap_covers_every_segment() -> None:
    assert demo_progress.watched_bitmap(12) == bytes([0b11100000])  # 3 segments
    assert demo_progress.watched_bitmap(40) == bytes([0xFF])  # 8 segments
    assert demo_progress.watched_bitmap(41) == bytes([0xFF, 0b10000000])  # 9 segments


async def test_expiry_preparation_refuses_outside_seed(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    monkeypatch.setattr(demo_progress, "get_settings", _Staging)
    with pytest.raises(demo_progress.SeedOnlyError):
        await demo_assessments.prepare_expired_attempt(db_session, uuid4(), uuid4())
