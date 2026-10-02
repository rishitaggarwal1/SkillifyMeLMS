"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ConfirmButton,
  EmptyState,
  ErrorAlert,
  PageTitle,
  errorMessage,
} from "@/features/admin/ui";
import type { Organization } from "@/lib/api/types";

import { organizationQuery, useInviteOrgAdmin, useUpdateOrganization, usersQuery } from "./api";
import { organizationFormSchema } from "./organizations";
import { StatusBadge } from "./users";

const editSchema = organizationFormSchema.pick({ name: true, is_content_publisher: true });
type EditForm = z.infer<typeof editSchema>;

export const inviteAdminSchema = z.object({
  email: z.email("Enter a valid email address."),
  full_name: z.string().trim().max(200, "At most 200 characters."),
});
type InviteAdminForm = z.infer<typeof inviteAdminSchema>;

export function OrganizationDetailPage({ organizationId }: { organizationId: string }) {
  const query = useQuery(organizationQuery(organizationId));
  if (query.isPending) return <Skeleton className="h-40 w-full" />;
  if (query.error) return <ErrorAlert error={query.error} />;
  const org = query.data;
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <Link href="/platform/organizations" className="text-sm text-muted-foreground">
          ← Organizations
        </Link>
        <PageTitle title={org.name} />
        <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <span>{org.slug}</span>
          {org.is_content_publisher ? <Badge variant="secondary">Publisher</Badge> : null}
          {org.status === "archived" ? <Badge variant="outline">Archived</Badge> : null}
        </p>
        <nav aria-label="About this organization" className="flex flex-wrap gap-3 text-sm">
          {[
            ["Members", `/platform/users?organization_id=${org.id}`],
            ["Courses", `/platform/courses?organization_id=${org.id}`],
            ["Audit log", `/platform/audit?organization_id=${org.id}`],
          ].map(([label, href]) => (
            <Link key={label} href={href!} className="underline underline-offset-4">
              {label}
            </Link>
          ))}
        </nav>
      </div>
      {org.status === "active" ? <InviteAdmin organizationId={org.id} /> : null}
      <OrgAdmins organizationId={org.id} />
      <EditOrganization key={org.updated_at} org={org} />
    </div>
  );
}

function InviteAdmin({ organizationId }: { organizationId: string }) {
  const invite = useInviteOrgAdmin(organizationId);
  const form = useForm<InviteAdminForm>({
    resolver: zodResolver(inviteAdminSchema),
    defaultValues: { email: "", full_name: "" },
  });
  const errors = form.formState.errors;

  async function submit(values: InviteAdminForm) {
    try {
      const invitation = await invite.mutateAsync(values);
      toast.success(`Invitation sent to ${invitation.email}`);
      form.reset();
    } catch (e) {
      form.setError("root", { message: errorMessage(e) });
    }
  }

  return (
    <section
      aria-labelledby="invite-admin-heading"
      className="flex flex-col gap-3 rounded-lg border p-4"
    >
      <h2 id="invite-admin-heading" className="text-base font-semibold">
        Invite an org admin
      </h2>
      <p className="text-sm text-muted-foreground">
        They get an email to set a password, then manage this organization&apos;s batches, members
        and courses.
      </p>
      <form
        onSubmit={form.handleSubmit(submit)}
        className="flex flex-col gap-3"
        aria-label="Invite an org admin"
        noValidate
      >
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="admin-email">Email</Label>
          <Input
            id="admin-email"
            type="email"
            autoComplete="off"
            {...form.register("email")}
            aria-invalid={!!errors.email}
          />
          {errors.email ? <p className="text-sm text-destructive">{errors.email.message}</p> : null}
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="admin-name">Full name (optional)</Label>
          <Input id="admin-name" autoComplete="off" {...form.register("full_name")} />
        </div>
        {errors.root ? (
          <p role="alert" className="text-sm text-destructive">
            {errors.root.message}
          </p>
        ) : null}
        <Button type="submit" className="self-start" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? "Sending…" : "Send invitation"}
        </Button>
      </form>
    </section>
  );
}

function OrgAdmins({ organizationId }: { organizationId: string }) {
  const query = useInfiniteQuery(usersQuery({ organizationId, role: "org_admin" }));
  const rows = query.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <section aria-labelledby="org-admins-heading" className="flex flex-col gap-2">
      <h2 id="org-admins-heading" className="text-base font-semibold">
        Org admins
      </h2>
      {query.isPending ? <Skeleton className="h-12 w-full" /> : null}
      {query.error ? <ErrorAlert error={query.error} /> : null}
      {query.isSuccess && rows.length === 0 ? <EmptyState>No org admin yet.</EmptyState> : null}
      <ul className="flex flex-col gap-2" aria-label="Org admins">
        {rows.map((u) => (
          <li key={u.id}>
            <Link
              href={`/platform/users/${u.id}`}
              className="flex items-center justify-between gap-3 rounded-lg border p-3 hover:bg-muted/50"
            >
              <span className="min-w-0">
                <span className="block truncate font-medium">{u.full_name || u.email}</span>
                <span className="block truncate text-sm text-muted-foreground">{u.email}</span>
              </span>
              <StatusBadge status={u.status} />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

function EditOrganization({ org }: { org: Organization }) {
  const update = useUpdateOrganization(org.id);
  const form = useForm<EditForm>({
    resolver: zodResolver(editSchema),
    defaultValues: { name: org.name, is_content_publisher: org.is_content_publisher },
  });
  const errors = form.formState.errors;

  async function submit(values: EditForm) {
    try {
      await update.mutateAsync(values);
      toast.success("Saved");
    } catch (e) {
      form.setError("root", { message: errorMessage(e) });
    }
  }

  return (
    <section
      aria-labelledby="edit-org-heading"
      className="flex flex-col gap-3 rounded-lg border p-4"
    >
      <h2 id="edit-org-heading" className="text-base font-semibold">
        Settings
      </h2>
      <form onSubmit={form.handleSubmit(submit)} className="flex flex-col gap-3" noValidate>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="edit-org-name">Name</Label>
          <Input id="edit-org-name" {...form.register("name")} aria-invalid={!!errors.name} />
          {errors.name ? <p className="text-sm text-destructive">{errors.name.message}</p> : null}
        </div>
        <Controller
          control={form.control}
          name="is_content_publisher"
          render={({ field }) => (
            <div className="flex items-center gap-2">
              <Checkbox
                id="edit-org-publisher"
                checked={field.value}
                onCheckedChange={(v) => field.onChange(v === true)}
              />
              <Label htmlFor="edit-org-publisher">Content publisher</Label>
            </div>
          )}
        />
        {errors.root ? (
          <p role="alert" className="text-sm text-destructive">
            {errors.root.message}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={form.formState.isSubmitting || !form.formState.isDirty}>
            Save
          </Button>
          {org.status === "active" ? (
            <ConfirmButton
              label="Archive"
              size="default"
              title={`Archive ${org.name}?`}
              description="Its members lose access at once. Nothing is deleted; you can restore it later."
              confirmLabel="Archive"
              onConfirm={() => update.mutateAsync({ status: "archived" })}
            />
          ) : (
            <Button
              type="button"
              variant="outline"
              onClick={() =>
                void update.mutateAsync({ status: "active" }).then(
                  () => toast.success("Restored"),
                  (e: unknown) => toast.error(errorMessage(e)),
                )
              }
            >
              Restore
            </Button>
          )}
        </div>
      </form>
    </section>
  );
}
