import { Editor } from "@tiptap/core";
import { afterEach, describe, expect, it } from "vitest";

import { normalizeNotesDoc } from "./notes-doc";
import { notesExtensions } from "./notes-extensions";

let editor: Editor | undefined;
afterEach(() => editor?.destroy());

function edit(html: string) {
  editor = new Editor({ extensions: notesExtensions(() => undefined), content: html });
  return normalizeNotesDoc(editor.getJSON());
}

describe("the notes editor's output", () => {
  it("covers every allowed construct in the API's shape", () => {
    const doc = edit(
      [
        "<h2>Two pointers</h2>",
        '<p><strong>bold</strong> <em>it</em> <code>x</code> <a href="https://example.com">l</a><br>n</p>',
        "<ul><li><p>one</p></li></ul>",
        '<ol start="3"><li><p>three</p></li></ol>',
        "<blockquote><p>q</p></blockquote>",
        '<pre><code class="language-python">def f(): pass</code></pre>',
        '<img data-file-id="00000000-0000-4000-8000-000000000001" alt="diagram">',
      ].join(""),
    );
    expect(doc.content.map((n) => n.type)).toEqual([
      "heading",
      "paragraph",
      "bulletList",
      "orderedList",
      "blockquote",
      "codeBlock",
      "image",
    ]);
    expect(doc.content[0]).toEqual({
      type: "heading",
      attrs: { level: 2 },
      content: [{ type: "text", text: "Two pointers" }],
    });
    const paragraph = doc.content[1]!.content!;
    expect(paragraph.flatMap((n) => n.marks ?? [])).toEqual([
      { type: "bold" },
      { type: "italic" },
      { type: "code" },
      { type: "link", attrs: { href: "https://example.com" } },
    ]);
    expect(paragraph.some((n) => n.type === "hardBreak")).toBe(true);
    expect(doc.content[3]!.attrs).toEqual({ start: 3 });
    expect(doc.content[5]!.attrs).toEqual({ language: "python" });
    expect(doc.content[6]).toEqual({
      type: "image",
      attrs: { file_id: "00000000-0000-4000-8000-000000000001", alt: "diagram" },
    });
  });

  it("never produces what the API rejects", () => {
    const doc = edit(
      [
        "<h1>Top</h1>",
        "<p><s>strike</s> <u>under</u></p>",
        "<hr>",
        '<p><a href="javascript:alert(1)">bad</a></p>',
        '<img src="https://evil/x.png">',
        "<script>alert(1)</script>",
        '<iframe src="https://evil"></iframe>',
      ].join(""),
    );
    // Unsupported formatting survives only as plain text.
    expect(doc.content).toEqual([
      { type: "paragraph", content: [{ type: "text", text: "Top" }] }, // <h1> is not allowed
      { type: "paragraph", content: [{ type: "text", text: "strike under" }] },
      { type: "paragraph", content: [{ type: "text", text: "bad" }] }, // javascript: link dropped
    ]);
    expect(JSON.stringify(doc)).not.toMatch(/javascript|evil|script|iframe|src/);
  });
});
