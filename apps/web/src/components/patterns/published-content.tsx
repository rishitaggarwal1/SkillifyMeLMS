"use client";

import { useEffect, useRef } from "react";

/** API-sanitized notes/instructions; image IDs resolve only through the authorized response. */
export function PublishedContent({
  html,
  imageUrls = {},
}: {
  html: string;
  imageUrls?: Record<string, string>;
}) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    for (const image of container.current?.querySelectorAll<HTMLImageElement>(
      "img[data-file-id]",
    ) ?? []) {
      const value = imageUrls[image.dataset.fileId ?? ""];
      if (!value) {
        image.removeAttribute("src");
        continue;
      }
      const url = new URL(value, window.location.href);
      if (url.protocol === "https:" || url.protocol === "http:") {
        image.src = url.href;
        image.loading = "lazy";
        image.decoding = "async";
      }
    }
  }, [html, imageUrls]);
  return (
    <div ref={container} className="notes-content" dangerouslySetInnerHTML={{ __html: html }} />
  );
}
