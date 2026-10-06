"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { PageSkeleton } from "@/components/patterns/states";

import { AreaSkeleton } from "./area-skeleton";
import { switchOrganization, useMe } from "./queries";
import { AREA_HOME, AREA_LABELS, homeFor, type Destination } from "./roles";

type Props = {
  /** What signed-out visitors see. */
  children: ReactNode;
  /** Injected in tests. */
  onSwitch?: (organizationId: string | null) => Promise<void>;
};

/**
 * The home page: signed-out visitors see the landing page; a signed-in user goes straight to their
 * only area, or chooses one when they work in several (roles are per organization).
 */
/** Open `destination`, switching the active organization first when it differs. */
function useOpen(onSwitch: NonNullable<Props["onSwitch"]>) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  return async (destination: Destination) => {
    if (destination.organizationId && destination.organizationId !== me?.active_organization_id) {
      await onSwitch(destination.organizationId);
      await queryClient.invalidateQueries();
    }
    router.replace(AREA_HOME[destination.area]);
  };
}

const OPEN_FAILED = "Couldn't open that area. Try again.";

export function RoleHome({ children, onSwitch = switchOrganization }: Props) {
  const { data: me, isPending } = useMe();
  const open = useOpen(onSwitch);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const home = me ? homeFor(me) : null;

  async function go(destination: Destination) {
    setBusy(true);
    setError(null);
    try {
      await open(destination);
    } catch {
      setError(OPEN_FAILED);
      setBusy(false);
    }
  }

  if (isPending) return <PageSkeleton />;
  if (!me || !home) return <>{children}</>;
  if (home.kind === "none") {
    return (
      <Alert>
        <AlertTitle>Nothing here yet</AlertTitle>
        <AlertDescription>
          {me.memberships.some((m) => m.roles.includes("lab_author"))
            ? "You're a lab author: coding labs arrive in Phase 4. Until then there's nothing to open here."
            : "Your account has no role that uses the portal yet. Ask your organization admin."}
        </AlertDescription>
      </Alert>
    );
  }
  if (home.kind === "redirect") {
    return <AutoOpen destination={home.destination} onSwitch={onSwitch} />;
  }
  return (
    <section className="flex flex-col gap-4" aria-labelledby="choose-heading">
      <h1 id="choose-heading" className="text-xl font-semibold tracking-tight sm:text-2xl">
        Where do you want to work?
      </h1>
      <ul className="flex flex-col gap-2" aria-label="Your roles">
        {home.options.map((d) => (
          <li key={`${d.area}:${d.organizationId ?? "platform"}`}>
            <Button
              variant="outline"
              className="h-auto w-full justify-between gap-3 px-4 py-3 text-left"
              disabled={busy}
              onClick={() => void go(d)}
            >
              <span className="flex min-w-0 flex-col">
                <span className="font-medium">{AREA_LABELS[d.area]}</span>
                <span className="truncate text-sm font-normal text-muted-foreground">
                  {d.organizationName ?? "All organizations"}
                </span>
              </span>
              <span aria-hidden>→</span>
            </Button>
          </li>
        ))}
      </ul>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </section>
  );
}

/** Someone with a single area goes straight there. */
function AutoOpen({
  destination,
  onSwitch,
}: {
  destination: Destination;
  onSwitch: NonNullable<Props["onSwitch"]>;
}) {
  const open = useOpen(onSwitch);
  const [failed, setFailed] = useState(false);
  const { area, organizationId } = destination;
  useEffect(() => {
    open({ area, organizationId, organizationName: null }).catch(() => setFailed(true));
    // Run once per destination; `open` is a new function every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [area, organizationId]);
  return failed ? (
    <p role="alert" className="text-sm text-destructive">
      {OPEN_FAILED}
    </p>
  ) : (
    <AreaSkeleton area={destination.area} />
  );
}
