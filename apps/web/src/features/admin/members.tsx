"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
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
import { FormField, PageSkeleton } from "@/components/patterns/states";
import { ORG_ROLES, ROLE_LABELS, type Member, type OrgRole } from "@/lib/api/types";

import {
  allBatchesQuery,
  invitationsQuery,
  membersQuery,
  useInvite,
  useRemoveMember,
  useResendInvitation,
  useRevokeInvitation,
  useUpdateMemberRoles,
} from "./api";
import { useDebounced } from "./hooks";
import { ConfirmButton, EmptyState, ErrorAlert, LoadMore, PageTitle, errorMessage } from "./ui";

const roleEnum = z.enum(["student", "instructor", "lab_author", "org_admin"]);

export const inviteSchema = z.object({
  email: z.email("Enter a valid email address."),
  full_name: z.string().trim().max(200),
  roles: z.array(roleEnum).min(1, "Choose at least one role."),
  batch_ids: z.array(z.string()),
});
type InviteForm = z.infer<typeof inviteSchema>;

export function MembersPage() {
  const [q, setQ] = useState("");
  const [role, setRole] = useState<OrgRole | "">("");
  const debounced = useDebounced(q.trim(), 300);
  const query = useInfiniteQuery(
    membersQuery({ q: debounced || undefined, role: role || undefined }),
  );
  const [inviting, setInviting] = useState(false);
  const [editing, setEditing] = useState<Member | null>(null);
  const rows = query.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="flex flex-col gap-4">
      <PageTitle
        title="Members"
        actions={<Button onClick={() => setInviting(true)}>Invite</Button>}
      />
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          type="search"
          placeholder="Search by name or email"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search members"
        />
        <NativeSelect
          aria-label="Filter by role"
          className="sm:w-48"
          value={role}
          onChange={(e) => setRole(e.target.value as OrgRole | "")}
        >
          <option value="">All roles</option>
          {ORG_ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABELS[r]}
            </option>
          ))}
        </NativeSelect>
      </div>
      {query.isPending ? <PageSkeleton /> : null}
      {query.error ? <ErrorAlert error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.isSuccess && rows.length === 0 ? <EmptyState>No members match.</EmptyState> : null}
      <ul className="flex flex-col gap-2" aria-label="Members">
        {rows.map((m) => (
          <MemberRow key={m.user.id} member={m} onEdit={() => setEditing(m)} />
        ))}
      </ul>
      <LoadMore
        hasNextPage={query.hasNextPage}
        isFetchingNextPage={query.isFetchingNextPage}
        onClick={() => void query.fetchNextPage()}
      />
      <PendingInvitations />
      {inviting ? <InviteDialog onClose={() => setInviting(false)} /> : null}
      {editing ? <EditRolesDialog member={editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

function MemberRow({ member, onEdit }: { member: Member; onEdit: () => void }) {
  const remove = useRemoveMember();
  return (
    <li className="flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between">
      <span className="min-w-0">
        <span className="block truncate font-medium">
          {member.user.full_name || member.user.email}
        </span>
        <span className="block truncate text-sm text-muted-foreground">{member.user.email}</span>
        <span className="mt-1 flex flex-wrap gap-1">
          {member.roles.map((r) => (
            <Badge key={r} variant="secondary">
              {ROLE_LABELS[r]}
            </Badge>
          ))}
          {member.user.status === "invited" ? <Badge variant="outline">Invited</Badge> : null}
        </span>
      </span>
      <span className="flex shrink-0 gap-2">
        <Button variant="outline" size="sm" onClick={onEdit}>
          Roles
        </Button>
        <ConfirmButton
          label="Remove"
          title="Remove from organization?"
          description={`${member.user.email} will lose access to this organization and leave all its batches.`}
          confirmLabel="Remove"
          onConfirm={() => remove.mutateAsync(member.user.id)}
        />
      </span>
    </li>
  );
}

function RoleCheckboxes({
  value,
  onChange,
}: {
  value: OrgRole[];
  onChange: (roles: OrgRole[]) => void;
}) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 text-sm font-medium">Roles</legend>
      {ORG_ROLES.map((r) => (
        <label key={r} className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={value.includes(r)}
            onCheckedChange={(on) =>
              onChange(on === true ? [...value, r] : value.filter((x) => x !== r))
            }
          />
          {ROLE_LABELS[r]}
        </label>
      ))}
    </fieldset>
  );
}

function EditRolesDialog({ member, onClose }: { member: Member; onClose: () => void }) {
  const [roles, setRoles] = useState<OrgRole[]>(member.roles);
  const update = useUpdateMemberRoles();

  async function save() {
    try {
      await update.mutateAsync({ userId: member.user.id, roles });
      toast.success("Roles updated");
      onClose();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Roles for {member.user.full_name || member.user.email}</DialogTitle>
          <DialogDescription>Roles apply only in this organization.</DialogDescription>
        </DialogHeader>
        <RoleCheckboxes value={roles} onChange={setRoles} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void save()} disabled={roles.length === 0 || update.isPending}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function InviteDialog({ onClose }: { onClose: () => void }) {
  const invite = useInvite();
  const batches = useQuery(allBatchesQuery("active"));
  const batchOptions = batches.data?.items ?? [];
  const form = useForm<InviteForm>({
    resolver: zodResolver(inviteSchema),
    defaultValues: { email: "", full_name: "", roles: ["student"], batch_ids: [] },
  });

  async function submit(values: InviteForm) {
    try {
      await invite.mutateAsync(values);
      toast.success(`Invitation sent to ${values.email}`);
      onClose();
    } catch (e) {
      form.setError("root", { message: errorMessage(e) });
    }
  }

  const errors = form.formState.errors;
  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invite someone</DialogTitle>
          <DialogDescription>
            New people get an email to set their password; existing accounts are added right away.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={form.handleSubmit(submit)} className="flex flex-col gap-4" noValidate>
          <FormField
            id="invite-email"
            label="Email"
            error={errors.email?.message}
            saving={form.formState.isSubmitting}
          >
            {(props) => (
              <Input {...props} type="email" autoComplete="off" {...form.register("email")} />
            )}
          </FormField>
          <FormField
            id="invite-name"
            label="Full name (optional)"
            saving={form.formState.isSubmitting}
          >
            {(props) => <Input {...props} autoComplete="off" {...form.register("full_name")} />}
          </FormField>
          <Controller
            control={form.control}
            name="roles"
            render={({ field }) => <RoleCheckboxes value={field.value} onChange={field.onChange} />}
          />
          {errors.roles ? <p className="text-sm text-destructive">{errors.roles.message}</p> : null}
          {batches.data?.truncated ? (
            <p className="text-xs text-muted-foreground">Showing the first 2,000 batches.</p>
          ) : null}
          {batchOptions.length > 0 ? (
            <Controller
              control={form.control}
              name="batch_ids"
              render={({ field }) => (
                <fieldset className="flex flex-col gap-2">
                  <legend className="mb-1 text-sm font-medium">Batches (optional)</legend>
                  <div className="flex max-h-40 flex-col gap-2 overflow-y-auto">
                    {batchOptions.map((b) => (
                      <label key={b.id} className="flex items-center gap-2 text-sm">
                        <Checkbox
                          checked={field.value.includes(b.id)}
                          onCheckedChange={(on) =>
                            field.onChange(
                              on === true
                                ? [...field.value, b.id]
                                : field.value.filter((x) => x !== b.id),
                            )
                          }
                        />
                        {b.name}
                      </label>
                    ))}
                  </div>
                </fieldset>
              )}
            />
          ) : null}
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
              {form.formState.isSubmitting ? "Sending…" : "Send invitation"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function PendingInvitations() {
  const query = useInfiniteQuery(invitationsQuery("pending"));
  const revoke = useRevokeInvitation();
  const resend = useResendInvitation();
  const rows = query.data?.pages.flatMap((p) => p.items) ?? [];
  if (query.isSuccess && rows.length === 0) return null;

  return (
    <section id="invitations" className="flex flex-col gap-2" aria-labelledby="pending-invitations">
      <h2 id="pending-invitations" className="text-base font-medium">
        Pending invitations
      </h2>
      {query.error ? <ErrorAlert error={query.error} onRetry={() => void query.refetch()} /> : null}
      <ul className="flex flex-col gap-2">
        {rows.map((inv) => (
          <li
            key={inv.id}
            className="flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between"
          >
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium">{inv.email}</span>
              <span className="block text-xs text-muted-foreground">
                Expires {new Date(inv.expires_at).toLocaleDateString()}
              </span>
            </span>
            <span className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  resend.mutate(inv.id, {
                    onSuccess: () => toast.success("Invitation resent"),
                    onError: (e) => toast.error(errorMessage(e)),
                  })
                }
              >
                Resend
              </Button>
              <ConfirmButton
                label="Revoke"
                title="Revoke invitation?"
                description={`${inv.email} will lose the access this invitation granted.`}
                confirmLabel="Revoke"
                onConfirm={() => revoke.mutateAsync(inv.id)}
              />
            </span>
          </li>
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
