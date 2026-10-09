"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "@/lib/toast";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { PageSkeleton } from "@/components/patterns/states";

import {
  batchMembersQuery,
  batchQuery,
  membersQuery,
  useAddBatchMembers,
  useArchiveBatch,
  useRemoveBatchMember,
} from "./api";
import { BatchCourses } from "@/features/reports/batch-courses";

import { useDebounced } from "./hooks";
import { ConfirmButton, EmptyState, ErrorAlert, LoadMore, PageTitle, errorMessage } from "./ui";

export function BatchDetailPage({ batchId }: { batchId: string }) {
  const router = useRouter();
  const batch = useQuery(batchQuery(batchId));
  const members = useInfiniteQuery(batchMembersQuery(batchId));
  const remove = useRemoveBatchMember(batchId);
  const archive = useArchiveBatch();
  const [adding, setAdding] = useState(false);

  if (batch.isPending) return <PageSkeleton />;
  if (batch.error) return <ErrorAlert error={batch.error} onRetry={() => void batch.refetch()} />;
  const b = batch.data;
  const rows = members.data?.pages.flatMap((p) => p.items) ?? [];
  const active = b.status === "active";

  return (
    <div className="flex flex-col gap-4">
      <Link href="/admin/batches" className="text-sm text-muted-foreground hover:text-foreground">
        ← All batches
      </Link>
      <PageTitle
        title={b.name}
        actions={
          active ? (
            <>
              <Button onClick={() => setAdding(true)}>Add members</Button>
              <Link
                href={`/admin/imports?batch=${b.id}`}
                className={buttonVariants({ variant: "outline" })}
              >
                Import CSV
              </Link>
              <ConfirmButton
                label="Archive"
                variant="outline"
                size="default"
                title={`Archive ${b.name}?`}
                description="Members and history are kept, but no one can be added to an archived batch."
                confirmLabel="Archive batch"
                onConfirm={async () => {
                  await archive.mutateAsync(b.id);
                  toast.success(`Archived ${b.name}`);
                  router.push("/admin/batches");
                }}
              />
            </>
          ) : (
            <Badge variant="secondary">Archived</Badge>
          )
        }
      />
      {b.description ? <p className="text-sm text-muted-foreground">{b.description}</p> : null}
      <BatchCourses batchId={b.id} />
      <h2 className="text-base font-medium">
        Members <span className="text-muted-foreground tabular-nums">({b.member_count})</span>
      </h2>
      {members.error ? (
        <ErrorAlert error={members.error} onRetry={() => void members.refetch()} />
      ) : null}
      {members.isSuccess && rows.length === 0 ? (
        <EmptyState>No members yet. Add existing members or import a CSV of students.</EmptyState>
      ) : null}
      <ul className="flex flex-col gap-2" aria-label="Batch members">
        {rows.map((m) => (
          <li
            key={m.user.id}
            className="flex items-center justify-between gap-3 rounded-lg border p-3"
          >
            <span className="min-w-0">
              <span className="block truncate font-medium">{m.user.full_name || m.user.email}</span>
              <span className="block truncate text-sm text-muted-foreground">{m.user.email}</span>
            </span>
            {active ? (
              <ConfirmButton
                label="Remove"
                variant="ghost"
                title="Remove from batch?"
                description={`${m.user.email} will leave ${b.name} (but stay in the organization).`}
                confirmLabel="Remove"
                onConfirm={() => remove.mutateAsync(m.user.id)}
              />
            ) : null}
          </li>
        ))}
      </ul>
      <LoadMore
        hasNextPage={members.hasNextPage}
        isFetchingNextPage={members.isFetchingNextPage}
        onClick={() => void members.fetchNextPage()}
      />
      {adding ? <AddMembersDialog batchId={b.id} onClose={() => setAdding(false)} /> : null}
    </div>
  );
}

function AddMembersDialog({ batchId, onClose }: { batchId: string; onClose: () => void }) {
  const [q, setQ] = useState("");
  const debounced = useDebounced(q, 300);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const candidates = useInfiniteQuery(membersQuery({ q: debounced || undefined }));
  const add = useAddBatchMembers(batchId);
  const rows = (candidates.data?.pages.flatMap((p) => p.items) ?? []).filter(
    (m) => !m.batch_ids.includes(batchId),
  );

  function toggle(id: string, on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  async function submit() {
    try {
      const result = await add.mutateAsync([...selected]);
      toast.success(`Added ${result.added.length} member${result.added.length === 1 ? "" : "s"}`);
      onClose();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add members</DialogTitle>
          <DialogDescription>People must already belong to the organization.</DialogDescription>
        </DialogHeader>
        <Input
          type="search"
          placeholder="Search by name or email"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search members"
        />
        <ul className="flex max-h-72 flex-col gap-1 overflow-y-auto" aria-label="Candidates">
          {rows.map((m) => (
            <li key={m.user.id}>
              <label className="flex cursor-pointer items-center gap-3 rounded-md p-2 hover:bg-muted">
                <Checkbox
                  checked={selected.has(m.user.id)}
                  onCheckedChange={(checked) => toggle(m.user.id, checked === true)}
                />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">
                    {m.user.full_name || m.user.email}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {m.user.email}
                  </span>
                </span>
              </label>
            </li>
          ))}
          {candidates.isSuccess && rows.length === 0 ? (
            <li className="p-2 text-sm text-muted-foreground">No matching members.</li>
          ) : null}
        </ul>
        <LoadMore
          hasNextPage={candidates.hasNextPage}
          isFetchingNextPage={candidates.isFetchingNextPage}
          onClick={() => void candidates.fetchNextPage()}
        />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={selected.size === 0 || add.isPending}>
            Add {selected.size || ""}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
