/** Browser uploads straight to storage with a presigned POST (shared by editors and students). */

/** A display name the API accepts (no control characters, quotes, slashes or backslashes). */
export function safeFileName(name: string, fallback: string): string {
  const cleaned = name
    .replace(/[\u0000-\u001f\u007f"\\/]/g, "_")
    .trim()
    .slice(0, 255);
  return cleaned || fallback;
}

/** POST a presigned form to storage, reporting progress. The file must be the last field. */
export function postForm(
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
