"""Notes: the Tiptap allow-list, rendering, sanitizing and code highlighting (pure functions)."""

import re
from html.parser import HTMLParser
from typing import Any

import pytest

from app.modules.courses.notes import (
    MAX_DEPTH,
    MAX_IMAGES,
    NotesValidationError,
    render_html,
    sanitize,
    validate_doc,
)

IMAGE = "00000000-0000-4000-8000-000000000001"


def doc(*blocks: Any) -> dict[str, Any]:
    return {"type": "doc", "content": list(blocks)}


def para(*inline: Any) -> dict[str, Any]:
    return {"type": "paragraph", "content": list(inline)}


def text(value: str, *marks: dict[str, Any]) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": value}
    if marks:
        node["marks"] = list(marks)
    return node


def link(href: str) -> dict[str, Any]:
    # Tiptap's Link extension sends target/rel/class too.
    return {"type": "link", "attrs": {"href": href, "target": "_blank", "rel": None, "class": None}}


EVERYTHING = doc(
    {"type": "heading", "attrs": {"level": 2}, "content": [text("Two pointers")]},
    para(
        text("bold ", {"type": "bold"}),
        text("italic ", {"type": "italic"}),
        text("code", {"type": "code"}),
        {"type": "hardBreak"},
        text("docs", link("https://example.com/a?b=1&c=2")),
    ),
    {
        "type": "bulletList",
        "content": [{"type": "listItem", "content": [para(text("one"))]}],
    },
    {
        "type": "orderedList",
        "attrs": {"start": 3, "type": None},
        "content": [{"type": "listItem", "content": [para(text("three"))]}],
    },
    {"type": "blockquote", "content": [para(text("quoted"))]},
    {"type": "codeBlock", "attrs": {"language": "python"}, "content": [text("def f():\n    pass")]},
    {"type": "image", "attrs": {"file_id": IMAGE, "alt": "diagram"}},
)


def test_every_allowed_construct_validates_and_renders() -> None:
    assert [str(i) for i in validate_doc(EVERYTHING)] == [IMAGE]
    html = render_html(EVERYTHING)
    for fragment in [
        "<h2>Two pointers</h2>",
        "<strong>bold </strong>",
        "<em>italic </em>",
        "<code>code</code>",
        "<br>",
        '<a href="https://example.com/a?b=1&amp;c=2" target="_blank" '
        'rel="noopener noreferrer nofollow">docs</a>',
        "<ul><li><p>one</p></li></ul>",
        '<ol start="3"><li><p>three</p></li></ol>',
        "<blockquote><p>quoted</p></blockquote>",
        f'<img data-file-id="{IMAGE}" alt="diagram">',
    ]:
        assert fragment in html, fragment


def test_code_blocks_are_highlighted_with_classes() -> None:
    html = render_html(EVERYTHING)
    assert '<pre class="highlight" data-language="python"><code>' in html
    assert '<span class="k">def</span>' in html  # keyword token class
    assert '<span class="nf">f</span>' in html  # function name
    assert "style=" not in html  # class-based: no inline styles, no JS highlighter


def test_code_is_escaped_not_executed() -> None:
    html = render_html(doc({"type": "codeBlock", "content": [text("<script>alert(1)</script>")]}))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'data-language="plaintext"' in html


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (doc({"type": "script"}), "not allowed"),
        (doc({"type": "iframe", "attrs": {"src": "https://evil"}}), "not allowed"),
        (doc({"type": "horizontalRule"}), "not allowed"),
        (doc(para(text("x", link("javascript:alert(1)")))), "http(s)"),
        (doc(para(text("x", link("JaVaScRiPt:alert(1)")))), "http(s)"),
        (doc(para(text("x", link("data:text/html,<script>alert(1)</script>")))), "http(s)"),
        (doc(para(text("x", link("/relative")))), "http(s)"),
        (doc(para(text("x", link(" https://example.com")))), "http(s)"),
        (doc({"type": "paragraph", "attrs": {"onclick": "alert(1)"}}), "unexpected attrs"),
        (doc({"type": "image", "attrs": {"src": "https://evil/x.png"}}), "unexpected attrs"),
        (doc({"type": "image", "attrs": {"file_id": "not-a-uuid"}}), "file_id"),
        (doc({"type": "heading", "attrs": {"level": 1}}), "level"),
        (doc(para(text("x", {"type": "underline"}))), "mark"),
        (doc(para(text("x", {"type": "bold", "attrs": {"style": "x"}}))), "unexpected attrs"),
        (doc(para({"type": "text", "text": "x", "style": "color:red"})), "unexpected keys"),
        (doc({"type": "codeBlock", "attrs": {"language": "brainfuck"}}), "language"),
        (
            doc({"type": "codeBlock", "content": [text("x", {"type": "bold"})]}),
            "unexpected keys",
        ),
        (doc({"type": "bulletList", "content": []}), "must not be empty"),
        (doc({"type": "orderedList", "attrs": {"start": 0}, "content": []}), "start"),
        (doc(para(text(""))), "non-empty"),
        ({"type": "doc", "content": "<p>raw html</p>"}, "list"),
        ([], "object"),
    ],
)
def test_disallowed_content_is_rejected(bad: Any, message: str) -> None:
    with pytest.raises(NotesValidationError, match=re.escape(message)):
        validate_doc(bad)


def test_nesting_and_image_count_are_capped() -> None:
    nested: dict[str, Any] = para(text("deep"))
    for _ in range(MAX_DEPTH):
        nested = {"type": "blockquote", "content": [nested]}
    with pytest.raises(NotesValidationError, match="nested too deeply"):
        validate_doc(doc(nested))
    images = [{"type": "image", "attrs": {"file_id": IMAGE}}] * (MAX_IMAGES + 1)
    with pytest.raises(NotesValidationError, match="images"):
        validate_doc(doc(*images))


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def test_text_and_attributes_are_escaped() -> None:
    html = render_html(
        doc(
            para(text('<img src=x onerror="alert(1)">')),
            {"type": "image", "attrs": {"file_id": IMAGE, "alt": '" onerror="alert(1)'}},
        )
    )
    parser = _Tags()
    parser.feed(html)
    # The markup in the text stays text; the only element attributes are the allowed ones.
    assert [tag for tag, _ in parser.tags] == ["p", "img"]
    assert set(parser.tags[1][1]) == {"data-file-id", "alt"}
    assert "&lt;img src=x" in html


@pytest.mark.parametrize(
    "payload",
    [
        "<script>alert(1)</script>",
        '<a href="javascript:alert(1)">x</a>',
        '<p onclick="alert(1)">x</p>',
        '<img src="x" onerror="alert(1)">',
        '<iframe src="https://evil"></iframe>',
        '<span style="background:url(javascript:alert(1))">x</span>',
        '<svg onload="alert(1)"></svg>',
        '<span class="k evil">x</span>',
    ],
)
def test_sanitizer_strips_xss_even_if_rendering_let_it_through(payload: str) -> None:
    cleaned = sanitize(payload)
    lowered = cleaned.lower()
    for bad in ("<script", "javascript:", "onclick", "onerror", "onload", "<iframe", "<svg"):
        assert bad not in lowered, cleaned
    assert "style=" not in lowered
    assert "evil" not in lowered


def test_empty_notes_render_to_nothing() -> None:
    assert render_html(None) == ""
    assert render_html(doc()) == ""
