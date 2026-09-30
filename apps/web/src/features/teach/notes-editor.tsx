"use client";

/**
 * Tiptap notes editor. Loaded with next/dynamic from the lesson page only, so Tiptap never ships
 * in the student or admin bundles.
 */
import { EditorContent, useEditor, useEditorState, type Editor } from "@tiptap/react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { NativeSelect } from "@/components/native-select";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/unwrap";

import { uploadFile } from "./files";
import { CODE_LANGUAGES, isWebUrl, normalizeNotesDoc, type NotesDoc } from "./notes-doc";
import { notesExtensions } from "./notes-extensions";

type Props = {
  initialDoc: NotesDoc;
  /** Signed URLs for images already in the document (from the draft preview). */
  initialImageUrls: Record<string, string>;
  saving: boolean;
  onSave: (doc: NotesDoc) => Promise<unknown>;
};

export default function NotesEditor({ initialDoc, initialImageUrls, saving, onSave }: Props) {
  // A stable map mutated as images are uploaded (read by the image node view, not by render).
  const [urls] = useState(() => new Map(Object.entries(initialImageUrls)));
  const [dirty, setDirty] = useState(false);
  const editor = useEditor({
    extensions: notesExtensions((fileId) => urls.get(fileId)),
    content: initialDoc,
    immediatelyRender: false, // rendered on the client only (avoids hydration mismatches)
    editorProps: {
      attributes: {
        class:
          "notes-content min-h-48 rounded-b-md border px-3 py-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        role: "textbox",
        "aria-multiline": "true",
        "aria-label": "Notes",
      },
    },
    onUpdate: () => setDirty(true),
  });

  if (!editor) return <div className="min-h-48 rounded-md border" aria-busy />;

  async function save() {
    if (!editor) return;
    await onSave(normalizeNotesDoc(editor.getJSON()));
    setDirty(false);
  }

  return (
    <div className="flex flex-col gap-2">
      <Toolbar
        editor={editor}
        onImageUploaded={(fileId, url) => {
          urls.set(fileId, url);
          editor
            .chain()
            .focus()
            .insertContent({ type: "image", attrs: { file_id: fileId } })
            .run();
        }}
      />
      <EditorContent editor={editor} />
      <div className="flex items-center gap-3">
        <Button onClick={() => void save()} disabled={saving || !dirty}>
          {saving ? "Saving…" : "Save notes"}
        </Button>
        <span role="status" className="text-sm text-muted-foreground">
          {dirty ? "Unsaved changes" : "All changes saved"}
        </span>
      </div>
    </div>
  );
}

function Toolbar({
  editor,
  onImageUploaded,
}: {
  editor: Editor;
  onImageUploaded: (fileId: string, url: string) => void;
}) {
  const state = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e.isActive("bold"),
      italic: e.isActive("italic"),
      code: e.isActive("code"),
      link: e.isActive("link"),
      h2: e.isActive("heading", { level: 2 }),
      h3: e.isActive("heading", { level: 3 }),
      h4: e.isActive("heading", { level: 4 }),
      bullet: e.isActive("bulletList"),
      ordered: e.isActive("orderedList"),
      quote: e.isActive("blockquote"),
      codeBlock: e.isActive("codeBlock"),
      language: (e.getAttributes("codeBlock").language as string | null) ?? "plaintext",
    }),
  });
  const [linking, setLinking] = useState(false);
  const [uploading, setUploading] = useState<number | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const chain = () => editor.chain().focus();

  const tools: { label: string; active: boolean; run: () => void }[] = [
    { label: "Bold", active: state.bold, run: () => chain().toggleBold().run() },
    { label: "Italic", active: state.italic, run: () => chain().toggleItalic().run() },
    { label: "Code", active: state.code, run: () => chain().toggleCode().run() },
    { label: "H2", active: state.h2, run: () => chain().toggleHeading({ level: 2 }).run() },
    { label: "H3", active: state.h3, run: () => chain().toggleHeading({ level: 3 }).run() },
    { label: "H4", active: state.h4, run: () => chain().toggleHeading({ level: 4 }).run() },
    { label: "List", active: state.bullet, run: () => chain().toggleBulletList().run() },
    { label: "Numbered", active: state.ordered, run: () => chain().toggleOrderedList().run() },
    { label: "Quote", active: state.quote, run: () => chain().toggleBlockquote().run() },
    { label: "Code block", active: state.codeBlock, run: () => chain().toggleCodeBlock().run() },
  ];

  async function uploadImage(file: File) {
    setUploading(0);
    try {
      const stored = await uploadFile(file, "image", setUploading);
      const download = await unwrap(
        api.GET("/api/v1/files/{file_id}/download", { params: { path: { file_id: stored.id } } }),
      );
      onImageUploaded(stored.id, download.url);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Image upload failed.");
    } finally {
      setUploading(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  return (
    <div
      role="toolbar"
      aria-label="Formatting"
      // Keep the editor's selection when a toolbar button is pressed (formatting applies to it).
      onMouseDown={(event) => {
        if ((event.target as HTMLElement).closest("button")) event.preventDefault();
      }}
      className="flex flex-wrap items-center gap-1 rounded-t-md border border-b-0 bg-muted/40 p-1"
    >
      {tools.map((tool) => (
        <Button
          key={tool.label}
          type="button"
          size="sm"
          variant={tool.active ? "secondary" : "ghost"}
          aria-pressed={tool.active}
          onClick={tool.run}
        >
          {tool.label}
        </Button>
      ))}
      {state.codeBlock ? (
        <NativeSelect
          aria-label="Code language"
          className="h-7 w-32"
          value={state.language}
          onChange={(event) =>
            chain()
              .updateAttributes("codeBlock", {
                language: event.target.value === "plaintext" ? null : event.target.value,
              })
              .run()
          }
        >
          {CODE_LANGUAGES.map((language) => (
            <option key={language} value={language}>
              {language}
            </option>
          ))}
        </NativeSelect>
      ) : null}
      <Button
        type="button"
        size="sm"
        variant={state.link ? "secondary" : "ghost"}
        onClick={() => (state.link ? chain().unsetLink().run() : setLinking((v) => !v))}
      >
        {state.link ? "Remove link" : "Link"}
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        disabled={uploading !== null}
        onClick={() => fileInput.current?.click()}
      >
        {uploading === null ? "Image" : `Uploading ${Math.round(uploading * 100)}%`}
      </Button>
      <input
        ref={fileInput}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif"
        className="hidden"
        aria-label="Upload image"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void uploadImage(file);
        }}
      />
      {linking ? (
        <form
          className="flex w-full gap-1"
          onSubmit={(event) => {
            event.preventDefault();
            const href = String(new FormData(event.currentTarget).get("href") ?? "").trim();
            if (!isWebUrl(href)) {
              toast.error("Links must start with http:// or https://");
              return;
            }
            chain().extendMarkRange("link").setLink({ href }).run();
            setLinking(false);
          }}
        >
          <Input name="href" type="url" placeholder="https://…" aria-label="Link URL" autoFocus />
          <Button type="submit" size="sm">
            Apply
          </Button>
        </form>
      ) : null}
    </div>
  );
}
