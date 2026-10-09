# SkillifyMe UI guidelines

Approved Phase 3 Step 5 walkthrough and additions: 2026-10-06.

## 1. Tokens and themes

`apps/web/src/app/globals.css` is the single colour source. Use semantic
utilities: background/foreground, card, muted, primary, accent, input, border,
ring and success/warning/danger/info with their surfaces. Components must not
use palette utilities such as indigo-600, white/black, arbitrary hex/RGB/HSL
colours or inline colour literals. The source guard also checks generated notes
CSS. Syntax highlighting maps Pygments classes to semantic code variables here.

Indigo is the starting brand. The light canvas is slate-white and the dark
canvas is deep slate. Status pairs carry their own readable foreground/surface.
Use text and an icon/label alongside colour. Primary hover is opaque and
text/background colours switch together; animate transforms rather than
interpolating foreground/background through low-contrast intermediate pairs. Contrast tests verify all named
text/surface pairs at 4.5:1 and input/focus contrast at 3:1 in both themes.

Theme follows the OS by default. Appearance offers System, Light and Dark;
the manual preference persists through next-themes. It stores only appearance,
never authentication tokens. Do not introduce component-specific dark palettes.

## 2. Type, spacing and elevation

Keep self-hosted Geist and Geist Mono. Use 12px captions, 14px metadata,
16px body/inputs, 20px section titles, 24px phone page titles, 32px desktop
page titles, 40px public hero. Monospace is for code; scores and timers use
tabular numerals. One h1 per page; h2 sections then h3 subsections.

Spacing comes from the 4px unit: 4/8/12/16/20/24/32/48/64px. Phone gutters
are 16px. Controls have 8px radius, cards 12px, dialogs 16px. Use restrained
tokenized xs/sm elevation for headers/cards and lg for overlays. Long names
wrap or truncate with an accessible full name; never force page overflow.

## 3. Shells and navigation

All roles share the sticky header, account controls, breadcrumbs and page title
pattern. Keep organization and the actual active role visible. An org admin
viewing a shared teaching progress page remains labelled Org admin. Platform
context says All organizations. A skip link targets the main landmark.

Desktop has a sidebar; phones have bottom navigation. Platform shows Dashboard,
Organizations, Users, More; More opens an accessible dialog for Courses/Audit.
College admin shows Batches, Courses, Members, Import, with the area link opening
the overview. Instructor shows Courses, Grading, Question banks, Videos.
Question banks opens the bank editor and its question detail route in Step 6. Student shows My
learning and Catalog; the player outline becomes a native drawer on phones.
Shared shells are used throughout their role, including detail/editor routes.
Public home, catalog and authentication result states use the same header,
palette, controls and page/state patterns.

## 4. Landing pages and first run

Platform: global totals and role distribution first. Create college: three
taps; invite admin: three; inspect user: two. Organization detail offers the
first administrator invitation as its initial action.

College admin: batch mean completion/last activity, grants needing batch
assignment, pending invitations and running/failed imports. No enrollments is
shown distinctly from zero completion. Create batch: two taps from Batches;
import: five; assign granted course: three (Courses, Choose batches, checkbox).
The checkbox saves immediately. First-run checklist: create batch, import or
invite students, assign course. Completion derives from saved state.

Instructor: Needs attention above owned/assigned courses: ungraded count and
oldest, assignments due within seven days, incomplete students inactive seven
days, quiz pass/fail counts for the last seven days. Cross-course Grading is
ungraded first then oldest. Grade: Grading, submission, Save grade (three taps).
Create course: two; edit lesson: three. First run: create course, add lesson,
publish. Checklists tick real completed steps and disappear when all are done.

Student: Continue learning, Due soon, Recent results, then other courses.
Due soon lists unsubmitted assignments in the next seven days from the student's
displayed version and links directly to their lesson. Results show own latest
active assignment grades and submitted quiz scores only. Resume: one tap;
submit text via outline: four; view grade via outline: three. No courses offers
Browse catalog and explains that the college assigns courses to batches.

Tap counts exclude typing, scrolling and the OS file picker. Each additional
cursor page is one Load more tap. All learning panels check live entitlement;
publisher staff never gain access to another college's student work.

## 5. Loading, empty, error and save states

Use PageSkeleton with cards/table/form structure matching the loaded section.
Small embedded elements can use Skeleton directly. Loading has an accessible
status, no fake zero counts. EmptyState has a single primary action; first-run
admin/instructor organizations use FirstRunChecklist. Keep empty search and
first-run states distinct.

ErrorAlert displays the standard message and optional request reference, with
Retry refetching the failed query. Page-level errors use the same pattern.
Preserve entered work on validation/save errors. Never imply a failed write
succeeded. Sonner toasts use semantic status colours; success is concise, an
error includes recovery guidance, and critical errors also remain inline.
LiveAnnouncement provides polite save/timer status or an urgent alert. Do not
announce a countdown every second; announce meaningful timer thresholds.

Step 9: all notifications use `@/lib/toast`. The global LazyToaster loads
Sonner on first use, queues messages until its themed host mounts and delivers
each once. Failed chunk loads keep the messages in an accessible fallback
with shared Retry/Dismiss buttons; retry or the next notification reloads the
host. Never import Sonner directly from feature code or eager layouts.

ConfirmButton uses a focus-trapped dialog for destructive actions. Name the
resource and consequence, offer Cancel, disable the confirm while saving and
keep a failed operation's error visible. Never use window.confirm.

## 6. Forms, tables and progress

Use shared Label/Input/Textarea/NativeSelect/Checkbox/Button primitives and
FormField for label/help/error relationships and disabled/saving states.
Every control has a visible label or an explicit accessible name. Errors use
aria-invalid and aria-describedby, and the form keeps its root error inline.
Save buttons distinguish Saving from Save; prevent duplicate submissions.
Conditional-write conflict recovery keeps the existing If-Match contract.

DataTable defines columns once, a sticky desktop header and labelled mobile
cards from the same row data. Use a cursor LoadMore; no page numbers/offsets.
Large progress matrices retain an accessible desktop table and readable phone
cards. Lists that are already cards remain lists; do not add redundant tables.
Progress uses the shared labelled Progress primitive and a numeric text value.
Upload progress names the file and announces completion/failure.

## 7. Accessibility and responsive baseline

Everything is keyboard reachable. Focus uses a 2px semantic ring with a 2px
offset; never remove it. Buttons, selects, checkboxes and interactive navigation
have at least 44px touch targets. Inputs use 16px to avoid phone zoom. Dialogs
restore focus. Honour reduced motion for every animation/transition. Use
semantic headings, main/navigation landmarks, labels, status and error roles.
Provide image alt text and distinct navigation names. Keep a 360px viewport
free of document-level horizontal scrolling; data and code can scroll locally.

Tests cover light/dark contrast and the semantic-token-only source guard,
shared checklist/form/error behavior, 360px/1280px shell screenshots per role,
keyboard/focus/theme/reduced-motion checks, and axe scans with zero serious or
critical violations on every renderable application page route. Redirect/API
handlers are exercised through their destination states; Keycloak's hosted
login is an external page rather than an application page component.

## 8. Extending the shared set

Steps 6/7 reuse these guidelines and components. Each UI step summary names
the guideline sections applied and any new shared component. Add a pattern
only when it serves multiple screens; keep assessment rules in their feature
modules. Never leak implementation terminology into product flows.

Step 6 adds reusable PublishedContent for sanitized instructions with authorized
image URLs, LateStatus for the frozen due/penalty state, and GradeBreakdown for
earned marks, rubric criteria, penalty and final score. These patterns also
serve the Step 7 student results/history views. Authoring validators and exact
decimal grade previews remain in feature/helper code; the API owns grading.
