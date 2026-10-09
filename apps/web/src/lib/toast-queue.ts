export type ToastKind = "success" | "error" | "warning" | "info";
export type ToastMessage = { id: number; kind: ToastKind; message: string };
export type ToastSnapshot<Runtime> = {
  runtime: Runtime | null;
  failed: boolean;
  messages: readonly ToastMessage[];
};

/** One first-use import; delivery waits for the renderer to mount. Failed loads
 * keep the queue available to the accessible fallback and retry on next use. */
export function createToastQueue<Runtime>(
  load: () => Promise<Runtime>,
  deliver: (runtime: Runtime, message: ToastMessage) => void,
) {
  const listeners = new Set<() => void>();
  const empty: ToastSnapshot<Runtime> = { runtime: null, failed: false, messages: [] };
  let snapshot = empty;
  let loading: Promise<void> | null = null;
  let mounted = false;
  let nextId = 0;
  const update = (next: ToastSnapshot<Runtime>) => {
    snapshot = next;
    listeners.forEach((listener) => listener());
  };
  const flush = () => {
    if (!mounted || !snapshot.runtime) return;
    const { runtime, messages } = snapshot;
    update({ ...snapshot, messages: [] });
    messages.forEach((message) => deliver(runtime, message));
  };
  const ensureLoaded = () => {
    if (snapshot.runtime || loading) return;
    loading = load().then(
      (runtime) => {
        loading = null;
        update({ ...snapshot, runtime, failed: false });
        flush();
      },
      () => {
        loading = null;
        update({ ...snapshot, failed: true });
      },
    );
  };
  return {
    getSnapshot: () => snapshot,
    getServerSnapshot: () => empty,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    show: (kind: ToastKind, message: string) => {
      update({ ...snapshot, messages: [...snapshot.messages, { id: ++nextId, kind, message }] });
      ensureLoaded();
      flush();
    },
    dismiss: (id: number) =>
      update({ ...snapshot, messages: snapshot.messages.filter((m) => m.id !== id) }),
    retry: ensureLoaded,
    mounted: () => {
      mounted = true;
      flush();
      return () => {
        mounted = false;
      };
    },
  };
}
