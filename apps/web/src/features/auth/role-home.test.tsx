import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Me, OrgRole } from "@/lib/api/types";
import { renderWithQuery, testQueryClient } from "@/test/utils";

import { RoleHome } from "./role-home";
import { AREA_HOME, areaOf, areasHere, destinations, homeFor } from "./roles";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const membership = (id: string, name: string, ...roles: OrgRole[]) => ({
  organization: { id, name, slug: id, is_content_publisher: false },
  roles,
});

const me = (overrides: Partial<Me> = {}): Me => ({
  user: { id: "u1", email: "person@college.test", full_name: "Person" },
  is_platform_admin: false,
  active_organization_id: null,
  active_roles: [],
  permissions: [],
  memberships: [],
  ...overrides,
});

describe("homeFor", () => {
  it.each([
    ["a platform admin", me({ is_platform_admin: true }), "/platform", null],
    ["an org admin", me({ memberships: [membership("c", "College", "org_admin")] }), "/admin", "c"],
    [
      "an instructor",
      me({ memberships: [membership("c", "College", "instructor")] }),
      "/teach",
      "c",
    ],
    ["a student", me({ memberships: [membership("c", "College", "student")] }), "/learn", "c"],
  ])("sends %s to their area", (_who, user, area, org) => {
    const home = homeFor(user);
    expect(home.kind).toBe("redirect");
    if (home.kind !== "redirect") return;
    expect(AREA_HOME[home.destination.area]).toBe(area);
    expect(home.destination.organizationId).toBe(org);
  });

  it("offers a choice for several roles, platform first", () => {
    const user = me({
      is_platform_admin: true,
      memberships: [
        membership("a", "Alpha College", "instructor", "org_admin"),
        membership("b", "Beta College", "student"),
      ],
    });
    const home = homeFor(user);
    expect(home.kind).toBe("choose");
    expect(destinations(user).map((d) => `${d.area}:${d.organizationId}`)).toEqual([
      "platform:null",
      "admin:a",
      "teach:a",
      "learn:b",
    ]);
  });

  it("has nothing for a lab author only (labs arrive in Phase 4)", () => {
    expect(homeFor(me({ memberships: [membership("c", "College", "lab_author")] }))).toEqual({
      kind: "none",
    });
  });
});

describe("areaOf / areasHere", () => {
  it("maps paths to areas, not prefixes of other words", () => {
    expect(areaOf("/platform/users/1")).toBe("platform");
    expect(areaOf("/admin")).toBe("admin");
    expect(areaOf("/teach/courses")).toBe("teach");
    expect(areaOf("/learn/enrollments/1")).toBe("learn");
    expect(areaOf("/learning")).toBeNull();
    expect(areaOf("/")).toBeNull();
  });

  it("lists only the active organization's areas, plus platform", () => {
    const user = me({
      is_platform_admin: true,
      active_organization_id: "b",
      memberships: [
        membership("a", "Alpha", "instructor"),
        membership("b", "Beta", "student", "org_admin"),
      ],
    });
    expect(areasHere(user)).toEqual(["platform", "admin", "learn"]);
  });
});

describe("<RoleHome />", () => {
  beforeEach(() => replace.mockClear());

  function renderHome(user: Me | null, onSwitch = vi.fn().mockResolvedValue(undefined)) {
    const client = testQueryClient();
    client.setQueryData(["me"], user);
    renderWithQuery(
      <RoleHome onSwitch={onSwitch}>
        <p>Landing</p>
      </RoleHome>,
      client,
    );
    return { onSwitch, client };
  }

  it("shows the landing page when signed out", () => {
    renderHome(null);
    expect(screen.getByText("Landing")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("redirects a single-role user without switching organization when it is active", async () => {
    const { onSwitch } = renderHome(
      me({ active_organization_id: "c", memberships: [membership("c", "College", "student")] }),
    );
    // While opening: a placeholder shaped like the student dashboard, not a blank page.
    expect(screen.getByRole("status", { name: "Opening your area" })).toBeInTheDocument();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/learn"));
    expect(onSwitch).not.toHaveBeenCalled();
    expect(screen.queryByText("Landing")).toBeNull();
  });

  it("switches to the organization first when another one is active", async () => {
    const { onSwitch } = renderHome(
      me({
        active_organization_id: "x",
        memberships: [
          membership("x", "Other", "lab_author"),
          membership("c", "College", "instructor"),
        ],
      }),
    );
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/teach"));
    expect(onSwitch).toHaveBeenCalledWith("c");
  });

  it("lets a multi-role user choose, switching organization on the way", async () => {
    const { onSwitch } = renderHome(
      me({
        is_platform_admin: true,
        active_organization_id: "a",
        memberships: [membership("a", "Alpha", "org_admin"), membership("b", "Beta", "student")],
      }),
    );
    const options = screen.getAllByRole("button");
    expect(options.map((b) => b.textContent)).toEqual([
      "Platform adminAll organizations→",
      "Org adminAlpha→",
      "StudentBeta→",
    ]);
    expect(replace).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /Student\s*Beta/ }));
    expect(onSwitch).toHaveBeenCalledWith("b");
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/learn"));
  });

  it("opens the platform area without switching organization", async () => {
    const { onSwitch } = renderHome(
      me({ is_platform_admin: true, memberships: [membership("a", "Alpha", "org_admin")] }),
    );
    await userEvent.click(screen.getByRole("button", { name: /Platform admin/ }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/platform"));
    expect(onSwitch).not.toHaveBeenCalled(); // the platform area isn't tied to an organization
  });

  it("reports a failed organization switch", async () => {
    renderHome(
      me({
        memberships: [membership("a", "Alpha", "org_admin"), membership("b", "Beta", "student")],
      }),
      vi.fn().mockRejectedValue(new Error("403")),
    );
    await userEvent.click(screen.getByRole("button", { name: /Student\s*Beta/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't open that area.");
    expect(replace).not.toHaveBeenCalled();
  });

  it("explains when there is no area yet", () => {
    renderHome(me({ memberships: [membership("c", "College", "lab_author")] }));
    expect(screen.getByText("Nothing here yet")).toBeInTheDocument();
    expect(screen.getByText(/coding labs arrive in Phase 4/)).toBeInTheDocument();
  });
});
