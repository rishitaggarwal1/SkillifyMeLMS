/**
 * Authenticated encryption for session cookies (AES-256-GCM via Web Crypto).
 *
 * Tokens live only in httpOnly cookies; encrypting them as well means a leaked cookie value (logs,
 * a misconfigured proxy, browser devtools screenshots) does not reveal a usable token, and any
 * tampering fails authentication. The key is derived from SESSION_SECRET with HKDF.
 *
 * Format: v1.<base64url(iv)>.<base64url(ciphertext+tag)>
 */

const VERSION = "v1";
const encoder = new TextEncoder();
const decoder = new TextDecoder();

function toBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(value: string): Uint8Array<ArrayBuffer> {
  const padded =
    value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (value.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

const keyCache = new Map<string, Promise<CryptoKey>>();

export function deriveKey(secret: string): Promise<CryptoKey> {
  let key = keyCache.get(secret);
  if (!key) {
    key = (async () => {
      const material = await crypto.subtle.importKey("raw", encoder.encode(secret), "HKDF", false, [
        "deriveKey",
      ]);
      return crypto.subtle.deriveKey(
        {
          name: "HKDF",
          hash: "SHA-256",
          salt: encoder.encode("skillifyme-session-cookie"),
          info: encoder.encode(VERSION),
        },
        material,
        { name: "AES-GCM", length: 256 },
        false,
        ["encrypt", "decrypt"],
      );
    })();
    keyCache.set(secret, key);
  }
  return key;
}

export async function seal(plaintext: string, secret: string): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    await deriveKey(secret),
    encoder.encode(plaintext),
  );
  return `${VERSION}.${toBase64Url(iv)}.${toBase64Url(new Uint8Array(ciphertext))}`;
}

/** Returns null for anything that isn't a valid, untampered value sealed with this secret. */
export async function unseal(sealed: string | undefined, secret: string): Promise<string | null> {
  if (!sealed) return null;
  const [version, iv, data] = sealed.split(".");
  if (version !== VERSION || !iv || !data) return null;
  try {
    const plaintext = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: fromBase64Url(iv) },
      await deriveKey(secret),
      fromBase64Url(data),
    );
    return decoder.decode(plaintext);
  } catch {
    return null;
  }
}
