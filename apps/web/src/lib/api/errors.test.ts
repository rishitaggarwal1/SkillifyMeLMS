// @vitest-environment node
import { expect, it } from "vitest";
import { toApiError } from "./errors";

it("preserves the standard envelope, optional details and request reference", () => {
  const response = new Response(null, { status: 409, headers: { "x-request-id": "request-1" } });
  for (const details of [undefined, null, { current_revision: 7 }, ["retry"]]) {
    const error = toApiError(response, {
      error: {
        code: "revision_conflict",
        message: "Changed elsewhere",
        details,
        private_field: "hidden",
      },
      extra: "hidden",
    });
    expect(error).toBeInstanceOf(Error);
    expect(error).toMatchObject({
      status: 409,
      code: "revision_conflict",
      message: "Changed elsewhere",
      requestId: "request-1",
    });
    expect(error.details).toEqual(details);
    expect(error).not.toHaveProperty("private_field");
    expect(error).not.toHaveProperty("extra");
  }
});

it.each([
  null,
  [],
  {},
  { error: null },
  { error: { code: 4, message: "private" } },
  { error: { code: "bad", message: 42 } },
])("rejects an untrusted error body without exposing its contents: %j", (body) => {
  const error = toApiError(new Response(null, { status: 502 }), body);
  expect(error).toMatchObject({
    status: 502,
    code: "unexpected_response",
    message: "Unexpected response from the server (HTTP 502).",
    requestId: null,
  });
  expect(error.details).toBeUndefined();
});
