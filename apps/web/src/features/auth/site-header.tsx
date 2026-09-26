"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Button, buttonVariants } from "@/components/ui/button";

import { OrgSwitcher } from "./org-switcher";
import { hasPermission, useMe } from "./queries";

export function SiteHeader() {
  const { data: me, isPending } = useMe();
  const pathname = usePathname();
  const loginHref = `/auth/login?returnTo=${encodeURIComponent(pathname || "/")}`;

  return (
    <header className="border-b">
      <div className="mx-auto flex h-14 w-full max-w-5xl items-center gap-3 px-4">
        <Link href="/" className="shrink-0 text-base font-semibold tracking-tight">
          SkillifyMe
        </Link>
        {me && hasPermission(me, "batch.manage") ? (
          <Link
            href="/admin/batches"
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            Admin
          </Link>
        ) : null}
        <div className="ml-auto flex min-w-0 items-center gap-2">
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
    </header>
  );
}
