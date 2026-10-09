"use client";

import { useSyncExternalStore } from "react";
import { toastQueue } from "@/lib/toast";
import { Button } from "./button";

export function Toaster() {
  const snapshot = useSyncExternalStore(
    toastQueue.subscribe,
    toastQueue.getSnapshot,
    toastQueue.getServerSnapshot,
  );
  if (snapshot.runtime) {
    const Host = snapshot.runtime.Toaster;
    return <Host position="top-center" onReady={toastQueue.mounted} />;
  }
  if (!snapshot.failed) return null;
  return (
    <section
      aria-label="Notifications"
      className="fixed inset-x-4 top-4 z-50 mx-auto max-w-sm space-y-2"
    >
      {snapshot.messages.map((message) => (
        <div
          key={message.id}
          className="rounded-lg border bg-popover p-3 text-popover-foreground shadow-lg"
        >
          <p role={message.kind === "error" ? "alert" : "status"}>{message.message}</p>
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={toastQueue.retry}>
              Retry
            </Button>
            <Button type="button" variant="ghost" onClick={() => toastQueue.dismiss(message.id)}>
              Dismiss
            </Button>
          </div>
        </div>
      ))}
    </section>
  );
}
