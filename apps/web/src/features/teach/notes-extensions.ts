import { Node, type Extensions } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";

import { isWebUrl } from "./notes-doc";

/** Resolves an uploaded image's file id to a short-lived signed URL (for display only). */
export type ImageUrlResolver = (fileId: string) => string | undefined;

/**
 * Images reference uploaded files by id; the saved document never contains a URL (signed URLs
 * expire, and the API rejects `src`). The editor shows them through `resolveUrl`.
 */
export const FileImage = Node.create<{ resolveUrl: ImageUrlResolver }>({
  name: "image",
  group: "block",
  atom: true,
  draggable: true,
  addOptions: () => ({ resolveUrl: () => undefined }),
  addAttributes: () => ({
    file_id: {
      default: null,
      parseHTML: (element) => element.getAttribute("data-file-id"),
      renderHTML: (attrs) => ({ "data-file-id": attrs.file_id as string }),
    },
    alt: {
      default: null,
      parseHTML: (element) => element.getAttribute("alt"),
      renderHTML: (attrs) => (attrs.alt ? { alt: attrs.alt as string } : {}),
    },
  }),
  parseHTML: () => [{ tag: "img[data-file-id]" }],
  renderHTML: ({ HTMLAttributes }) => ["img", HTMLAttributes],
  addNodeView() {
    const resolveUrl = this.options.resolveUrl;
    return ({ node }) => {
      const img = document.createElement("img");
      img.alt = (node.attrs.alt as string | null) ?? "";
      img.className = "my-2 max-h-80 max-w-full rounded border";
      const src = resolveUrl(node.attrs.file_id as string);
      if (src) img.src = src;
      return { dom: img };
    };
  },
});

/** Exactly the node and mark set the API accepts (apps/api/app/modules/courses/notes.py). */
export function notesExtensions(resolveUrl: ImageUrlResolver): Extensions {
  return [
    StarterKit.configure({
      heading: { levels: [2, 3, 4] },
      strike: false,
      underline: false,
      horizontalRule: false,
      link: {
        openOnClick: false,
        autolink: true,
        defaultProtocol: "https",
        protocols: ["http", "https"],
        isAllowedUri: (url) => isWebUrl(url),
      },
    }),
    FileImage.configure({ resolveUrl }),
  ];
}
