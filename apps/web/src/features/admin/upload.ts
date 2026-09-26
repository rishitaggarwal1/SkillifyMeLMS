import { z } from "zod";

import { toApiError } from "@/lib/api/errors";
import type { ImportJob } from "@/lib/api/types";

export const MAX_IMPORT_BYTES = 5 * 1024 * 1024;

/** Client-side checks before uploading (the API re-validates everything). */
export const importFileSchema = z
  .instanceof(File, { message: "Choose a CSV file." })
  .refine((f) => f.name.toLowerCase().endsWith(".csv"), "The file must be a .csv file.")
  .refine((f) => f.size > 0, "The file is empty.")
  .refine((f) => f.size <= MAX_IMPORT_BYTES, "The file is larger than 5 MB.");

/**
 * Upload with progress. XMLHttpRequest is used because fetch() can't report upload progress,
 * which matters on slow mobile connections.
 */
export function uploadImport(
  file: File,
  batchId: string | null,
  onProgress: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<ImportJob> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/backend/api/v1/imports");
    xhr.responseType = "text";
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* not JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(1);
        resolve(body as ImportJob);
      } else {
        const headers = new Headers();
        const requestId = xhr.getResponseHeader("x-request-id");
        if (requestId) headers.set("x-request-id", requestId);
        reject(toApiError(new Response(null, { status: xhr.status, headers }), body));
      }
    };
    xhr.onerror = () => reject(new Error("Network error while uploading. Check your connection."));
    signal?.addEventListener("abort", () => xhr.abort());
    const form = new FormData();
    form.append("file", file);
    if (batchId) form.append("batch_id", batchId);
    xhr.send(form);
  });
}
