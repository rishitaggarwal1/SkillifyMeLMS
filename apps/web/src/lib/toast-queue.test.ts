// @vitest-environment node
import { expect, it, vi } from "vitest";
import { createToastQueue } from "./toast-queue";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

it("imports on first use once and retains concurrent messages until the host mounts", async () => {
  const pending = deferred<string>();
  const load = vi.fn(() => pending.promise);
  const deliver = vi.fn();
  const queue = createToastQueue(load, deliver);
  expect(load).not.toHaveBeenCalled();
  queue.show("success", "Saved");
  queue.show("error", "Retry saving");
  expect(load).toHaveBeenCalledTimes(1);
  pending.resolve("loaded runtime");
  await pending.promise;
  expect(deliver).not.toHaveBeenCalled();
  expect(queue.getSnapshot().messages).toHaveLength(2);
  queue.mounted();
  expect(deliver.mock.calls).toEqual([
    ["loaded runtime", { id: 1, kind: "success", message: "Saved" }],
    ["loaded runtime", { id: 2, kind: "error", message: "Retry saving" }],
  ]);
  expect(queue.getSnapshot().messages).toEqual([]);
  queue.show("info", "Updated");
  expect(deliver).toHaveBeenLastCalledWith("loaded runtime", {
    id: 3,
    kind: "info",
    message: "Updated",
  });
  expect(load).toHaveBeenCalledTimes(1);
});

it("delivers when the host mounts before the import resolves, without replay on remount", async () => {
  const pending = deferred<string>();
  const deliver = vi.fn();
  const queue = createToastQueue(() => pending.promise, deliver);
  const unmount = queue.mounted();
  queue.show("success", "Submitted");
  pending.resolve("runtime");
  await pending.promise;
  expect(deliver).toHaveBeenCalledTimes(1);
  unmount();
  queue.show("warning", "Connection interrupted");
  expect(deliver).toHaveBeenCalledTimes(1);
  queue.mounted();
  expect(deliver).toHaveBeenCalledTimes(2);
  expect(deliver).toHaveBeenLastCalledWith("runtime", {
    id: 2,
    kind: "warning",
    message: "Connection interrupted",
  });
});

it("keeps failed imports available to the fallback and retries without losing or duplicating messages", async () => {
  const failed = deferred<string>();
  const retry = deferred<string>();
  const load = vi.fn().mockReturnValueOnce(failed.promise).mockReturnValueOnce(retry.promise);
  const deliver = vi.fn();
  const queue = createToastQueue<string>(load, deliver);
  queue.show("success", "Grade saved");
  failed.reject(new Error("offline chunk"));
  await failed.promise.catch(() => {});
  expect(queue.getSnapshot().failed).toBe(true);
  expect(queue.getSnapshot().messages).toEqual([
    { id: 1, kind: "success", message: "Grade saved" },
  ]);
  queue.show("error", "Upload failed");
  expect(load).toHaveBeenCalledTimes(2);
  retry.resolve("runtime");
  await retry.promise;
  queue.mounted();
  expect(queue.getSnapshot().failed).toBe(false);
  expect(deliver.mock.calls.map((call) => call[1].message)).toEqual([
    "Grade saved",
    "Upload failed",
  ]);
});

it("does not replay a notification dismissed from the failed-load fallback", async () => {
  const load = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce("runtime");
  const deliver = vi.fn();
  const queue = createToastQueue<string>(load, deliver);
  queue.show("info", "Notice");
  await Promise.resolve();
  queue.dismiss(1);
  queue.retry();
  await Promise.resolve();
  queue.mounted();
  expect(load).toHaveBeenCalledTimes(2);
  expect(deliver).not.toHaveBeenCalled();
});
