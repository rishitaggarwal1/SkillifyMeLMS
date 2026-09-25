import { z } from "zod";

/** The API's single error envelope: {"error": {"code", "message", "details"}}. */
export const errorEnvelopeSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    details: z.unknown().optional(),
  }),
});

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  readonly requestId: string | null;

  constructor(opts: {
    status: number;
    code: string;
    message: string;
    details?: unknown;
    requestId?: string | null;
  }) {
    super(opts.message);
    this.name = "ApiError";
    this.status = opts.status;
    this.code = opts.code;
    this.details = opts.details;
    this.requestId = opts.requestId ?? null;
  }
}

/** Convert any non-success API response body into an ApiError (never trusts the body's shape). */
export function toApiError(response: Response, body: unknown): ApiError {
  const requestId = response.headers.get("x-request-id");
  const parsed = errorEnvelopeSchema.safeParse(body);
  if (parsed.success) {
    return new ApiError({ status: response.status, requestId, ...parsed.data.error });
  }
  return new ApiError({
    status: response.status,
    code: "unexpected_response",
    message: `Unexpected response from the server (HTTP ${response.status}).`,
    requestId,
  });
}
