"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorAlert, PageTitle } from "@/features/admin/ui";
import { hasPermission, useMe } from "@/features/auth/queries";
import type { Course } from "@/lib/api/types";

import { courseQuery, versionsQuery } from "./api";
import { AssignmentsPanel } from "./assignments-panel";
import { GradingSection } from "./grading";
import { OutlineEditor } from "./outline-editor";
import { PublishDialog } from "./publish-dialog";

export function CoursePage({ courseId }: { courseId: string }) {
  const course = useQuery(courseQuery(courseId));
  if (course.isPending) return <Skeleton className="h-48 w-full" />;
  if (course.error) return <ErrorAlert error={course.error} />;
  return course.data.is_owner ? (
    <CourseEditor course={course.data} />
  ) : (
    <AssignedCourse course={course.data} />
  );
}

function Header({ course, actions }: { course: Course; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <Link href="/teach/courses" className="text-sm text-muted-foreground hover:underline">
        ← Courses
      </Link>
      <PageTitle title={course.title} actions={actions} />
      <div className="flex flex-wrap gap-1.5">
        {course.current_version ? (
          <Badge variant="outline">Published v{course.current_version.version}</Badge>
        ) : (
          <Badge variant="secondary">Not published yet</Badge>
        )}
        {course.is_public_catalog ? <Badge variant="outline">Public catalog</Badge> : null}
        {course.status === "archived" ? <Badge variant="secondary">Archived</Badge> : null}
      </div>
    </div>
  );
}

function CourseEditor({ course }: { course: Course }) {
  const { data: me } = useMe();
  const [publishing, setPublishing] = useState(false);
  const canEdit = hasPermission(me, "course.edit");
  return (
    <div className="flex flex-col gap-6">
      <Header
        course={course}
        actions={
          canEdit && course.status === "active" ? (
            <Button onClick={() => setPublishing(true)}>Publish…</Button>
          ) : null
        }
      />
      {course.current_version ? <StudentLink courseId={course.id} /> : null}
      <GradingSection course={course} />
      <OutlineEditor courseId={course.id} />
      <Versions courseId={course.id} />
      {course.current_version ? (
        <AssignmentsPanel course={course} />
      ) : (
        <p className="text-sm text-muted-foreground">Publish the course to assign it to batches.</p>
      )}
      {publishing ? <PublishDialog course={course} onClose={() => setPublishing(false)} /> : null}
    </div>
  );
}

function AssignedCourse({ course }: { course: Course }) {
  return (
    <div className="flex flex-col gap-6">
      <Header course={course} />
      <p className="max-w-prose text-sm text-muted-foreground">
        This course is assigned to your organization by its publisher. You can read it and choose
        which of your batches get it; only the publisher can edit it.
      </p>
      <StudentLink courseId={course.id} />
      <GradingSection course={course} />
      <Versions courseId={course.id} />
      <AssignmentsPanel course={course} />
    </div>
  );
}

/** The stable link instructors share with students: it resolves to each student's enrollment. */
function StudentLink({ courseId }: { courseId: string }) {
  const link = `${window.location.origin}/learn/courses/${courseId}`;
  return (
    <div className="flex max-w-xl flex-col gap-1.5">
      <Label htmlFor={`student-link-${courseId}`}>Student link</Label>
      <div className="flex gap-2">
        <Input
          id={`student-link-${courseId}`}
          readOnly
          value={link}
          onFocus={(e) => e.target.select()}
        />
        <Button
          variant="outline"
          onClick={() =>
            void navigator.clipboard
              .writeText(link)
              .then(() => toast.success("Link copied"))
              .catch(() => toast.error("Copy failed; select the link and copy it."))
          }
        >
          Copy
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Opens the course for students in an assigned batch; others see “not found”.
      </p>
    </div>
  );
}

function Versions({ courseId }: { courseId: string }) {
  const versions = useQuery(versionsQuery(courseId));
  if (!versions.data?.items.length) return null;
  return (
    <section className="flex flex-col gap-2" aria-label="Versions">
      <h2 className="text-base font-semibold">Published versions</h2>
      <ul className="flex flex-col gap-1 text-sm">
        {versions.data.items.map((v) => (
          <li key={v.id} className="flex flex-wrap items-center gap-2">
            <span className="font-medium tabular-nums">v{v.version}</span>
            <Badge variant={v.release_type === "major" ? "default" : "secondary"}>
              {v.release_type}
            </Badge>
            <time className="text-muted-foreground" dateTime={v.published_at}>
              {new Date(v.published_at).toLocaleString("en-IN", {
                dateStyle: "medium",
                timeStyle: "short",
              })}
            </time>
          </li>
        ))}
      </ul>
    </section>
  );
}
