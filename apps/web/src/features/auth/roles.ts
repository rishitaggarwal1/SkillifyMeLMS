import type { Me, OrgRole } from "@/lib/api/types";

/** The four areas of the portal, one per role people sign in for. */
export type Area = "platform" | "admin" | "teach" | "learn";

export const AREA_LABELS: Record<Area, string> = {
  platform: "Platform admin",
  admin: "Org admin",
  teach: "Instructor",
  learn: "Student",
};

export const AREA_HOME: Record<Area, string> = {
  platform: "/platform",
  admin: "/admin",
  teach: "/teach",
  learn: "/learn",
};

/** A place someone can work: an area, in an organization (none for the platform area). */
export type Destination = {
  area: Area;
  organizationId: string | null;
  organizationName: string | null;
};

const ROLE_AREAS: [role: OrgRole, area: Area][] = [
  ["org_admin", "admin"],
  ["instructor", "teach"],
  ["student", "learn"],
];

/** Every (area, organization) the user can use, platform first, then by organization name.
 * lab_author has no area yet (labs arrive in Phase 3). */
export function destinations(me: Me): Destination[] {
  const out: Destination[] = [];
  if (me.is_platform_admin)
    out.push({ area: "platform", organizationId: null, organizationName: null });
  for (const m of me.memberships) {
    for (const [role, area] of ROLE_AREAS) {
      if (m.roles.includes(role)) {
        out.push({
          area,
          organizationId: m.organization.id,
          organizationName: m.organization.name,
        });
      }
    }
  }
  return out;
}

export type Home =
  | { kind: "redirect"; destination: Destination }
  | { kind: "choose"; options: Destination[] }
  | { kind: "none" };

/** Where `/` sends a signed-in user: straight to their only area, or a chooser when they have
 * several (e.g. instructor in one college, student in another). */
export function homeFor(me: Me): Home {
  const options = destinations(me);
  if (options.length === 0) return { kind: "none" };
  if (options.length === 1) return { kind: "redirect", destination: options[0]! };
  return { kind: "choose", options };
}

/** The area a path belongs to (the header shows it as the active role). */
export function areaOf(pathname: string): Area | null {
  for (const area of Object.keys(AREA_HOME) as Area[]) {
    const home = AREA_HOME[area];
    if (pathname === home || pathname.startsWith(`${home}/`)) return area;
  }
  return null;
}

/** Areas available in the active organization (plus the platform area for platform admins), for
 * the header's links. */
export function areasHere(me: Me): Area[] {
  return destinations(me)
    .filter((d) => d.area === "platform" || d.organizationId === me.active_organization_id)
    .map((d) => d.area);
}
