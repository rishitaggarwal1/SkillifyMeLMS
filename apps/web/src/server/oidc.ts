import "server-only";

import * as client from "openid-client";

import { serverConfig } from "./config";
import type { Tokens } from "./session";

/**
 * Keycloak OIDC client for the BFF (confidential client + PKCE).
 *
 * Endpoints are configured explicitly instead of discovered: the browser-facing ones (authorize,
 * logout) and the issuer use the public URL, while server-to-server ones (token, JWKS) use the
 * internal URL. Discovery would return public URLs that aren't reachable from inside Docker.
 */
let configuration: client.Configuration | undefined;

export function oidcConfiguration(): client.Configuration {
  if (configuration) return configuration;
  const cfg = serverConfig();
  const realmPublic = `${cfg.keycloakPublicUrl}/realms/${cfg.realm}/protocol/openid-connect`;
  const realmInternal = `${cfg.keycloakInternalUrl}/realms/${cfg.realm}/protocol/openid-connect`;
  configuration = new client.Configuration(
    {
      issuer: cfg.issuer,
      authorization_endpoint: `${realmPublic}/auth`,
      end_session_endpoint: `${realmPublic}/logout`,
      token_endpoint: `${realmInternal}/token`,
      jwks_uri: `${realmInternal}/certs`,
      id_token_signing_alg_values_supported: ["RS256"],
    },
    cfg.clientId,
    { client_secret: cfg.clientSecret, id_token_signed_response_alg: "RS256" },
    client.ClientSecretBasic(cfg.clientSecret),
  );
  if (!cfg.keycloakInternalUrl.startsWith("https:")) {
    // Local/dev only: Keycloak is served over plain HTTP inside the Docker network.
    client.allowInsecureRequests(configuration);
  }
  return configuration;
}

export function callbackUrl(): string {
  return `${serverConfig().webOrigin}/auth/callback`;
}

export async function startLogin(): Promise<{
  url: URL;
  verifier: string;
  state: string;
  nonce: string;
}> {
  const verifier = client.randomPKCECodeVerifier();
  const state = client.randomState();
  const nonce = client.randomNonce();
  const url = client.buildAuthorizationUrl(oidcConfiguration(), {
    redirect_uri: callbackUrl(),
    scope: "openid profile email",
    code_challenge: await client.calculatePKCECodeChallenge(verifier),
    code_challenge_method: "S256",
    state,
    nonce,
  });
  return { url, verifier, state, nonce };
}

function toTokens(response: client.TokenEndpointResponse): Tokens {
  const refreshExpires = Number(
    (response as { refresh_expires_in?: number }).refresh_expires_in ?? 0,
  );
  return {
    accessToken: response.access_token,
    refreshToken: response.refresh_token ?? null,
    idToken: response.id_token ?? null,
    sessionSeconds: refreshExpires > 0 ? refreshExpires : (response.expires_in ?? 300),
  };
}

/** Exchanges the authorization code; validates state, PKCE, nonce, and the ID token. */
export async function completeLogin(
  searchParams: URLSearchParams,
  checks: { verifier: string; state: string; nonce: string },
): Promise<Tokens> {
  // The redirect_uri sent to the token endpoint must equal the one used to start the login, so
  // rebuild the callback URL from WEB_ORIGIN rather than trusting the request's Host header.
  const currentUrl = new URL(`${callbackUrl()}?${searchParams.toString()}`);
  const response = await client.authorizationCodeGrant(oidcConfiguration(), currentUrl, {
    pkceCodeVerifier: checks.verifier,
    expectedState: checks.state,
    expectedNonce: checks.nonce,
    idTokenExpected: true,
  });
  return toTokens(response);
}

export async function refresh(refreshToken: string): Promise<Tokens> {
  return toTokens(await client.refreshTokenGrant(oidcConfiguration(), refreshToken));
}

export function logoutUrl(idToken: string | null): URL {
  const params: Record<string, string> = {
    post_logout_redirect_uri: `${serverConfig().webOrigin}/`,
    client_id: serverConfig().clientId,
  };
  if (idToken) params.id_token_hint = idToken;
  return client.buildEndSessionUrl(oidcConfiguration(), params);
}
