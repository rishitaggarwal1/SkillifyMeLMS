"use client";

import Link from "next/link";
import { CheckCircle2, Circle } from "lucide-react";
import { useId, type ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

export function PageSkeleton({
  kind = "cards",
  rows = 3,
}: {
  kind?: "cards" | "table" | "form";
  rows?: number;
}) {
  return (
    <div aria-label="Loading page" role="status" aria-busy="true" className="flex flex-col gap-4">
      <span className="sr-only">Loading</span>
      <Skeleton className="h-8 w-48" />
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className={cn("state-card flex flex-col gap-3", kind === "table" && "py-3")}>
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className={kind === "form" ? "h-11 w-full" : "h-4 w-1/2"} />
          {kind === "cards" ? <Skeleton className="h-2 w-full" /> : null}
        </div>
      ))}
    </div>
  );
}

export function StatusBadge({
  kind = "info",
  children,
}: {
  kind?: "success" | "warning" | "danger" | "info";
  children: ReactNode;
}) {
  const styles = {
    success: "bg-success-surface text-success",
    warning: "bg-warning-surface text-warning",
    danger: "bg-danger-surface text-danger",
    info: "bg-info-surface text-info",
  };
  return <Badge className={cn("border-transparent", styles[kind])}>{children}</Badge>;
}

export function LiveAnnouncement({
  children,
  urgent = false,
}: {
  children: ReactNode;
  urgent?: boolean;
}) {
  return (
    <span
      role={urgent ? "alert" : "status"}
      aria-live={urgent ? "assertive" : "polite"}
      aria-atomic="true"
      className="sr-only"
    >
      {children}
    </span>
  );
}

export function FormField({
  id: providedId,
  label,
  help,
  error,
  children,
  saving = false,
  disabled = false,
}: {
  id?: string;
  label: string;
  help?: string;
  error?: string;
  children: (props: {
    id: string;
    "aria-describedby": string | undefined;
    "aria-invalid": boolean;
    disabled: boolean;
  }) => ReactNode;
  saving?: boolean;
  disabled?: boolean;
}) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  const description = help || error ? `${id}-description` : undefined;
  return (
    <div className="flex flex-col gap-2" aria-busy={saving}>
      <Label htmlFor={id}>{label}</Label>
      {children({
        id,
        "aria-describedby": description,
        "aria-invalid": !!error,
        disabled: disabled || saving,
      })}
      {description ? (
        <div id={description}>
          {help ? <p className="text-sm text-muted-foreground">{help}</p> : null}
          {error ? (
            <p role="alert" className="text-sm text-danger">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
      <LiveAnnouncement>{saving ? "Saving" : ""}</LiveAnnouncement>
    </div>
  );
}

export type ChecklistStep = { label: string; done: boolean; href: string; onAction?: () => void };
export function FirstRunChecklist({ title, steps }: { title: string; steps: ChecklistStep[] }) {
  if (steps.every((s) => s.done)) return null;
  const next = steps.find((s) => !s.done);
  return (
    <section className="state-card flex flex-col gap-4" aria-label={title}>
      <h2>{title}</h2>
      <ol className="flex flex-col gap-3">
        {steps.map((step, i) => (
          <li key={step.label} className="flex items-center gap-3">
            {step.done ? (
              <CheckCircle2 className="shrink-0 text-success" size={20} aria-hidden="true" />
            ) : (
              <Circle className="shrink-0 text-muted-foreground" size={20} aria-hidden="true" />
            )}
            <span className="sr-only">{step.done ? "Completed" : "Not completed"}: </span>
            <span>
              {i + 1}. {step.label}
            </span>
          </li>
        ))}
      </ol>
      {next ? (
        next.onAction ? (
          <Button className="w-fit" onClick={next.onAction}>
            {next.label}
          </Button>
        ) : (
          <Link className={cn(buttonVariants(), "w-fit")} href={next.href}>
            {next.label}
          </Link>
        )
      ) : null}
    </section>
  );
}
