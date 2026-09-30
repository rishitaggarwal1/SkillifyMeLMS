import { describe, expect, it } from "vitest";

import { imageFileIds, isWebUrl, normalizeNotesDoc } from "./notes-doc";

const IMAGE = "00000000-0000-4000-8000-000000000001";

describe("normalizeNotesDoc", () => {
  it("keeps the allowed shape and drops Tiptap's extra attributes", () => {
    const tiptap = {
      type: "doc",
      content: [
        { type: "heading", attrs: { level: 2 }, content: [{ type: "text", text: "Arrays" }] },
        {
          type: "paragraph",
          content: [
            {
              type: "text",
              text: "docs",
              marks: [
                { type: "bold" },
                {
                  type: "link",
                  attrs: { href: "https://example.com", target: "_blank", rel: null, class: null },
                },
              ],
            },
            { type: "hardBreak" },
          ],
        },
        {
          type: "orderedList",
          attrs: { start: 3, type: null },
          content: [
            {
              type: "listItem",
              content: [{ type: "paragraph", content: [{ type: "text", text: "x" }] }],
            },
          ],
        },
        {
          type: "codeBlock",
          attrs: { language: "python" },
          content: [{ type: "text", text: "pass" }],
        },
        { type: "image", attrs: { file_id: IMAGE, alt: "diagram", src: "blob:x", title: null } },
      ],
    };
    expect(normalizeNotesDoc(tiptap)).toEqual({
      type: "doc",
      content: [
        { type: "heading", attrs: { level: 2 }, content: [{ type: "text", text: "Arrays" }] },
        {
          type: "paragraph",
          content: [
            {
              type: "text",
              text: "docs",
              marks: [{ type: "bold" }, { type: "link", attrs: { href: "https://example.com" } }],
            },
            { type: "hardBreak" },
          ],
        },
        {
          type: "orderedList",
          attrs: { start: 3 },
          content: [
            {
              type: "listItem",
              content: [{ type: "paragraph", content: [{ type: "text", text: "x" }] }],
            },
          ],
        },
        {
          type: "codeBlock",
          attrs: { language: "python" },
          content: [{ type: "text", text: "pass" }],
        },
        { type: "image", attrs: { file_id: IMAGE, alt: "diagram" } },
      ],
    });
  });

  it("drops what the API would reject", () => {
    const doc = normalizeNotesDoc({
      type: "doc",
      content: [
        { type: "horizontalRule" },
        { type: "iframe", attrs: { src: "https://evil" } },
        {
          type: "paragraph",
          attrs: { onclick: "x" },
          content: [
            {
              type: "text",
              text: "a",
              marks: [{ type: "link", attrs: { href: "javascript:alert(1)" } }],
            },
            { type: "text", text: "b", marks: [{ type: "strike" }, { type: "underline" }] },
            { type: "text", text: "" },
          ],
        },
        { type: "heading", attrs: { level: 1 }, content: [{ type: "text", text: "h" }] },
        {
          type: "codeBlock",
          attrs: { language: "brainfuck" },
          content: [{ type: "text", text: "x", marks: [{ type: "bold" }] }],
        },
        { type: "blockquote", content: [{ type: "image", attrs: { file_id: IMAGE } }] },
        { type: "image", attrs: { src: "https://evil/x.png" } },
      ],
    });
    expect(doc.content).toEqual([
      {
        type: "paragraph",
        content: [
          { type: "text", text: "a" },
          { type: "text", text: "b" },
        ],
      },
      { type: "heading", attrs: { level: 2 }, content: [{ type: "text", text: "h" }] },
      { type: "codeBlock", attrs: { language: null }, content: [{ type: "text", text: "x" }] },
    ]);
  });

  it("treats anything that isn't a doc as empty", () => {
    expect(normalizeNotesDoc(null)).toEqual({ type: "doc", content: [] });
    expect(normalizeNotesDoc({ type: "paragraph" })).toEqual({ type: "doc", content: [] });
  });

  it("finds image ids anywhere", () => {
    const doc = normalizeNotesDoc({
      type: "doc",
      content: [
        { type: "image", attrs: { file_id: IMAGE } },
        {
          type: "bulletList",
          content: [{ type: "listItem", content: [{ type: "image", attrs: { file_id: "f2" } }] }],
        },
      ],
    });
    expect(imageFileIds(doc)).toEqual([IMAGE, "f2"]);
  });
});

describe("isWebUrl", () => {
  it.each([
    ["https://example.com/a?b=1", true],
    ["http://example.com", true],
    ["javascript:alert(1)", false],
    ["data:text/html,x", false],
    ["/relative", false],
    [" https://example.com", false],
    [42, false],
  ])("%j -> %j", (href, ok) => {
    expect(isWebUrl(href)).toBe(ok);
  });
});
