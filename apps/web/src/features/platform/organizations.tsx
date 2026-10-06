"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { NativeSelect } from "@/components/native-select";
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
import { useDebounced } from "@/features/admin/hooks";
import { EmptyState, ErrorAlert, LoadMore, PageTitle, errorMessage } from "@/features/admin/ui";

import { organizationsQuery, useCreateOrganization } from "./api";

export const SLUG_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;

/** A URL-safe slug from a name ("St. Xavier's College" -> "st-xavier-s-college"). */
export function slugify(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80)
    .replace(/-+$/, "");
}

export const organizationFormSchema = z.object({
  name: z.string().trim().min(1, "Name is required.").max(200, "At most 200 characters."),
  slug: z
    .string()
    .trim()
    .min(2, "At least 2 characters.")
    .max(80, "At most 80 characters.")
    .regex(SLUG_PATTERN, "Lowercase letters, digits and single hyphens only."),
  is_content_publisher: z.boolean(),
});
type OrganizationForm = z.infer<typeof organizationFormSchema>;

export function OrganizationsPage() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<"" | "active" | "archived">("active");
  const [creating, setCreating] = useState(false);
  const debounced = useDebounced(q.trim(), 300);
  const query = useInfiniteQuery(
    organizationsQuery({ q: debounced || undefined, status: status || undefined }),
  );
  const rows = query.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="flex flex-col gap-4">
      <PageTitle
        title="Organizations"
        actions={<Button onClick={() => setCreating(true)}>New organization</Button>}
      />
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          type="search"
          placeholder="Search by name"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search organizations"
        />
        <NativeSelect
          aria-label="Filter by status"
          className="sm:w-48"
          value={status}
          onChange={(e) => setStatus(e.target.value as "" | "active" | "archived")}
        >
          <option value="active">Active</option>
          <option value="archived">Archived</option>
          <option value="">All</option>
        </NativeSelect>
      </div>
      {query.isPending ? <PageSkeleton /> : null}
      {query.error ? <ErrorAlert error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.isSuccess && rows.length === 0 ? (
        <EmptyState>No organizations match.</EmptyState>
      ) : null}
      <ul className="flex flex-col gap-2" aria-label="Organizations">
        {rows.map((o) => (
          <li key={o.id}>
            <Link
              href={`/platform/organizations/${o.id}`}
              className="flex items-center justify-between gap-3 rounded-lg border p-3 hover:bg-muted/50"
            >
              <span className="min-w-0">
                <span className="block truncate font-medium">{o.name}</span>
                <span className="block truncate text-sm text-muted-foreground">{o.slug}</span>
              </span>
              <span className="flex shrink-0 flex-wrap justify-end gap-1">
                {o.is_content_publisher ? <Badge variant="secondary">Publisher</Badge> : null}
                {o.status === "archived" ? <Badge variant="outline">Archived</Badge> : null}
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
      {creating ? <CreateOrganizationDialog onClose={() => setCreating(false)} /> : null}
    </div>
  );
}

function CreateOrganizationDialog({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const create = useCreateOrganization();
  const [slugEdited, setSlugEdited] = useState(false);
  const form = useForm<OrganizationForm>({
    resolver: zodResolver(organizationFormSchema),
    defaultValues: { name: "", slug: "", is_content_publisher: false },
  });
  const errors = form.formState.errors;

  async function submit(values: OrganizationForm) {
    try {
      const org = await create.mutateAsync(values);
      toast.success(`Created ${org.name}`);
      router.push(`/platform/organizations/${org.id}`);
    } catch (e) {
      form.setError("root", { message: errorMessage(e) });
    }
  }

  const name = form.register("name");
  return (
    <Dialog open onOpenChange={(open) => (open ? null : onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New organization</DialogTitle>
          <DialogDescription>
            A college or training partner. Invite its first admin on the next screen.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={form.handleSubmit(submit)} className="flex flex-col gap-4" noValidate>
          <FormField
            id="org-name"
            label="Name"
            error={errors.name?.message}
            saving={form.formState.isSubmitting}
          >
            {(props) => (
              <Input
                {...props}
                autoComplete="off"
                {...name}
                onChange={(e) => {
                  void name.onChange(e);
                  if (!slugEdited) form.setValue("slug", slugify(e.target.value));
                }}
              />
            )}
          </FormField>
          <FormField
            id="org-slug"
            label="Slug"
            error={errors.slug?.message}
            saving={form.formState.isSubmitting}
            help="A short unique id, e.g. st-xaviers-mumbai. It can't be changed later."
          >
            {(props) => (
              <Input
                {...props}
                autoComplete="off"
                {...form.register("slug", { onChange: () => setSlugEdited(true) })}
              />
            )}
          </FormField>
          <Controller
            control={form.control}
            name="is_content_publisher"
            render={({ field }) => (
              <div className="flex items-start gap-2">
                <Checkbox
                  id="org-publisher"
                  checked={field.value}
                  onCheckedChange={(v) => field.onChange(v === true)}
                />
                <Label htmlFor="org-publisher" className="flex flex-col items-start gap-0.5">
                  <span>Content publisher</span>
                  <span className="text-xs font-normal text-muted-foreground">
                    Authors courses for other organizations, and sees every organization&apos;s name
                    in the course-sharing directory.
                  </span>
                </Label>
              </div>
            )}
          />
          {errors.root ? (
            <p role="alert" className="text-sm text-destructive">
              {errors.root.message}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={form.formState.isSubmitting}>
              {form.formState.isSubmitting ? "Creating…" : "Create organization"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
