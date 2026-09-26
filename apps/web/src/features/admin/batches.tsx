"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";

import { batchesQuery, useCreateBatch } from "./api";
import { EmptyState, ErrorAlert, LoadMore, PageTitle, errorMessage } from "./ui";

export const batchFormSchema = z.object({
  name: z.string().trim().min(1, "Name is required.").max(120, "At most 120 characters."),
  description: z.string().trim().max(1000, "At most 1000 characters."),
});
type BatchForm = z.infer<typeof batchFormSchema>;

export function BatchesPage() {
  const [open, setOpen] = useState(false);
  const query = useInfiniteQuery(batchesQuery());
  const batches = query.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="flex flex-col gap-4">
      <PageTitle
        title="Batches"
        actions={<Button onClick={() => setOpen(true)}>New batch</Button>}
      />
      {query.isPending ? <Skeleton className="h-24 w-full" /> : null}
      {query.error ? <ErrorAlert error={query.error} /> : null}
      {query.isSuccess && batches.length === 0 ? (
        <EmptyState>No batches yet. Create one to group students, e.g. “CSE 2026”.</EmptyState>
      ) : null}
      <ul className="flex flex-col gap-2" aria-label="Batches">
        {batches.map((b) => (
          <li key={b.id}>
            <Link
              href={`/admin/batches/${b.id}`}
              className="flex items-center justify-between gap-3 rounded-lg border p-3 hover:bg-muted/50"
            >
              <span className="min-w-0">
                <span className="block truncate font-medium">{b.name}</span>
                {b.description ? (
                  <span className="block truncate text-sm text-muted-foreground">
                    {b.description}
                  </span>
                ) : null}
              </span>
              <span className="flex shrink-0 items-center gap-2 text-sm text-muted-foreground">
                <span className="tabular-nums">{b.member_count} members</span>
                {b.status === "archived" ? <Badge variant="secondary">Archived</Badge> : null}
              </span>
            </Link>
          </li>
        ))}
      </ul>
      <LoadMore
        hasNextPage={query.hasNextPage}
        isFetchingNextPage={query.isFetchingNextPage}
        onClick={() => void query.fetchNextPage()}
      />
      <CreateBatchDialog open={open} onOpenChange={setOpen} />
    </div>
  );
}

function CreateBatchDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const create = useCreateBatch();
  const form = useForm<BatchForm>({
    resolver: zodResolver(batchFormSchema),
    defaultValues: { name: "", description: "" },
  });

  async function submit(values: BatchForm) {
    try {
      const batch = await create.mutateAsync(values);
      toast.success(`Created ${batch.name}`);
      form.reset();
      onOpenChange(false);
    } catch (e) {
      form.setError("root", { message: errorMessage(e) });
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New batch</DialogTitle>
          <DialogDescription>
            A group of students, e.g. a branch and graduation year.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={form.handleSubmit(submit)} className="flex flex-col gap-4" noValidate>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="batch-name">Name</Label>
            <Input
              id="batch-name"
              autoComplete="off"
              {...form.register("name")}
              aria-invalid={!!form.formState.errors.name}
            />
            {form.formState.errors.name ? (
              <p className="text-sm text-destructive">{form.formState.errors.name.message}</p>
            ) : null}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="batch-description">Description (optional)</Label>
            <Textarea id="batch-description" rows={3} {...form.register("description")} />
          </div>
          {form.formState.errors.root ? (
            <p role="alert" className="text-sm text-destructive">
              {form.formState.errors.root.message}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={form.formState.isSubmitting}>
              {form.formState.isSubmitting ? "Creating…" : "Create batch"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
