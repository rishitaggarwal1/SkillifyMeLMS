import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Me } from "@/lib/api/types";
import { jsonResponse, mockApiClient, renderWithQuery } from "@/test/utils";

import { OrgSwitcher } from "./org-switcher";
import { fetchMe } from "./queries";

const org = (id: string, name: string) => ({
  organization: { id, name, slug: name.toLowerCase(), is_content_publisher: false },
  roles: ["instructor" as const],
});

const me = (overrides: Partial<Me> = {}): Me => ({
  user: { id: "u1", email: "multi@college.test", full_name: "Meera" },
  is_platform_admin: false,
  active_organization_id: null,
  active_roles: [],
  permissions: [],
  memberships: [org("o1", "Demo College"), org("o2", "SkillifyMe")],
  ...overrides,
});

describe("<OrgSwitcher />", () => {
  it("shows the only organization as text", () => {
    renderWithQuery(<OrgSwitcher me={me({ memberships: [org("o1", "Demo College")] })} />);
    expect(screen.getByTestId("current-org")).toHaveTextContent("Demo College");
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("asks to choose when several organizations and none is active", () => {
    renderWithQuery(<OrgSwitcher me={me()} />);
    expect(screen.getByRole("combobox", { name: "Organization" })).toHaveValue("");
    expect(screen.getByRole("option", { name: "Choose organization…" })).toBeInTheDocument();
  });

  it("switches and refetches everything", async () => {
    const onSwitch = vi.fn().mockResolvedValue(undefined);
    const { client } = renderWithQuery(
      <OrgSwitcher me={me({ active_organization_id: "o1" })} onSwitch={onSwitch} />,
    );
    const invalidate = vi.spyOn(client, "invalidateQueries");

    await userEvent.selectOptions(screen.getByRole("combobox"), "o2");

    expect(onSwitch).toHaveBeenCalledWith("o2");
    await waitFor(() => expect(invalidate).toHaveBeenCalled());
  });

  it("reports a failed switch", async () => {
    const onSwitch = vi.fn().mockRejectedValue(new Error("403"));
    renderWithQuery(<OrgSwitcher me={me({ active_organization_id: "o1" })} onSwitch={onSwitch} />);
    await userEvent.selectOptions(screen.getByRole("combobox"), "o2");
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't switch organization.");
  });
});

describe("fetchMe", () => {
  it("returns null when signed out", async () => {
    const client = mockApiClient({
      "/api/v1/me": () =>
        jsonResponse({ error: { code: "unauthenticated", message: "x", details: null } }, 401),
    });
    await expect(fetchMe(client)).resolves.toBeNull();
  });

  it("throws other errors", async () => {
    const client = mockApiClient({
      "/api/v1/me": () =>
        jsonResponse({ error: { code: "internal_error", message: "x", details: null } }, 500),
    });
    await expect(fetchMe(client)).rejects.toMatchObject({ status: 500 });
  });
});
