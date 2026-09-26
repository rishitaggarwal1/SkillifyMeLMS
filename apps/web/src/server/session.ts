import "server-only";

import type { NextRequest, NextResponse } from "next/server";

import { seal, unseal } from "@/lib/auth/crypto";
import { isUuid } from "@/lib/auth/policy";

import { serverConfig } from "./config";

/**
 * Session cookies. Tokens are never exposed to browser JavaScript: every cookie is httpOnly,
 * Secure, SameSite=Lax, and uses the `__Host-` prefix (no Domain, Path=/, Secure required), and the
 * token values are additionally encrypted (see lib/auth/crypto.ts).
 */
export const COOKIES = {
  access: "__Host-sm_at",
  refresh: "__Host-sm_rt",
  idToken: "__Host-sm_it",
  org: "__Host-sm_org",
  login: "__Host-sm_login", // transient PKCE/state/nonce during the login round trip
} as const;

const BASE = { httpOnly: true, secure: true, sameSite: "lax", path: "/" } as const;

export type Tokens = {
  accessToken: string;
  refreshToken: string | null;
  idToken: string | null;
  /** Seconds until the session (refresh token) expires; sets cookie lifetimes. */
  sessionSeconds: number;
};

export type Session = {
  accessToken: string | null;
  refreshToken: string | null;
  idToken: string | null;
  organizationId: string | null;
};

type CookieReader = { get(name: string): { value: string } | undefined };

export async function readSession(cookies: CookieReader): Promise<Session> {
  const { sessionSecret } = serverConfig();
  const [accessToken, refreshToken, idToken] = await Promise.all([
    unseal(cookies.get(COOKIES.access)?.value, sessionSecret),
    unseal(cookies.get(COOKIES.refresh)?.value, sessionSecret),
    unseal(cookies.get(COOKIES.idToken)?.value, sessionSecret),
  ]);
  const org = cookies.get(COOKIES.org)?.value;
  return { accessToken, refreshToken, idToken, organizationId: isUuid(org) ? org : null };
}

export function hasSessionCookie(request: NextRequest): boolean {
  return request.cookies.has(COOKIES.refresh) || request.cookies.has(COOKIES.access);
}

export async function writeTokens(response: NextResponse, tokens: Tokens): Promise<void> {
  const { sessionSecret } = serverConfig();
  const maxAge = Math.max(60, tokens.sessionSeconds);
  response.cookies.set(COOKIES.access, await seal(tokens.accessToken, sessionSecret), {
    ...BASE,
    maxAge,
  });
  if (tokens.refreshToken) {
    response.cookies.set(COOKIES.refresh, await seal(tokens.refreshToken, sessionSecret), {
      ...BASE,
      maxAge,
    });
  }
  if (tokens.idToken) {
    response.cookies.set(COOKIES.idToken, await seal(tokens.idToken, sessionSecret), {
      ...BASE,
      maxAge,
    });
  }
}

export function clearSession(response: NextResponse): void {
  for (const name of [COOKIES.access, COOKIES.refresh, COOKIES.idToken, COOKIES.login]) {
    response.cookies.set(name, "", { ...BASE, maxAge: 0 });
  }
}

export function writeOrganization(response: NextResponse, organizationId: string | null): void {
  response.cookies.set(COOKIES.org, organizationId ?? "", {
    ...BASE,
    maxAge: organizationId ? 60 * 60 * 24 * 365 : 0,
  });
}

// ---------------------------------------------------------------------------- login round trip

export type LoginState = { verifier: string; state: string; nonce: string; returnTo: string };

export async function writeLoginState(response: NextResponse, state: LoginState): Promise<void> {
  response.cookies.set(
    COOKIES.login,
    await seal(JSON.stringify(state), serverConfig().sessionSecret),
    { ...BASE, maxAge: 600 },
  );
}

export async function readLoginState(cookies: CookieReader): Promise<LoginState | null> {
  const raw = await unseal(cookies.get(COOKIES.login)?.value, serverConfig().sessionSecret);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as LoginState;
  } catch {
    return null;
  }
}
