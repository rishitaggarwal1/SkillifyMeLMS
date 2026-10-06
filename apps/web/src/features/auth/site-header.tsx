"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ThemeControl } from "@/components/patterns/theme-control";
import { Button, buttonVariants } from "@/components/ui/button";
import type { Me } from "@/lib/api/types";
import { cn } from "@/lib/utils";

import { OrgSwitcher } from "./org-switcher";
import { useMe } from "./queries";
import { AREA_HOME, AREA_LABELS, areaOf, areasHere, type Area } from "./roles";

export function SiteHeader() {
  const { data: me, isPending } = useMe();
  const pathname = usePathname() || "/";
  const loginHref = `/auth/login?returnTo=${encodeURIComponent(pathname)}`;

  return (
    <header className="sticky top-0 z-40 border-b bg-card shadow-xs" data-testid="app-header">
      <div className="mx-auto flex min-h-16 w-full max-w-7xl items-center gap-3 px-4">
        <Link href="/" className="shrink-0 text-base font-semibold tracking-tight">
          SkillifyMe
        </Link>
        <div className="ml-auto flex min-w-0 items-center gap-1 sm:gap-2">
          <ThemeControl />
          {isPending ? null : me ? (
            <>
              <OrgSwitcher me={me} />
              <form action="/auth/logout" method="post">
                <Button type="submit" variant="ghost" size="sm" data-testid="sign-out">
                  Sign out
                </Button>
              </form>
            </>
          ) : (
            <a href={loginHref} className={buttonVariants({ size: "sm" })} data-testid="sign-in">
              Sign in
            </a>
          )}
        </div>
      </div>
      {me ? <AreaBar me={me} pathname={pathname} /> : null}
    </header>
  );
}

/** Which role you're acting as, in which organization, and links to your other areas. */
function AreaBar({ me, pathname }: { me: Me; pathname: string }) {
  const active = areaOf(pathname);
  const areas = areasHere(me);
  if (areas.length === 0) return null;
  const org = me.memberships.find((m) => m.organization.id === me.active_organization_id);
  const where =
    active === "platform" ? "All organizations" : (org?.organization.name ?? "No organization");
  return (
    <div className="mx-auto flex w-full max-w-7xl flex-wrap items-center gap-x-3 gap-y-1 px-4 pb-2 text-sm">
      {active ? (
        <p className="min-w-0 truncate text-muted-foreground" data-testid="active-role">
          <span className="font-medium text-foreground">
            {active === "teach" && me.active_roles.includes("org_admin")
              ? "Org admin"
              : AREA_LABELS[active]}
          </span>
          <span aria-hidden> · </span>
          <span className="sr-only">in </span>
          {where}
        </p>
      ) : null}
      {areas.length > 1 || !active ? (
        <nav aria-label="Your areas" className="-mx-1 flex gap-1 overflow-x-auto">
          {areas.map((area: Area) => (
            <Link
              key={area}
              href={AREA_HOME[area]}
              aria-current={area === active ? "page" : undefined}
              className={cn(
                "inline-flex min-h-11 shrink-0 items-center rounded-md px-2 py-1 text-muted-foreground hover:bg-muted hover:text-foreground",
                "aria-[current=page]:bg-muted aria-[current=page]:text-foreground",
              )}
            >
              {AREA_LABELS[area]}
            </Link>
          ))}
        </nav>
      ) : null}
    </div>
  );
}
