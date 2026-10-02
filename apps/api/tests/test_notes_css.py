"""The web app's copy of the notes code-highlighting stylesheet matches what the API renders."""

from pathlib import Path

from app.cli.export_notes_css import css

COMMITTED = Path(__file__).resolve().parents[2] / "web" / "src" / "styles" / "notes-code.css"


def test_committed_stylesheet_is_current() -> None:
    assert COMMITTED.read_text(encoding="utf-8") == css(), "run `make gen-api` and commit the CSS"


def test_stylesheet_targets_rendered_code_blocks() -> None:
    generated = css()
    assert "pre.highlight .k " in generated  # keyword token class the renderer emits
