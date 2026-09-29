import { z } from "zod";
import type { components } from "@/lib/api/schema";

export const videoFileSchema = z
  .instanceof(File)
  .refine((file) => file.type === "video/mp4", "Choose an MP4 video.")
  .refine(
    (file) => file.size > 0 && file.size <= 2 * 1024 ** 3,
    "Choose a video smaller than 2 GB.",
  );

export async function uploadVideo(
  file: File,
  ticket: components["schemas"]["UploadTicketOut"],
  onProgress: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<void> {
  videoFileSchema.parse(file);
  if (signal?.aborted) throw new DOMException("Upload cancelled", "AbortError");
  if (ticket.protocol === "tus") {
    const { Upload } = await import("tus-js-client");
    await new Promise<void>((resolve, reject) => {
      const upload = new Upload(file, {
        endpoint: ticket.url,
        headers: ticket.headers,
        metadata: { filetype: file.type, title: file.name },
        storeFingerprintForResuming: false,
        retryDelays: [0, 1000, 3000, 5000],
        onProgress: (sent, total) => onProgress(sent / total),
        onSuccess: () => {
          signal?.removeEventListener("abort", abort);
          resolve();
        },
        onError: (error) => {
          signal?.removeEventListener("abort", abort);
          reject(error);
        },
      });
      const abort = () => {
        void upload.abort();
        reject(new DOMException("Upload cancelled", "AbortError"));
      };
      signal?.addEventListener("abort", abort, { once: true });
      upload.start();
    });
    return;
  }
  await new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    xhr.open("PUT", ticket.url);
    for (const [name, value] of Object.entries(ticket.headers)) xhr.setRequestHeader(name, value);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onloadend = () => signal?.removeEventListener("abort", abort);
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error("Video upload failed."));
    xhr.onerror = () => reject(new Error("Check your connection and retry the upload."));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    signal?.addEventListener("abort", abort, { once: true });
    xhr.send(file);
  });
}
