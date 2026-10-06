"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { NativeSelect } from "@/components/native-select";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageSkeleton } from "@/components/patterns/states";
import { useMe } from "@/features/auth/queries";
import { useDebounced } from "@/features/admin/hooks";
import {
  ConfirmButton,
  EmptyState,
  ErrorAlert,
  LoadMore,
  PageTitle,
  errorMessage,
} from "@/features/admin/ui";
import { ORG_ROLES, ROLE_LABELS, type OrgRole, type PlatformUser } from "@/lib/api/types";

import {
  organizationQuery,
  userQuery,
  usersQuery,
  useSetUserEnabled,
  type UserFilters,
} from "./api";

type Status = NonNullable<UserFilters["status"]>;
const STATUS_LABELS: Record<Status, string> = {
  active: "Active",
  invited: "Invited",
  disabled: "Disabled",
};

export function StatusBadge({ status }: { status: string }) {
  if (status === "active") return null;
  return (
    <Badge variant={status === "disabled" ? "destructive" : "outline"} className="shrink-0">
      {STATUS_LABELS[status as Status] ?? status}
    </Badge>
  );
}

export function UsersPage({ organizationId }: { organizationId?: string }) {
  const [q, setQ] = useState("");
  const [role, setRole] = useState<OrgRole | "">("");
  const [status, setStatus] = useState<Status | "">("");
  const debounced = useDebounced(q.trim(), 300);
  const query = useInfiniteQuery(
    usersQuery({
      q: debounced || undefined,
      role: role || undefined,
      status: status || undefined,
      organizationId,
    }),
  );
  const org = useQuery({ ...organizationQuery(organizationId ?? ""), enabled: !!organizationId });
  const rows = query.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="flex flex-col gap-4">
      <PageTitle title="Users" />
      {organizationId ? (
        <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <span>
            Members of <span className="font-medium text-foreground">{org.data?.name ?? "…"}</span>
          </span>
          <Link href="/platform/users" className="underline-offset-4 hover:underline">
            Show everyone
          </Link>
        </p>
      ) : null}
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          type="search"
          placeholder="Search by name or email"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search users"
        />
        <NativeSelect
          aria-label="Filter by organization role"
          className="sm:w-48"
          value={role}
          onChange={(e) => setRole(e.target.value as OrgRole | "")}
        >
          <option value="">Any org role</option>
          {ORG_ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABELS[r]}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label="Filter by status"
          className="sm:w-40"
          value={status}
          onChange={(e) => setStatus(e.target.value as Status | "")}
        >
          <option value="">Any status</option>
          {(Object.keys(STATUS_LABELS) as Status[]).map((s) => (
            <option key={s} value={s}>
              {STATUS_LABELS[s]}
            </option>
          ))}
        </NativeSelect>
      </div>
      {query.isPending ? <PageSkeleton /> : null}
      {query.error ? <ErrorAlert error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.isSuccess && rows.length === 0 ? <EmptyState>No users match.</EmptyState> : null}
      <ul className="flex flex-col gap-2" aria-label="Users">
        {rows.map((u) => (
          <UserRow key={u.id} user={u} />
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

function UserRow({ user }: { user: PlatformUser }) {
  return (
    <li>
      <Link
        href={`/platform/users/${user.id}`}
        className="flex flex-col gap-1 rounded-lg border p-3 hover:bg-muted/50"
      >
        <span className="flex items-center justify-between gap-2">
          <span className="truncate font-medium">{user.full_name || user.email}</span>
          <StatusBadge status={user.status} />
        </span>
        <span className="truncate text-sm text-muted-foreground">{user.email}</span>
        {user.memberships.length ? (
          <span className="text-sm text-muted-foreground">
            {user.memberships
              .map(
                (m) => `${m.organization.name}: ${m.roles.map((r) => ROLE_LABELS[r]).join(", ")}`,
              )
              .join(" · ")}
          </span>
        ) : (
          <span className="text-sm text-muted-foreground">No organization</span>
        )}
      </Link>
    </li>
  );
}

export function UserDetailPage({ userId }: { userId: string }) {
  const query = useQuery(userQuery(userId));
  const { data: me } = useMe();
  const setEnabled = useSetUserEnabled(userId);
  if (query.isPending) return <PageSkeleton />;
  if (query.error) return <ErrorAlert error={query.error} onRetry={() => void query.refetch()} />;
  const user = query.data;
  const orgNames = new Map(user.memberships.map((m) => [m.organization.id, m.organization.name]));
  const isSelf = me?.user.id === user.id;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <Link href="/platform/users" className="text-sm text-muted-foreground">
          ← Users
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <PageTitle title={user.full_name || user.email} />
          <StatusBadge status={user.status} />
        </div>
        <p className="truncate text-sm text-muted-foreground">{user.email}</p>
      </div>

      <section aria-labelledby="memberships-heading" className="flex flex-col gap-2">
        <h2 id="memberships-heading" className="text-base font-semibold">
          Organizations
        </h2>
        {user.memberships.length === 0 ? (
          <EmptyState>Not a member of any organization.</EmptyState>
        ) : (
          <ul className="flex flex-col gap-2" aria-label="Memberships">
            {user.memberships.map((m) => (
              <li
                key={m.organization.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3"
              >
                <Link
                  href={`/platform/organizations/${m.organization.id}`}
                  className="min-w-0 truncate font-medium underline-offset-4 hover:underline"
                >
                  {m.organization.name}
                </Link>
                <span className="flex flex-wrap gap-1">
                  {m.roles.map((r) => (
                    <Badge key={r} variant="secondary">
                      {ROLE_LABELS[r]}
                    </Badge>
                  ))}
                  {m.organization_status === "archived" ? (
                    <Badge variant="outline">Archived org</Badge>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="batches-heading" className="flex flex-col gap-2">
        <h2 id="batches-heading" className="text-base font-semibold">
          Batches
        </h2>
        {user.batches.length === 0 ? (
          <p className="text-sm text-muted-foreground">In no batch.</p>
        ) : (
          <ul className="flex flex-col gap-1 text-sm" aria-label="Batches">
            {user.batches.map((b) => (
              <li key={b.id}>
                {b.name}
                <span className="text-muted-foreground"> · {orgNames.get(b.organization_id)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section
        aria-labelledby="access-heading"
        className="flex flex-col gap-2 rounded-lg border p-4"
      >
        <h2 id="access-heading" className="text-base font-semibold">
          Access
        </h2>
        {user.status === "disabled" ? (
          <>
            <p className="text-sm text-muted-foreground">
              This account can&apos;t sign in or use the API.
            </p>
            <Button
              className="self-start"
              variant="outline"
              disabled={setEnabled.isPending}
              onClick={() =>
                void setEnabled.mutateAsync(true).then(
                  () => toast.success("Account enabled"),
                  (e: unknown) => toast.error(errorMessage(e)),
                )
              }
            >
              Enable account
            </Button>
          </>
        ) : isSelf ? (
          <p className="text-sm text-muted-foreground">You can&apos;t disable your own account.</p>
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              Disabling blocks sign-in everywhere and ends their current sessions. Their data is
              kept, and you can enable the account again.
            </p>
            <div>
              <ConfirmButton
                label="Disable account"
                size="default"
                title={`Disable ${user.email}?`}
                description="They are signed out and can't sign in to any organization until the account is enabled again."
                confirmLabel="Disable"
                onConfirm={() => setEnabled.mutateAsync(false)}
              />
            </div>
          </>
        )}
      </section>
    </div>
  );
}
