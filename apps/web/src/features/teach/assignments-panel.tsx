"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { batchesQuery } from "@/features/admin/api";
import { useDebounced } from "@/features/admin/hooks";
import { ErrorAlert, LoadMore, errorMessage } from "@/features/admin/ui";
import { hasPermission, useMe } from "@/features/auth/queries";
import type { Assignment, Course } from "@/lib/api/types";

import {
  assignmentsQuery,
  directoryNamesQuery,
  directoryQuery,
  useCreateAssignments,
  useDeleteAssignment,
} from "./api";
import { useIsPublisher } from "./teach-shell";

/**
 * Who gets the course. Owners assign to their own batches and, as a content publisher, grant it
 * to other organizations. A receiving org's admin distributes a granted course to their batches
 * and can remove only what their own org created (the API and RLS enforce all of this).
 */
export function AssignmentsPanel({ course }: { course: Course }) {
  const { data: me } = useMe();
  const orgId = me?.active_organization_id ?? null;
  const isPublisher = useIsPublisher();
  const query = useInfiniteQuery(assignmentsQuery(course.id));
  const assignments = query.data?.pages.flatMap((p) => p.items) ?? [];

  const canManage = course.is_owner
    ? hasPermission(me, "course.assign")
    : hasPermission(me, "course.distribute");

  return (
    <section className="flex flex-col gap-4" aria-label="Assignments">
      <h2 className="text-base font-semibold">Who gets this course</h2>
      {query.isPending ? <Skeleton className="h-20 w-full" /> : null}
      {query.error ? <ErrorAlert error={query.error} /> : null}
      {query.isSuccess && orgId ? (
        <>
          <BatchAssignments
            course={course}
            orgId={orgId}
            assignments={assignments}
            canManage={canManage}
          />
          {course.is_owner && isPublisher ? (
            <OrgGrants
              course={course}
              orgId={orgId}
              assignments={assignments}
              canManage={canManage}
            />
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function BatchAssignments({
  course,
  orgId,
  assignments,
  canManage,
}: {
  course: Course;
  orgId: string;
  assignments: Assignment[];
  canManage: boolean;
}) {
  const batches = useInfiniteQuery(batchesQuery("active"));
  const create = useCreateAssignments(course.id);
  const remove = useDeleteAssignment(course.id);
  const list = batches.data?.pages.flatMap((p) => p.items) ?? [];
  const rowFor = (batchId: string) =>
    assignments.find((a) => a.organization_id === orgId && a.batch_id === batchId);
  const hasGrant = assignments.some((a) => a.organization_id === orgId && a.kind === "org_grant");

  async function toggle(batchId: string, on: boolean) {
    try {
      if (on) {
        await create.mutateAsync({ batch_ids: [batchId] });
      } else {
        const row = rowFor(batchId);
        if (row) await remove.mutateAsync(row.id);
      }
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-medium">Your batches</h3>
      {!course.is_owner && !hasGrant ? (
        <p className="text-sm text-muted-foreground">
          The publisher assigned this course to specific batches; only they can change which.
        </p>
      ) : null}
      {batches.error ? <ErrorAlert error={batches.error} /> : null}
      {batches.isSuccess && list.length === 0 ? (
        <p className="text-sm text-muted-foreground">Your organization has no batches yet.</p>
      ) : null}
      <ul className="flex flex-col gap-1.5" aria-label="Batches">
        {list.map((batch) => {
          const row = rowFor(batch.id);
          // Rows the publisher created can only be removed by the publisher.
          const lockedByPublisher = !!row && row.assigned_by_org_id !== orgId;
          const canAdd = course.is_owner || hasGrant;
          const disabled = !canManage || lockedByPublisher || (!row && !canAdd) || create.isPending;
          return (
            <li key={batch.id}>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={!!row}
                  disabled={disabled}
                  onCheckedChange={(on) => void toggle(batch.id, on === true)}
                />
                <span>{batch.name}</span>
                <span className="text-muted-foreground tabular-nums">
                  {batch.member_count} members
                </span>
                {lockedByPublisher ? <Badge variant="secondary">Set by publisher</Badge> : null}
              </label>
            </li>
          );
        })}
      </ul>
      <LoadMore
        hasNextPage={batches.hasNextPage}
        isFetchingNextPage={batches.isFetchingNextPage}
        onClick={() => void batches.fetchNextPage()}
      />
    </div>
  );
}

function OrgGrants({
  course,
  orgId,
  assignments,
  canManage,
}: {
  course: Course;
  orgId: string;
  assignments: Assignment[];
  canManage: boolean;
}) {
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim(), 300);
  const create = useCreateAssignments(course.id);
  const remove = useDeleteAssignment(course.id);
  const grants = assignments.filter((a) => a.kind === "org_grant" && a.organization_id !== orgId);
  const directBatches = assignments.filter(
    (a) => a.kind === "batch" && a.organization_id !== orgId,
  );
  const otherOrgIds = [...new Set([...grants, ...directBatches].map((a) => a.organization_id))];
  const names = useQuery(directoryNamesQuery(otherOrgIds));
  const results = useQuery({ ...directoryQuery(q), enabled: canManage && q.length > 0 });
  const nameOf = (id: string) => names.data?.get(id) ?? "Organization";

  async function grant(organizationId: string) {
    try {
      await create.mutateAsync({ organization_id: organizationId, batch_ids: [] });
      setSearch("");
      toast.success(`Granted to ${results.data?.items.find((o) => o.id === organizationId)?.name}`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-medium">Other organizations</h3>
      <p className="text-sm text-muted-foreground">
        A grant lets the organization&apos;s admin choose which of their batches get the course.
      </p>
      {grants.length ? (
        <ul className="flex flex-col gap-1.5" aria-label="Organization grants">
          {grants.map((row) => {
            const batchCount = directBatches.filter(
              (b) => b.organization_id === row.organization_id,
            ).length;
            return (
              <li key={row.id} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium">{nameOf(row.organization_id)}</span>
                {batchCount ? (
                  <Badge variant="secondary">
                    {batchCount} batch{batchCount === 1 ? "" : "es"}
                  </Badge>
                ) : null}
                {canManage && row.assigned_by_org_id === orgId ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      remove.mutateAsync(row.id).catch((e) => toast.error(errorMessage(e)))
                    }
                  >
                    Remove
                  </Button>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">Not granted to any organization yet.</p>
      )}
      {canManage ? (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`grant-search-${course.id}`}>Grant to an organization</Label>
          <Input
            id={`grant-search-${course.id}`}
            type="search"
            placeholder="Search organizations by name"
            value={search}
            maxLength={100}
            onChange={(event) => setSearch(event.target.value)}
          />
          {results.error ? <ErrorAlert error={results.error} /> : null}
          {results.data ? (
            <ul className="flex flex-col gap-1" aria-label="Organization search results">
              {results.data.items
                .filter((org) => org.id !== orgId)
                .map((org) => {
                  const granted = grants.some((g) => g.organization_id === org.id);
                  return (
                    <li key={org.id} className="flex items-center justify-between gap-2 text-sm">
                      <span>{org.name}</span>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={granted || create.isPending}
                        onClick={() => void grant(org.id)}
                      >
                        {granted ? "Granted" : "Grant"}
                      </Button>
                    </li>
                  );
                })}
              {results.data.items.length === 0 ? (
                <li className="text-sm text-muted-foreground">No organizations match.</li>
              ) : null}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
