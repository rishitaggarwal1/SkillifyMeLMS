"use client";

import { useState, type ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError } from "@/lib/api/errors";

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

export function ErrorAlert({ error }: { error: unknown }) {
  const requestId = error instanceof ApiError ? error.requestId : null;
  return (
    <Alert variant="destructive" role="alert">
      <AlertTitle>{errorMessage(error)}</AlertTitle>
      {requestId ? <AlertDescription>Reference: {requestId}</AlertDescription> : null}
    </Alert>
  );
}

export function LoadMore({
  hasNextPage,
  isFetchingNextPage,
  onClick,
}: {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onClick: () => void;
}) {
  if (!hasNextPage) return null;
  return (
    <Button
      variant="outline"
      className="w-full sm:w-auto"
      onClick={onClick}
      disabled={isFetchingNextPage}
    >
      {isFetchingNextPage ? "Loading…" : "Load more"}
    </Button>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
      {children}
    </p>
  );
}

export function PageTitle({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}

/** A confirm step for destructive actions (the artifact-safe replacement for window.confirm). */
export function ConfirmButton({
  label,
  title,
  description,
  confirmLabel,
  onConfirm,
  variant = "destructive",
  size = "sm",
}: {
  label: string;
  title: string;
  description: string;
  confirmLabel: string;
  onConfirm: () => Promise<unknown>;
  variant?: "destructive" | "outline" | "ghost";
  size?: "sm" | "default";
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      setOpen(false);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button variant={variant} size={size} onClick={() => setOpen(true)}>
        {label}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          {error ? <ErrorAlert error={error} /> : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => void confirm()} disabled={busy}>
              {busy ? "Working…" : confirmLabel}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
