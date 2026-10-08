import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/errors";
import type { QuestionBank } from "@/lib/api/types";
import { bankKeys, useBankEdit } from "./assessment-api";
import type { IfMatch } from "./api";

function setup() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  });
  qc.setQueryData(bankKeys.bank("bank"), { id: "bank", revision: 7 } as QuestionBank);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, wrapper };
}
describe("shared bank revision contract", () => {
  it("serializes question and skill edits across hooks using the returned parent revision", async () => {
    const { qc, wrapper } = setup();
    const revisions: string[] = [];
    const run = async (_: string, header: IfMatch) => {
      revisions.push(header["If-Match"]);
      await new Promise((resolve) => setTimeout(resolve, 5));
      return { bank_revision: Number(header["If-Match"]) + 1 };
    };
    const { result } = renderHook(
      () => ({
        question: useBankEdit("bank", run, (saved) => saved.bank_revision),
        skills: useBankEdit("bank", run, (saved) => saved.bank_revision),
      }),
      { wrapper },
    );
    await act(() =>
      Promise.all([
        result.current.question.mutateAsync("prompt"),
        result.current.skills.mutateAsync("skills"),
        result.current.question.mutateAsync("options"),
      ]),
    );
    expect(revisions).toEqual(["7", "8", "9"]);
    expect(qc.getQueryData<QuestionBank>(bankKeys.bank("bank"))?.revision).toBe(10);
  });
  it("propagates a conflict and never invents a successful revision", async () => {
    const { qc, wrapper } = setup();
    const conflict = new ApiError({ status: 409, code: "revision_conflict", message: "stale" });
    const run = vi.fn(async (_: string, _header: IfMatch): Promise<number> => {
      throw conflict;
    });
    const { result } = renderHook(() => useBankEdit("bank", run, (saved) => saved), { wrapper });
    await act(async () => {
      await expect(result.current.mutateAsync("new prompt")).rejects.toBe(conflict);
    });
    expect(qc.getQueryData<QuestionBank>(bankKeys.bank("bank"))?.revision).toBe(7);
    expect(qc.getQueryState(bankKeys.bank("bank"))?.isInvalidated).toBe(true);
  });
});
