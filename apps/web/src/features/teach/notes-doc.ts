/**
 * The notes document the API accepts (apps/api/app/modules/courses/notes.py is the authority).
 * `normalizeNotesDoc` reduces the editor's JSON to exactly that shape, dropping attributes Tiptap
 * adds by default (link target/rel/class, orderedList type...), so a Tiptap upgrade can't make
 * saves fail validation. Anything outside the allow-list is dropped rather than sent.
 */

export const CODE_LANGUAGES = [
  "plaintext",
  "bash",
  "c",
  "cpp",
  "csharp",
  "css",
  "go",
  "html",
  "java",
  "javascript",
  "json",
  "kotlin",
  "python",
  "rust",
  "sql",
  "typescript",
] as const;
export type CodeLanguage = (typeof CODE_LANGUAGES)[number];

export type NotesMark =
  | { type: "bold" }
  | { type: "italic" }
  | { type: "code" }
  | { type: "link"; attrs: { href: string } };

export type NotesNode = {
  type: string;
  attrs?: Record<string, unknown>;
  content?: NotesNode[];
  text?: string;
  marks?: NotesMark[];
};

export type NotesDoc = { type: "doc"; content: NotesNode[] };

const BLOCKS = new Set([
  "paragraph",
  "heading",
  "bulletList",
  "orderedList",
  "codeBlock",
  "blockquote",
  "image",
]);

type Raw = { type?: unknown; attrs?: unknown; content?: unknown; text?: unknown; marks?: unknown };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const children = (node: Raw): Raw[] =>
  Array.isArray(node.content) ? (node.content.filter(isRecord) as Raw[]) : [];

const attrs = (node: Raw): Record<string, unknown> => (isRecord(node.attrs) ? node.attrs : {});

export function isWebUrl(href: unknown): href is string {
  if (typeof href !== "string" || href !== href.trim() || href.length > 2000) return false;
  try {
    const url = new URL(href);
    return (url.protocol === "http:" || url.protocol === "https:") && !!url.hostname;
  } catch {
    return false;
  }
}

function marks(raw: unknown): NotesMark[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: NotesMark[] = [];
  for (const mark of raw) {
    if (!isRecord(mark) || typeof mark.type !== "string" || seen.has(mark.type)) continue;
    if (mark.type === "bold" || mark.type === "italic" || mark.type === "code") {
      out.push({ type: mark.type });
      seen.add(mark.type);
    } else if (mark.type === "link") {
      const href = isRecord(mark.attrs) ? mark.attrs.href : undefined;
      if (isWebUrl(href)) {
        out.push({ type: "link", attrs: { href } });
        seen.add("link");
      }
    }
  }
  return out;
}

function inline(nodes: Raw[], allowMarks: boolean): NotesNode[] {
  const out: NotesNode[] = [];
  for (const node of nodes) {
    if (node.type === "hardBreak" && allowMarks) out.push({ type: "hardBreak" });
    if (node.type !== "text" || typeof node.text !== "string" || node.text === "") continue;
    const kept = allowMarks ? marks(node.marks) : [];
    out.push(
      kept.length
        ? { type: "text", text: node.text, marks: kept }
        : { type: "text", text: node.text },
    );
  }
  return out;
}

function withContent(
  type: string,
  content: NotesNode[],
  extra?: Record<string, unknown>,
): NotesNode {
  return { type, ...(extra ? { attrs: extra } : {}), ...(content.length ? { content } : {}) };
}

function block(node: Raw, allowed: Set<string>): NotesNode | null {
  const type = node.type;
  if (typeof type !== "string" || !allowed.has(type)) return null;
  const a = attrs(node);
  switch (type) {
    case "paragraph":
      return withContent("paragraph", inline(children(node), true));
    case "heading": {
      const level = [2, 3, 4].includes(a.level as number) ? (a.level as number) : 2;
      return withContent("heading", inline(children(node), true), { level });
    }
    case "bulletList":
    case "orderedList": {
      const items = children(node)
        .filter((item) => item.type === "listItem")
        .map((item) => {
          const content = blocks(children(item), BLOCKS);
          return { type: "listItem", content: content.length ? content : [{ type: "paragraph" }] };
        });
      if (!items.length) return null;
      if (type === "bulletList") return { type, content: items };
      const start = Number.isInteger(a.start)
        ? Math.min(Math.max(a.start as number, 1), 10_000)
        : 1;
      return { type, attrs: { start }, content: items };
    }
    case "blockquote": {
      const content = blocks(children(node), new Set([...BLOCKS].filter((t) => t !== "image")));
      return content.length ? { type, content } : null;
    }
    case "codeBlock": {
      const language = (CODE_LANGUAGES as readonly unknown[]).includes(a.language)
        ? (a.language as CodeLanguage)
        : null;
      return withContent("codeBlock", inline(children(node), false), { language });
    }
    case "image": {
      if (typeof a.file_id !== "string") return null;
      const alt = typeof a.alt === "string" ? a.alt.slice(0, 300) : null;
      return { type: "image", attrs: { file_id: a.file_id, alt } };
    }
    default:
      return null;
  }
}

function blocks(nodes: Raw[], allowed: Set<string>): NotesNode[] {
  return nodes.map((node) => block(node, allowed)).filter((n): n is NotesNode => n !== null);
}

export function normalizeNotesDoc(doc: unknown): NotesDoc {
  const content = isRecord(doc) && doc.type === "doc" ? blocks(children(doc), BLOCKS) : [];
  return { type: "doc", content };
}

/** File ids of the images a document uses (for previews and upload bookkeeping). */
export function imageFileIds(doc: NotesDoc): string[] {
  const ids: string[] = [];
  const walk = (nodes: NotesNode[] | undefined) =>
    nodes?.forEach((node) => {
      if (node.type === "image" && typeof node.attrs?.file_id === "string") {
        ids.push(node.attrs.file_id);
      }
      walk(node.content);
    });
  walk(doc.content);
  return ids;
}
