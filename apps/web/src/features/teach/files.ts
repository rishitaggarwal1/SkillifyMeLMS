import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { StoredFile } from "@/lib/api/types";
import { unwrap } from "@/lib/api/unwrap";

export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"] as const;
type ImageType = (typeof IMAGE_TYPES)[number];

/** A display name the API accepts (no control characters, quotes, slashes or backslashes). */
export function safeFileName(name: string, fallback: string): string {
  const cleaned = name
    .replace(/[\u0000-\u001f\u007f"\\/]/g, "_")
    .trim()
    .slice(0, 255);
  return cleaned || fallback;
}

export function fileProblem(file: File, kind: "pdf" | "image"): string | null {
  if (kind === "pdf" && file.type !== "application/pdf") return "Choose a PDF file.";
  if (kind === "image" && !(IMAGE_TYPES as readonly string[]).includes(file.type)) {
    return "Choose a PNG, JPEG, WebP or GIF image.";
  }
  if (file.size === 0) return "That file is empty.";
  return null;
}

/**
 * Upload a PDF or image straight to storage, then have the API check it:
 * 1. `POST /files` gives a presigned POST limited to this file's type and the size limit;
 * 2. the browser posts the form to storage (progress reported);
 * 3. `POST /files/{id}/confirm` checks size and contents. A rejection is thrown with the reason.
 */
export async function uploadFile(
  file: File,
  kind: "pdf" | "image",
  onProgress: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<StoredFile> {
  const problem = fileProblem(file, kind);
  if (problem) throw new Error(problem);
  const created = await unwrap(
    api.POST("/api/v1/files", {
      body: {
        kind,
        file_name: safeFileName(file.name, kind === "pdf" ? "document.pdf" : "image"),
        content_type: kind === "pdf" ? "application/pdf" : (file.type as ImageType),
      },
    }),
  );
  if (file.size > created.upload.max_bytes) {
    const mb = Math.floor(created.upload.max_bytes / 1024 ** 2);
    throw new Error(`That file is larger than ${mb} MB.`);
  }
  await postForm(created.upload.url, created.upload.fields, file, onProgress, signal);
  try {
    return await unwrap(
      api.POST("/api/v1/files/{file_id}/confirm", {
        params: { path: { file_id: created.file.id } },
      }),
    );
  } catch (error) {
    if (error instanceof ApiError && error.code === "file_rejected") {
      const reason = (error.details as { reason?: string } | undefined)?.reason;
      throw new Error(reason ?? "The file was rejected.");
    }
    throw error;
  }
}

function postForm(
  url: string,
  fields: Record<string, string>,
  file: File,
  onProgress: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Upload cancelled", "AbortError"));
      return;
    }
    const form = new FormData();
    for (const [name, value] of Object.entries(fields)) form.append(name, value);
    form.append("file", file); // must be the last field
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    xhr.open("POST", url);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onloadend = () => signal?.removeEventListener("abort", abort);
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new Error("Storage refused the upload (wrong type or too large)."));
    xhr.onerror = () => reject(new Error("Check your connection and retry the upload."));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    signal?.addEventListener("abort", abort, { once: true });
    xhr.send(form);
  });
}
