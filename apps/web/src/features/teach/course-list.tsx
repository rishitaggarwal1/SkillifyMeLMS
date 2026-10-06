"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { Label } from "@/components/ui/label";
import { FormField, PageSkeleton } from "@/components/patterns/states";
import { Textarea } from "@/components/ui/textarea";
import { EmptyState, ErrorAlert, LoadMore, PageTitle, errorMessage } from "@/features/admin/ui";
import { hasPermission, useMe } from "@/features/auth/queries";
import type { Course } from "@/lib/api/types";

import { TeachingAttention } from "./overview";
import { coursesQuery, useCreateCourse } from "./api";

export const courseFormSchema = z.object({
  title: z.string().trim().min(1, "Title is required.").max(200, "At most 200 characters."),
  description: z.string().trim().max(5000, "At most 5000 characters."),
  is_public_catalog: z.boolean(),
});
type CourseForm = z.infer<typeof courseFormSchema>;

export function CourseList({ landing = false }: { landing?: boolean }) {
  const { data: me } = useMe();
  const [open, setOpen] = useState(false);
  const canAuthor = hasPermission(me, "course.edit");
  return (
    <div className="flex flex-col gap-6">
      <PageTitle
        title={landing ? "Teaching" : "Courses"}
        actions={canAuthor ? <Button onClick={() => setOpen(true)}>New course</Button> : null}
      />
      {landing ? <TeachingAttention onCreate={() => setOpen(true)} /> : null}
      <CourseSection
        owned
        firstRun={landing}
        title="Your organization's courses"
        empty="No courses yet. Create one to start building lessons."
      />
      <CourseSection
        owned={false}
        title="Assigned to your organization"
        empty="No courses have been assigned to your organization."
      />
      {canAuthor ? <CreateCourseDialog open={open} onOpenChange={setOpen} /> : null}
    </div>
  );
}

function CourseSection({
  owned,
  title,
  empty,
  firstRun = false,
}: {
  owned: boolean;
  title: string;
  empty: string;
  firstRun?: boolean;
}) {
  const query = useInfiniteQuery(coursesQuery(owned));
  const courses = query.data?.pages.flatMap((p) => p.items) ?? [];
  const label = owned ? "Your courses" : "Assigned courses";
  return (
    <section className="flex flex-col gap-3" aria-label={label}>
      <h2 className="text-base font-semibold">{title}</h2>
      {query.isPending ? <PageSkeleton /> : null}
      {query.error ? <ErrorAlert error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.isSuccess && courses.length === 0 && !firstRun ? (
        <EmptyState>{empty}</EmptyState>
      ) : null}
      <ul className="flex flex-col gap-2">
        {courses.map((course) => (
          <CourseRow key={course.id} course={course} />
        ))}
      </ul>
      <LoadMore
        hasNextPage={query.hasNextPage}
        isFetchingNextPage={query.isFetchingNextPage}
        onClick={() => void query.fetchNextPage()}
      />
    </section>
  );
}

function CourseRow({ course }: { course: Course }) {
  return (
    <li>
      <Link
        href={`/teach/courses/${course.id}`}
        className="flex items-center justify-between gap-3 rounded-lg border p-3 hover:bg-muted/50"
      >
        <span className="min-w-0">
          <span className="block truncate font-medium">{course.title}</span>
          {course.description ? (
            <span className="block truncate text-sm text-muted-foreground">
              {course.description}
            </span>
          ) : null}
        </span>
        <span className="flex shrink-0 flex-wrap justify-end gap-1.5">
          {course.status === "archived" ? <Badge variant="secondary">Archived</Badge> : null}
          {course.current_version ? (
            <Badge variant="outline">v{course.current_version.version}</Badge>
          ) : (
            <Badge variant="secondary">Draft</Badge>
          )}
        </span>
      </Link>
    </li>
  );
}

function CreateCourseDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  const create = useCreateCourse();
  const form = useForm<CourseForm>({
    resolver: zodResolver(courseFormSchema),
    defaultValues: { title: "", description: "", is_public_catalog: false },
  });

  async function submit(values: CourseForm) {
    try {
      const course = await create.mutateAsync(values);
      onOpenChange(false);
      form.reset();
      router.push(`/teach/courses/${course.id}`);
    } catch (e) {
      form.setError("root", { message: errorMessage(e) });
    }
  }

  const isPublic = useWatch({ control: form.control, name: "is_public_catalog" });
  const errors = form.formState.errors;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New course</DialogTitle>
          <DialogDescription>You can add modules and lessons next.</DialogDescription>
        </DialogHeader>
        <form onSubmit={form.handleSubmit(submit)} className="flex flex-col gap-4" noValidate>
          <FormField
            id="course-title"
            label="Title"
            error={errors.title?.message}
            saving={form.formState.isSubmitting}
          >
            {(props) => <Input {...props} autoComplete="off" {...form.register("title")} />}
          </FormField>
          <FormField
            id="course-description"
            label="Description (optional)"
            error={errors.description?.message}
            saving={form.formState.isSubmitting}
          >
            {(props) => <Textarea {...props} rows={3} {...form.register("description")} />}
          </FormField>
          <Label className="flex items-center gap-2 font-normal">
            <Checkbox
              checked={isPublic}
              onCheckedChange={(checked) => form.setValue("is_public_catalog", checked === true)}
            />
            List in the public catalog once published
          </Label>
          {errors.root ? (
            <p role="alert" className="text-sm text-destructive">
              {errors.root.message}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={form.formState.isSubmitting}>
              {form.formState.isSubmitting ? "Creating…" : "Create course"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
