import "server-only";

import { needsRefresh } from "@/lib/auth/policy";

import { refresh } from "./oidc";
import type { Session, Tokens } from "./session";

export type AccessResult = {
  accessToken: string | null;
  /** New tokens to persist when a refresh happened. */
  refreshed: Tokens | null;
  /** The session is dead (refresh failed): cookies should be cleared. */
  expired: boolean;
};

/** A usable access token for this request, refreshing it shortly before expiry. */
export async function accessTokenFor(session: Session): Promise<AccessResult> {
  const now = Math.floor(Date.now() / 1000);
  if (!needsRefresh(session.accessToken, now)) {
    return { accessToken: session.accessToken, refreshed: null, expired: false };
  }
  if (!session.refreshToken) {
    return { accessToken: null, refreshed: null, expired: session.accessToken !== null };
  }
  try {
    const tokens = await refresh(session.refreshToken);
    return { accessToken: tokens.accessToken, refreshed: tokens, expired: false };
  } catch {
    return { accessToken: null, refreshed: null, expired: true };
  }
}
