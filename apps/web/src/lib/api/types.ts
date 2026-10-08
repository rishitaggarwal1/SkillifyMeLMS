import type { components } from "./schema";

type Schemas = components["schemas"];

export type Me = Schemas["MeResponse"];
export type Membership = Schemas["MembershipOut"];
export type OrgRole = Schemas["OrgRole"];
export type Batch = Schemas["BatchOut"];
export type Member = Schemas["MemberOut"];
export type Invitation = Schemas["InvitationOut"];
export type ImportJob = Schemas["ImportJobOut"];

// ---- courses (instructor builder)
export type Course = Schemas["CourseOut"];
export type Draft = Schemas["DraftOut"];
export type DraftModule = Schemas["DraftModule"];
export type LessonSummary = Schemas["LessonSummary"];
export type Lesson = Schemas["LessonOut"];
export type LessonType = Schemas["LessonType"];
export type PublishPreview = Schemas["PublishPreview"];
export type CourseVersion = Schemas["VersionOut"];
export type Assignment = Schemas["AssignmentOut"];
export type Skill = Schemas["SkillOut"];
export type Video = Schemas["VideoOut"];
export type StoredFile = Schemas["FileOut"];
export type DirectoryOrganization = Schemas["DirectoryOrganization"];

// ---- platform admin
export type Organization = Schemas["OrganizationOut"];
export type PlatformSummary = Schemas["PlatformSummary"];
export type PlatformUser = Schemas["PlatformUserOut"];
export type PlatformUserDetail = Schemas["PlatformUserDetail"];
export type PlatformCourse = Schemas["PlatformCourseOut"];
export type AuditEntry = Schemas["AuditEntryOut"];

export const LESSON_TYPE_LABELS: Record<LessonType, string> = {
  video: "Video",
  notes: "Notes",
  pdf: "PDF",
  quiz: "Quiz",
  lab: "Lab",
  assignment: "Assignment",
};
/** Only labs remain unavailable in the course authoring UI. */
export const PLACEHOLDER_LESSON_TYPES: ReadonlySet<LessonType> = new Set(["lab"]);

export const ROLE_LABELS: Record<OrgRole, string> = {
  org_admin: "Org admin",
  instructor: "Instructor",
  lab_author: "Lab author",
  student: "Student",
};

export const ORG_ROLES: OrgRole[] = ["student", "instructor", "lab_author", "org_admin"];

// ---- assignments
export type AssignmentDraft = Schemas["AssignmentDraftOut"];
export type PublishedAssignment = Schemas["PublishedAssignment"];
export type StudentAssignment = Schemas["StudentAssignmentOut"];
export type Submission = Schemas["SubmissionOut"];
export type GraderSubmissionRow = Schemas["GraderSubmissionRow"];
export type GraderSubmission = Schemas["GraderSubmissionDetail"];
export type SubmissionAttempt = Schemas["SubmissionAttemptOut"];
export type AssignmentGrade = Schemas["GradeOut"];
export type Rubric = NonNullable<PublishedAssignment["rubric"]>;
export type LateData = Schemas["LateData"];

// ---- assessment authoring (never used for student attempt projections)
export type QuestionBank = Schemas["BankOut"];
export type AuthorQuestion = Schemas["QuestionOut"];
export type QuestionBody = Schemas["QuestionBody"];
export type QuizBody = Schemas["QuizBody"];
export type DraftQuiz = Schemas["QuizOut"];

// ---- progress reports
export type CourseProgressPage = Schemas["CourseProgressPage"];
export type StudentProgress = Schemas["StudentProgress"];
export type ProgressLesson = Schemas["ProgressLesson"];
export type BatchCourseSummary = Schemas["BatchCourseSummary"];
