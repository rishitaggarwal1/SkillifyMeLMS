import "server-only";

import { z } from "zod";

/**
 * BFF configuration from the environment (validated once, lazily, so `next build` doesn't need it).
 * Every Keycloak URL derives from KEYCLOAK_PUBLIC_URL + KEYCLOAK_REALM, exactly like the API:
 * - public URL (browser redirects, token issuer): KEYCLOAK_PUBLIC_URL, pinned by KC_HOSTNAME
 * - internal URL (token exchange, JWKS from inside Docker): KEYCLOAK_INTERNAL_URL, else public
 */
const schema = z.object({
  KEYCLOAK_REALM: z.string().min(1).default("skillifyme"),
  KEYCLOAK_PUBLIC_URL: z.url(),
  KEYCLOAK_INTERNAL_URL: z.url().optional(),
  OIDC_WEB_CLIENT_ID: z.string().min(1).default("skillifyme-web"),
  KC_WEB_CLIENT_SECRET: z.string().min(1),
  WEB_ORIGIN: z.url(),
  SESSION_SECRET: z.string().min(32, "SESSION_SECRET must be at least 32 characters"),
  API_INTERNAL_URL: z.url(),
  REDIS_URL: z.string().optional(),
  AUTH_RATE_LIMIT_PER_MINUTE: z.coerce.number().int().min(1).default(20),
});

export type ServerConfig = {
  realm: string;
  issuer: string;
  keycloakPublicUrl: string;
  keycloakInternalUrl: string;
  clientId: string;
  clientSecret: string;
  webOrigin: string;
  sessionSecret: string;
  apiInternalUrl: string;
  redisUrl: string | undefined;
  authRateLimitPerMinute: number;
};

let cached: ServerConfig | undefined;

export function serverConfig(): ServerConfig {
  if (cached) return cached;
  const env = schema.parse(process.env);
  const trim = (url: string) => url.replace(/\/+$/, "");
  const keycloakPublicUrl = trim(env.KEYCLOAK_PUBLIC_URL);
  cached = {
    realm: env.KEYCLOAK_REALM,
    issuer: `${keycloakPublicUrl}/realms/${env.KEYCLOAK_REALM}`,
    keycloakPublicUrl,
    keycloakInternalUrl: trim(env.KEYCLOAK_INTERNAL_URL ?? keycloakPublicUrl),
    clientId: env.OIDC_WEB_CLIENT_ID,
    clientSecret: env.KC_WEB_CLIENT_SECRET,
    webOrigin: trim(env.WEB_ORIGIN),
    sessionSecret: env.SESSION_SECRET,
    apiInternalUrl: trim(env.API_INTERNAL_URL),
    redisUrl: env.REDIS_URL || undefined,
    authRateLimitPerMinute: env.AUTH_RATE_LIMIT_PER_MINUTE,
  };
  return cached;
}
