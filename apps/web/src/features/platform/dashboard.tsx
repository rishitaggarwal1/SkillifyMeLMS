"use client";

import { useQuery } from "@tanstack/react-query";

import { Skeleton } from "@/components/ui/skeleton";
import { ErrorAlert, PageTitle } from "@/features/admin/ui";
import { ROLE_LABELS, type OrgRole } from "@/lib/api/types";

import { summaryQuery } from "./api";

const n = (counts: Record<string, number>, key: string) => counts[key] ?? 0;
const ORG_ROLE_ORDER: OrgRole[] = ["org_admin", "instructor", "lab_author", "student"];

function Stat({ label, value, detail }: { label: string; value: number; detail?: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border p-4">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-2xl font-semibold tabular-nums">{value.toLocaleString("en-IN")}</dd>
      {detail ? <dd className="text-xs text-muted-foreground">{detail}</dd> : null}
    </div>
  );
}

export function PlatformDashboard() {
  const query = useQuery(summaryQuery());
  const s = query.data;
  return (
    <div className="flex flex-col gap-6">
      <PageTitle title="Platform" />
      {query.isPending ? <Skeleton className="h-40 w-full" /> : null}
      {query.error ? <ErrorAlert error={query.error} /> : null}
      {s ? (
        <>
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3" aria-label="Platform totals">
            <Stat
              label="Organizations"
              value={n(s.organizations, "active")}
              detail={`${n(s.organizations, "archived")} archived`}
            />
            <Stat
              label="Users"
              value={n(s.users, "active") + n(s.users, "invited")}
              detail={`${n(s.users, "invited")} invited · ${n(s.users, "disabled")} disabled`}
            />
            <Stat
              label="Courses"
              value={n(s.courses, "active")}
              detail={`${n(s.courses, "published")} published · ${n(s.courses, "archived")} archived`}
            />
            <Stat
              label="Enrollments"
              value={n(s.enrollments, "active")}
              detail={`${n(s.enrollments, "revoked")} revoked`}
            />
            <Stat
              label="Active today"
              value={s.active_today}
              detail={`Students who opened a lesson since ${new Date(s.active_since).toLocaleString(
                "en-IN",
                { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" },
              )} IST`}
            />
          </dl>
          <section aria-labelledby="org-roles-heading" className="flex flex-col gap-2">
            <h2 id="org-roles-heading" className="text-base font-semibold">
              Users by organization role
            </h2>
            <p className="text-sm text-muted-foreground">
              People holding each role in at least one active organization. Platform admins
              aren&apos;t included: theirs is a sign-in (Keycloak) role, not an organization role.
            </p>
            <dl
              className="grid grid-cols-2 gap-3 sm:grid-cols-4"
              aria-label="Users by organization role"
            >
              {ORG_ROLE_ORDER.map((role) => (
                <Stat key={role} label={ROLE_LABELS[role]} value={n(s.users_by_role, role)} />
              ))}
            </dl>
          </section>
        </>
      ) : null}
    </div>
  );
}
