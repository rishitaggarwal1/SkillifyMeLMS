import type { components } from "./schema";

type Schemas = components["schemas"];

export type Me = Schemas["MeResponse"];
export type Membership = Schemas["MembershipOut"];
export type OrgRole = Schemas["OrgRole"];
export type Batch = Schemas["BatchOut"];
export type Member = Schemas["MemberOut"];
export type Invitation = Schemas["InvitationOut"];
export type ImportJob = Schemas["ImportJobOut"];

export const ROLE_LABELS: Record<OrgRole, string> = {
  org_admin: "Org admin",
  instructor: "Instructor",
  lab_author: "Lab author",
  student: "Student",
};

export const ORG_ROLES: OrgRole[] = ["student", "instructor", "lab_author", "org_admin"];
