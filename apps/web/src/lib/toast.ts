"use client";

import { createToastQueue } from "./toast-queue";

export const toastQueue = createToastQueue(
  () => import("@/components/ui/sonner"),
  (runtime, message) => runtime.showToast(message.kind, message.message),
);

// Callers need no async handling; the shared host retains their notifications.
export const toast = {
  success: (message: string) => toastQueue.show("success", message),
  error: (message: string) => toastQueue.show("error", message),
  warning: (message: string) => toastQueue.show("warning", message),
  info: (message: string) => toastQueue.show("info", message),
};
