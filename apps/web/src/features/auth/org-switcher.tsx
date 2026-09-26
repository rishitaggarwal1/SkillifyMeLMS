"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { NativeSelect } from "@/components/native-select";
import type { Me } from "@/lib/api/types";

import { switchOrganization } from "./queries";

type Props = {
  me: Me;
  /** Injected in tests. */
  onSwitch?: (organizationId: string | null) => Promise<void>;
};

/** Lets people who belong to several organizations choose which one they are working in. */
export function OrgSwitcher({ me, onSwitch = switchOrganization }: Props) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (me.memberships.length === 0) return null;
  if (me.memberships.length === 1) {
    return (
      <span className="truncate text-sm text-muted-foreground" data-testid="current-org">
        {me.memberships[0]!.organization.name}
      </span>
    );
  }

  async function change(value: string) {
    setPending(true);
    setError(null);
    try {
      await onSwitch(value || null);
      // Everything on screen is org-scoped: refetch it all.
      await queryClient.invalidateQueries();
    } catch {
      setError("Couldn't switch organization.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex min-w-0 flex-col">
      <label htmlFor="org-switcher" className="sr-only">
        Organization
      </label>
      <NativeSelect
        id="org-switcher"
        className="h-8 max-w-48 sm:max-w-64"
        value={me.active_organization_id ?? ""}
        disabled={pending}
        onChange={(e) => void change(e.target.value)}
      >
        {me.active_organization_id === null ? <option value="">Choose organization…</option> : null}
        {me.memberships.map((m) => (
          <option key={m.organization.id} value={m.organization.id}>
            {m.organization.name}
          </option>
        ))}
      </NativeSelect>
      {error ? (
        <span role="alert" className="text-xs text-destructive">
          {error}
        </span>
      ) : null}
    </div>
  );
}
