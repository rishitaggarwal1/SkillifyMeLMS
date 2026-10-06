"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageSkeleton } from "@/components/patterns/states";
import { useDebounced } from "@/features/admin/hooks";
import { EmptyState, ErrorAlert, LoadMore, PageTitle } from "@/features/admin/ui";
import type { AuditEntry } from "@/lib/api/types";

import { auditQuery } from "./api";

const WHEN = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  dateStyle: "medium",
  timeStyle: "medium",
});

export function PlatformAuditPage({
  organizationId,
  actorUserId,
}: {
  organizationId?: string;
  actorUserId?: string;
}) {
  const [action, setAction] = useState("");
  const [targetType, setTargetType] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const filters = {
    organizationId,
    actorUserId,
    action: useDebounced(action.trim(), 300),
    targetType: useDebounced(targetType.trim(), 300),
    from: from || undefined,
    to: to || undefined,
  };
  const query = useInfiniteQuery(auditQuery(filters));
  const rows = query.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="flex flex-col gap-4">
      <PageTitle title="Audit log" />
      {organizationId || actorUserId ? (
        <p className="flex flex-wrap gap-2 text-sm text-muted-foreground">
          {organizationId ? <span>One organization only.</span> : null}
          {actorUserId ? <span>One person&apos;s actions only.</span> : null}
          <Link href="/platform/audit" className="underline-offset-4 hover:underline">
            Show everything
          </Link>
        </p>
      ) : null}
      <fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-4">
        <legend className="sr-only">Filters</legend>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="audit-action">Action</Label>
          <Input
            id="audit-action"
            placeholder="e.g. user.disabled"
            value={action}
            onChange={(e) => setAction(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="audit-target">Target type</Label>
          <Input
            id="audit-target"
            placeholder="e.g. course"
            value={targetType}
            onChange={(e) => setTargetType(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="audit-from">From (IST)</Label>
          <Input
            id="audit-from"
            type="date"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="audit-to">To (IST, inclusive)</Label>
          <Input id="audit-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
      </fieldset>
      {query.isPending ? <PageSkeleton /> : null}
      {query.error ? <ErrorAlert error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.isSuccess && rows.length === 0 ? <EmptyState>No entries match.</EmptyState> : null}
      <ul className="flex flex-col gap-2" aria-label="Audit entries">
        {rows.map((e) => (
          <AuditRow key={e.id} entry={e} />
        ))}
      </ul>
      <LoadMore
        hasNextPage={query.hasNextPage}
        isFetchingNextPage={query.isFetchingNextPage}
        onClick={() => void query.fetchNextPage()}
      />
    </div>
  );
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const changes = entry.before != null || entry.after != null;
  return (
    <li className="flex flex-col gap-1 rounded-lg border p-3 text-sm">
      <span className="flex flex-wrap items-center justify-between gap-2">
        <code className="font-medium">{entry.action}</code>
        <time dateTime={entry.created_at} className="text-muted-foreground">
          {WHEN.format(new Date(entry.created_at))}
        </time>
      </span>
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
        <span>
          {entry.target_type}
          {entry.target_id ? <span className="break-all"> {entry.target_id}</span> : null}
        </span>
        {entry.actor_user_id ? (
          <Link
            href={`/platform/users/${entry.actor_user_id}`}
            className="underline-offset-4 hover:underline"
          >
            By this person
          </Link>
        ) : (
          <span>By the system</span>
        )}
        {entry.actor_is_platform_admin ? <Badge variant="secondary">Platform admin</Badge> : null}
        {entry.organization_id ? (
          <Link
            href={`/platform/organizations/${entry.organization_id}`}
            className="underline-offset-4 hover:underline"
          >
            Organization
          </Link>
        ) : (
          <span>Platform-level</span>
        )}
      </span>
      {changes ? (
        <details>
          <summary className="cursor-pointer text-muted-foreground">Changes</summary>
          <pre className="mt-2 max-h-64 overflow-auto rounded bg-muted p-2 text-xs">
            {JSON.stringify({ before: entry.before, after: entry.after }, null, 2)}
          </pre>
        </details>
      ) : null}
    </li>
  );
}
