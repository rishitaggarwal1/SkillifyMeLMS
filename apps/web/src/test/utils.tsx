import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import { createApiClient } from "@/lib/api/client";

/** An API client whose fetch returns canned responses, keyed by path. */
export function mockApiClient(routes: Record<string, () => Response | Promise<Response>>) {
  return createApiClient("http://api.test", async (request) => {
    const handler = routes[new URL(request.url).pathname];
    if (!handler) throw new TypeError(`fetch failed: no mock for ${request.url}`);
    return handler();
  });
}

export function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

export function renderWithQuery(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
