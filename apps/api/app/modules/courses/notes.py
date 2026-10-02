"""Notes lessons: Tiptap JSON validation (strict allow-list) and server-side HTML rendering.

Defense in depth against XSS:
1. `validate_doc` rejects anything outside the allow-list: unknown nodes, marks or attributes,
   non-http(s) links, images that aren't one of our uploaded files.
2. `render_html` builds HTML from the validated tree only, escaping every text and attribute.
3. The result is sanitized again by nh3 (ammonia) with an equally narrow policy.

Code blocks are highlighted by Pygments with class-based output (`pre.highlight span.k` ...),
so no highlighting JavaScript runs on phones. Rendering happens at publish time (into the version
snapshot); draft previews render on demand.

Allowed document shape (Tiptap/ProseMirror JSON):

    doc          -> block*
    block        =  paragraph | heading(level 2-4) | bulletList | orderedList(start)
                    | codeBlock(language) | blockquote | image(file_id, alt)
    listItem     -> block+
    paragraph, heading -> inline*
    inline       =  text(marks: bold | italic | code | link(href http/https)) | hardBreak
    codeBlock    -> text* (no marks)
"""

from collections.abc import Callable, Iterator, Mapping
from html import escape
from typing import Any, NoReturn
from urllib.parse import urlsplit
from uuid import UUID

import nh3
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.token import STANDARD_TYPES

MAX_DEPTH = 16
MAX_IMAGES = 50
MAX_ALT = 300
MAX_HREF = 2000
MAX_LIST_START = 10_000

# Tiptap language name -> Pygments lexer alias. Anything else is rejected by validation.
LANGUAGES: Mapping[str, str] = {
    "plaintext": "text",
    "bash": "bash",
    "c": "c",
    "cpp": "cpp",
    "csharp": "csharp",
    "css": "css",
    "go": "go",
    "html": "html",
    "java": "java",
    "javascript": "javascript",
    "json": "json",
    "kotlin": "kotlin",
    "python": "python",
    "rust": "rust",
    "sql": "sql",
    "typescript": "typescript",
}

BLOCKS = {"paragraph", "heading", "bulletList", "orderedList", "codeBlock", "blockquote", "image"}
INLINE = {"text", "hardBreak"}
MARKS = {"bold", "italic", "code", "link"}
# Tiptap's Link extension sends these alongside href; we ignore their values and render our own.
LINK_EXTRA_ATTRS = {"target", "rel", "class"}


class NotesValidationError(ValueError):
    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


# ============================================================================ validation


def validate_doc(doc: Any) -> list[UUID]:
    """Validate a Tiptap document against the allow-list. Returns the image file ids it uses, in
    document order, so the caller can check they are this org's ready images."""
    images: list[UUID] = []
    _node(doc, "", {"doc"}, 0, images)
    if len(images) > MAX_IMAGES:
        raise NotesValidationError("doc", f"at most {MAX_IMAGES} images are allowed")
    return images


def _fail(path: str, message: str) -> NoReturn:
    raise NotesValidationError(path, message)


def _keys(node: dict[str, Any], path: str, allowed: set[str]) -> None:
    extra = set(node) - allowed
    if extra:
        _fail(path, f"unexpected keys {sorted(extra)}")


def _attrs(node: dict[str, Any], path: str, allowed: set[str]) -> dict[str, Any]:
    attrs = node.get("attrs", {})
    if attrs is None:
        return {}
    if not isinstance(attrs, dict):
        _fail(path, "attrs must be an object")
    extra = set(attrs) - allowed
    if extra:
        _fail(path, f"unexpected attrs {sorted(extra)}")
    return attrs


def _children(node: dict[str, Any], path: str, *, required: bool) -> list[Any]:
    content = node.get("content", [])
    if not isinstance(content, list):
        _fail(path, "content must be a list")
    if required and not content:
        _fail(path, "content must not be empty")
    return content


# A checker validates one node's own keys and attrs and returns its children and the node types
# they may be, or None for a leaf.
Children = tuple[list[Any], set[str]] | None


def _doc(node: dict[str, Any], path: str, _: list[UUID]) -> Children:
    _keys(node, path, {"type", "content"})
    return _children(node, path, required=False), BLOCKS


def _paragraph(node: dict[str, Any], path: str, _: list[UUID]) -> Children:
    _keys(node, path, {"type", "attrs", "content"})
    _attrs(node, path, set())
    return _children(node, path, required=False), INLINE


def _heading(node: dict[str, Any], path: str, _: list[UUID]) -> Children:
    _keys(node, path, {"type", "attrs", "content"})
    if _attrs(node, path, {"level"}).get("level") not in {2, 3, 4}:
        _fail(path, "heading level must be 2, 3 or 4")
    return _children(node, path, required=False), INLINE


def _bullet_list(node: dict[str, Any], path: str, _: list[UUID]) -> Children:
    _keys(node, path, {"type", "attrs", "content"})
    _attrs(node, path, set())
    return _children(node, path, required=True), {"listItem"}


def _ordered_list(node: dict[str, Any], path: str, _: list[UUID]) -> Children:
    _keys(node, path, {"type", "attrs", "content"})
    attrs = _attrs(node, path, {"start", "type"})
    start = attrs.get("start", 1)
    if not isinstance(start, int) or isinstance(start, bool) or not 1 <= start <= MAX_LIST_START:
        _fail(path, f"start must be an integer from 1 to {MAX_LIST_START}")
    if attrs.get("type") is not None:
        _fail(path, "list type is not supported")
    return _children(node, path, required=True), {"listItem"}


def _list_item(node: dict[str, Any], path: str, _: list[UUID]) -> Children:
    _keys(node, path, {"type", "content"})
    return _children(node, path, required=True), BLOCKS


def _blockquote(node: dict[str, Any], path: str, _: list[UUID]) -> Children:
    _keys(node, path, {"type", "content"})
    return _children(node, path, required=True), BLOCKS - {"image"}


def _code_block_node(node: dict[str, Any], path: str, _: list[UUID]) -> Children:
    _keys(node, path, {"type", "attrs", "content"})
    language = _attrs(node, path, {"language"}).get("language")
    if language is not None and language not in LANGUAGES:
        _fail(path, f"language must be one of {sorted(LANGUAGES)}")
    for index, child in enumerate(_children(node, path, required=False)):
        _text(child, f"{path}[{index}]", marks_allowed=False)
    return None


def _image(node: dict[str, Any], path: str, images: list[UUID]) -> Children:
    _keys(node, path, {"type", "attrs"})
    attrs = _attrs(node, path, {"file_id", "alt"})
    try:
        images.append(UUID(str(attrs.get("file_id"))))
    except ValueError:
        _fail(path, "an image must reference an uploaded file by file_id")
    alt = attrs.get("alt")
    if alt is not None and (not isinstance(alt, str) or len(alt) > MAX_ALT):
        _fail(path, f"alt must be text of at most {MAX_ALT} characters")
    return None


def _hard_break(node: dict[str, Any], path: str, _: list[UUID]) -> Children:
    _keys(node, path, {"type"})
    return None


def _text_node(node: dict[str, Any], path: str, _: list[UUID]) -> Children:
    _text(node, path, marks_allowed=True)
    return None


_CHECKERS: Mapping[str, Callable[[dict[str, Any], str, list[UUID]], Children]] = {
    "doc": _doc,
    "paragraph": _paragraph,
    "heading": _heading,
    "bulletList": _bullet_list,
    "orderedList": _ordered_list,
    "listItem": _list_item,
    "blockquote": _blockquote,
    "codeBlock": _code_block_node,
    "image": _image,
    "hardBreak": _hard_break,
    "text": _text_node,
}


def _node(node: Any, path: str, allowed: set[str], depth: int, images: list[UUID]) -> None:
    if depth > MAX_DEPTH:
        _fail(path, "the document is nested too deeply")
    if not isinstance(node, dict):
        _fail(path or "doc", "a node must be an object")
    kind = node.get("type")
    if kind not in allowed:
        _fail(path or "doc", f"node type {kind!r} is not allowed here")
    path = f"{path}.{kind}" if path else kind
    found = _CHECKERS[kind](node, path, images)
    if found is not None:
        children, child_types = found
        for index, child in enumerate(children):
            _node(child, f"{path}[{index}]", child_types, depth + 1, images)


def _text(node: Any, path: str, *, marks_allowed: bool) -> None:
    if not isinstance(node, dict) or node.get("type") != "text":
        _fail(path, "only text is allowed here")
    _keys(node, path, {"type", "text", "marks"} if marks_allowed else {"type", "text"})
    if not isinstance(node.get("text"), str) or not node["text"]:
        _fail(path, "text must be a non-empty string")
    marks = node.get("marks", [])
    if not isinstance(marks, list):
        _fail(path, "marks must be a list")
    seen: set[str] = set()
    for index, mark in enumerate(marks):
        mark_path = f"{path}.marks[{index}]"
        if not isinstance(mark, dict) or mark.get("type") not in MARKS:
            _fail(mark_path, f"mark must be one of {sorted(MARKS)}")
        _keys(mark, mark_path, {"type", "attrs"})
        if mark["type"] in seen:
            _fail(mark_path, "duplicate mark")
        seen.add(mark["type"])
        if mark["type"] == "link":
            href = _attrs(mark, mark_path, {"href", *LINK_EXTRA_ATTRS}).get("href")
            if not _is_web_url(href):
                _fail(mark_path, "links must be absolute http(s) URLs")
        else:
            _attrs(mark, mark_path, set())


def _is_web_url(href: Any) -> bool:
    if not isinstance(href, str) or not 0 < len(href) <= MAX_HREF or href != href.strip():
        return False
    try:
        parts = urlsplit(href)
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


# ============================================================================ rendering

_FORMATTER = HtmlFormatter(nowrap=True)
_TOKEN_CLASSES = {cls for cls in STANDARD_TYPES.values() if cls}

_ALLOWED_TAGS = {
    "p", "br", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "pre", "code", "span",
    "strong", "em", "a", "img",
}  # fmt: skip
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "target"},
    "ol": {"start"},
    "pre": {"data-language"},
    "img": {"data-file-id", "alt"},
}
_ALLOWED_CLASSES = {"pre": {"highlight"}, "span": _TOKEN_CLASSES}


def image_file_ids(doc: Any) -> list[UUID]:
    """Image file ids of an already-validated document."""
    return validate_doc(doc)


def render_html(doc: Mapping[str, Any] | None) -> str:
    """Sanitized HTML for a validated document. Images carry `data-file-id` (no `src`): readers
    get short-lived signed URLs for them separately."""
    if not doc:
        return ""
    return sanitize("".join(_render(doc)))


def sanitize(html: str) -> str:
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        allowed_classes=_ALLOWED_CLASSES,
        url_schemes={"http", "https"},
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )


def _render(node: Mapping[str, Any]) -> Iterator[str]:
    kind = node["type"]
    children = node.get("content", [])
    attrs = node.get("attrs") or {}
    if kind == "doc":
        for child in children:
            yield from _render(child)
    elif kind == "paragraph":
        yield f"<p>{_inline(children)}</p>"
    elif kind == "heading":
        level = int(attrs["level"])
        yield f"<h{level}>{_inline(children)}</h{level}>"
    elif kind in {"bulletList", "orderedList"}:
        start = int(attrs.get("start") or 1)
        tag = "ul" if kind == "bulletList" else "ol"
        yield f'<ol start="{start}">' if tag == "ol" and start != 1 else f"<{tag}>"
        for item in children:
            yield "<li>"
            for child in item.get("content", []):
                yield from _render(child)
            yield "</li>"
        yield f"</{tag}>"
    elif kind == "blockquote":
        yield "<blockquote>"
        for child in children:
            yield from _render(child)
        yield "</blockquote>"
    elif kind == "codeBlock":
        yield _code_block(attrs.get("language"), "".join(c["text"] for c in children))
    elif kind == "image":
        alt = escape(str(attrs.get("alt") or ""), quote=True)
        yield f'<img data-file-id="{UUID(str(attrs["file_id"]))}" alt="{alt}">'


def _inline(nodes: list[Mapping[str, Any]]) -> str:
    out: list[str] = []
    for node in nodes:
        if node["type"] == "hardBreak":
            out.append("<br>")
            continue
        html = escape(node["text"], quote=False)
        # Fixed nesting order, whatever order the editor sent the marks in.
        marks = {mark["type"]: mark for mark in node.get("marks", [])}
        if "code" in marks:
            html = f"<code>{html}</code>"
        if "italic" in marks:
            html = f"<em>{html}</em>"
        if "bold" in marks:
            html = f"<strong>{html}</strong>"
        if "link" in marks:
            href = escape(marks["link"]["attrs"]["href"], quote=True)
            html = f'<a href="{href}" target="_blank">{html}</a>'
        out.append(html)
    return "".join(out)


def _code_block(language: str | None, code: str) -> str:
    name = language or "plaintext"
    lexer = get_lexer_by_name(LANGUAGES[name], stripnl=False, ensurenl=False)
    body = highlight(code, lexer, _FORMATTER)
    return f'<pre class="highlight" data-language="{name}"><code>{body}</code></pre>'


def stylesheet() -> str:
    """The Pygments CSS for `pre.highlight` blocks (served with the web app's notes styles).
    Only rules scoped to `pre.highlight` are kept: Pygments also emits global `pre` and
    line-number rules, which would restyle every `<pre>` in the app."""
    css = str(HtmlFormatter().get_style_defs("pre.highlight"))  # type: ignore[no-untyped-call]
    return "\n".join(line for line in css.splitlines() if line.startswith("pre.highlight"))
