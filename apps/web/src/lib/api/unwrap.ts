import { toApiError } from "./errors";

type FetchResult<T> = { data?: T; error?: unknown; response: Response };

/** Resolve an openapi-fetch call to its data, or throw an ApiError parsed from the envelope. */
export async function unwrap<T>(call: Promise<FetchResult<T>>): Promise<T> {
  const { data, error, response } = await call;
  if (response.ok) return data as T;
  throw toApiError(response, error);
}
