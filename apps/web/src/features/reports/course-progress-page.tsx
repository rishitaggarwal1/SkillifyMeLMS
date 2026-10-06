"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { NativeSelect } from "@/components/native-select";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { batchQuery } from "@/features/admin/api";
import { EmptyState, ErrorAlert, PageTitle } from "@/features/admin/ui";
import { useMe } from "@/features/auth/queries";
import { allAssignmentsQuery, courseQuery } from "@/features/teach/api";

import { CourseProgress } from "./progress-table";

/** /teach/courses/[id]/progress: per batch of the org that has the course, the students' progress. */
export function CourseProgressPage({ courseId, batchId }: { courseId: string; batchId?: string }) {
  const router = useRouter();
  const { data: me } = useMe();
  const course = useQuery(courseQuery(courseId));
  const assignments = useQuery(allAssignmentsQuery(courseId));
  const orgId = me?.active_organization_id;
  const assignedIds = [
    ...new Set(
      (assignments.data?.items ?? [])
        .filter((a) => a.organization_id === orgId && a.batch_id)
        .map((a) => a.batch_id!),
    ),
  ];
  // Only the batches that have the course (a few), however many batches the org has.
  const batches = useQueries({ queries: assignedIds.map((id) => batchQuery(id)) });
  const choices = batches
    .flatMap((b) => (b.data ? [b.data] : []))
    .sort((x, y) => x.name.localeCompare(y.name));
  const batchesPending = batches.some((b) => b.isPending);
  const selected = batchId && assignedIds.includes(batchId) ? batchId : choices[0]?.id;

  if (course.error)
    return <ErrorAlert error={course.error} onRetry={() => void course.refetch()} />;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <Link href={`/teach/courses/${courseId}`} className="text-sm text-muted-foreground">
          ← {course.data?.title ?? "Course"}
        </Link>
        <PageTitle title="Progress" />
      </div>
      {assignments.isPending || batchesPending ? <Skeleton className="h-10 w-64" /> : null}
      {assignments.isSuccess && !batchesPending && choices.length === 0 ? (
        <EmptyState>None of your organization&apos;s batches has this course yet.</EmptyState>
      ) : null}
      {choices.length > 0 ? (
        <div className="flex max-w-xs flex-col gap-1.5">
          <Label htmlFor="progress-batch">Batch</Label>
          <NativeSelect
            id="progress-batch"
            value={selected}
            onChange={(e) =>
              router.replace(`/teach/courses/${courseId}/progress?batch=${e.target.value}`)
            }
          >
            {choices.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </NativeSelect>
        </div>
      ) : null}
      {selected ? <CourseProgress key={selected} courseId={courseId} batchId={selected} /> : null}
    </div>
  );
}
