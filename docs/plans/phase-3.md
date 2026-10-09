# Phase 3 — Quizzes and full assignments

**Status: complete (2026-10-10).** All nine implementation steps passed their
local lint/type/test gates and green pushed CI. Decisions D1-D7 and the security
repair below include the user's approved revisions. The final documentation
close-out must also have green exact-commit CI before `v0.3.0` is tagged;
its run URL is recorded in the annotated tag and release summary.

**Baseline:** `4f7d00826e368b9dbd7ce1635332a8b1d93dedb7`, the peeled
`v0.2.5` release commit. The working tree was clean before this plan was written.
Phase 2.5 is complete; its assignments module is extended in place.

Binding references: [CLAUDE.md](../../CLAUDE.md), [AGENTS.md](../../AGENTS.md),
[access control](../access-control.md), [events](../events.md),
[Phase 2](phase-2.md) and [Phase 2.5](phase-2-5-demo.md), including its
"Open follow-ups / carried forward" and "Post-handover repairs" sections.
The design below was checked against the courses, enrollments, assignments,
media and skills models, services and relevant RLS migrations, the publishing
hooks, existing web forms and the request-transaction regressions.

## 1. Scope and inherited contracts

- New `assessments` module: reusable question banks, skill-tagged questions,
  quizzes, timed attempts, autosave, automatic scoring and controlled results.
- Extend `assignments`: submission and grade history, rubrics, server-enforced
  late policies and images in instructions.
- Extend publishing, enrollment completion, `/teach`, the enrollment player,
  events, demo seed and tests for both assessment types.
- All tenant tables use indexed `organization_id`, UUIDv7 identifiers and UTC
  `timestamptz`. Use cursor pagination, batched queries, the standard error
  envelope, service authorization and per-operation RLS.
- Preserve course ownership and batch visibility. Assigned-org staff read
  published course content; only owner-org editors author it. Assignment graders
  remain `instructor`/`org_admin` in the **student's** org, with the platform-admin
  override. Publisher staff do not acquire access to another org's student work.
- Preserve the shared function-scoped session dependency repaired in `9217b03`:
  commit before success headers, standard error envelope on failed commit,
  synchronous/asynchronous after-commit hooks only after successful commit.
  No per-endpoint transaction workarounds.
- Preserve the catalog invalidation, login-prefetch and demo-pagination repairs.
  Existing E2E assertions and timeouts remain in force.
- Labs and realtime notifications remain Phase 4; plagiarism and AI feedback
  remain carried forward. This brief does not add either to implementation scope.

**Acceptance flow:** an instructor builds and publishes a quiz and a rubric
assignment; an assigned student fails then passes the quiz, submits the
assignment late, receives rubric grading and sees the penalty, score and 100%
progress. A student in another batch cannot discover or open any of it. Run this
flow on desktop and at 360px, alongside the entire existing suite.

## 2. Module interfaces and publishing

`assessments` follows the established module layout:
`router.py`, `schemas.py`, `models.py`, `service.py`, `repository.py`, `tests/`;
add scoring and task helpers inside that module as needed. Answer autosave and
resume use Postgres only in this phase.

| Caller                                  | Service interface or hook                                                                                                                      |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| assessments / assignments → courses     | Editable lesson, course revision edits, published version and stable lesson references; shared notes validation/rendering                      |
| assessments / assignments → enrollments | Authorize the student's active enrollment/current batch grant, record authorized completion and recompute progress in the caller's transaction |
| assessments → skills                    | `existing_skill_ids` and `skill_names`; global taxonomy management rules stay unchanged                                                        |
| assignments → media                     | Validate confirmed images/submission files and issue short-lived signed URLs                                                                   |
| courses → assessments / assignments     | Centrally wired content-source service hooks; no importing their repositories or querying their tables                                         |
| enrollments → assessments / assignments | Centrally wired, batched completion-evidence service hooks; no assessment-table joins                                                          |
| reports / seed → assessment modules     | Read states or create demo work through the public service interfaces                                                                          |

Extend `courses.content_sources` and `app.wiring` instead of introducing a
second publishing mechanism. Hooks provide a safe preview/structural descriptor,
file references and a same-transaction publish callback. The callback receives
the newly created course-version ID and writes immutable assessment definitions
and private grading material through the assessments service. Preview remains
read-only. Missing registration or missing/invalid definitions fail closed.

Publish acquires the course revision lock and the relevant bank locks in a
consistent order. All question edits bump and lock their parent bank, so the
question set, public question text and private key copied by one publish cannot
come from different revisions. A rolled-back publish leaves neither assessment
versions nor course versions. Every entry point, including CLI and workers,
gets the same idempotent central wiring.

Quiz draft and published lesson **content is `{quiz_id}`**. Immutable quiz data
is resolved by `(course_version_id, lesson_id, quiz_id)` in assessments. Safe
structural descriptors live in separate snapshot metadata, not as answer-bearing
lesson content. Public outlines, catalog data and course caches contain no keys,
explanations, student answers or attempt results. Do not store hashes of answer
keys in public metadata; a small answer space could be guessed from a hash.

## 3. Assessments data and access

| Table                                          | Owner and purpose                                                                                                                                                                                                     |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `question_banks`                               | Author's org; name, description, revision, archive state and audit timestamps                                                                                                                                         |
| `questions`                                    | Same org and composite bank FK; type, prompt, stable option IDs/text or blank configuration, revision and archive state; no answer-key fields                                                                         |
| `question_skills`                              | Same tagging relationship as `lesson_skills`: question/skill pair, owner `organization_id`, indexed skill FK and existing global skill IDs                                                                            |
| `question_keys`                                | Same org; correct option IDs or accepted blank answers and explanations, separately protected by RLS                                                                                                                  |
| `quizzes`                                      | Course-owner org; one draft definition per quiz lesson, course/lesson, selection mode, marks, pass marks, timer, attempt allowance, randomization, reveal mode and reveal timing                                      |
| `quiz_versions`                                | Course-owner org; immutable published configuration and eligible question manifest, linked to the course version                                                                                                      |
| `quiz_version_questions` / `quiz_version_keys` | Immutable copies of published prompts/options/tags/marks and separately protected keys/explanations; draft edits/deletion cannot change an attempt                                                                    |
| `quiz_attempts`                                | Student's org; enrollment, course, stable lesson/quiz, course version, major, attempt number, selected question/order manifest, start/deadline/submission timestamps, state, revision, score, maximum and passed flag |
| `quiz_answers`                                 | Student's org and composite attempt FK; one answer per selected question, saved payload/revision; awarded marks populated only by finalization                                                                        |

Use composite tenant FKs where both sides share an org. Draft question/quiz
deletion or archiving cannot cascade into published definitions or student work.
Index all FKs and filter/order columns, including bank/type/skill searches,
attempt history and an expiry index on in-progress attempts. Database constraints
enforce valid states, unique attempt numbers and one in-progress attempt per
enrollment/lesson/major. Archive referenced banks/questions rather than deleting
their published copies.

Question-bank authoring uses `course.edit` in the active org: instructors and org
admins may author their own org's banks, including private-course orgs. A
`lab_author` alone does not gain quiz authoring. Using existing skills does not
grant permission to create taxonomy entries. A quiz can select only its owner's
questions/banks.

### Answer secrecy: two independently tested layers

1. **Database layer.** Draft/publication key tables have editor-only SELECT RLS;
   student queries return zero rows, including queries through joins or views.
   The app role owns no tables and has no `BYPASSRLS`. Public question projections
   have no key or explanation columns. Students can read only prompts selected
   for their own attempts, their own saved answers and their own attempt records.
   Assigned-org reader staff can preview published questions without gaining
   author-bank key access.
2. **Trusted grading and reveal.** Narrow `SECURITY DEFINER` functions read the
   protected published keys, finalize a permitted attempt, or return permitted
   post-submission solution data. Fixed `search_path`, bound inputs, no PUBLIC
   execution, validated tenant/user, enrollment, version, selected questions,
   revision, state and deadline. No callable function previews a score/key for
   an unfinished attempt. The app role cannot directly write protected attempt
   state, deadlines, marks or answer scoring columns; narrow mutation functions
   and grants/guards prevent manufacturing a submitted attempt to bypass reveal.
   Cross-module policy checks use documented SQL helper interfaces.
3. **Response layer.** Separate author, active-attempt and result models. Active
   question models contain prompt/options/marks/tags and the student's saved
   answer, with typed nested fields and `extra="forbid"`; they never declare
   correct answers, explanations, correctness or awarded marks. Result models
   are discriminated by reveal mode: score-only, correct-answers, explanations.
   Score-only has aggregate score/max/pass only; correct-answers omits explanation
   fields; explanations includes both. State checks precede selecting the result
   model. Both DB reveal functions and schemas enforce `reveal_timing` as well
   as the reveal mode; submitted results remain score-only until timing permits
   solutions. No untyped question JSON, shared author/student model or generic ORM
   serialization may bypass this contract.

Direct app-role SQL tests prove database protection without using HTTP or
Pydantic. Separate schema/serializer tests feed privileged key-bearing fixtures
to student projections and prove that secret fields cannot serialize, without
relying on RLS to remove them. HTTP tests cover every student entry point,
including outlines, old results, errors and concurrent finalization.

**Existing outbox backdoor to close:** migration `0002` scopes app-role outbox
SELECT only by org, while `assignment_graded` contains student IDs and scores.
Narrow SELECT for sensitive learning events to the owning student and permitted
staff of that student's org, using documented ownership helpers. Cover existing
v1 grade events as well as new events. Preserve INSERT/RETURNING for legitimate
student writes and the relay's separate read/update policies. Raw SQL tests must
prove another student's result is inaccessible through outbox payloads too.
Keep private keys/explanations out of events and audit payloads entirely.

### Quiz selection and scoring (decision D1)

- Two selection modes: an explicit ordered set of questions with per-question
  marks, or **pull N from one bank** (optionally skill-filtered) with a common
  mark value per draw. No replacement. Freeze the eligible pool at publish;
  later bank additions do not silently change a published quiz.
- Store the chosen set/order once at attempt creation. A retake can draw a fresh
  set from that frozen pool. `randomize_order` shuffles presentation order on
  the server; refresh never redraws or restarts the timer.
- `pass_marks` is an absolute decimal mark threshold, not a percentage;
  `0 < pass_marks <= max_marks`. Bank draws have fixed total `N × marks_per_draw`.
  Scores use decimal arithmetic and round half-up to two places per question;
  total is the sum of awarded marks. Ignore client-supplied scores and timestamps.
- `mcq_single`: exactly one valid option, full marks for the correct option,
  otherwise zero. An unanswered question scores zero.
- `mcq_multi`: at least one correct and one incorrect option. With `C` correct
  options and `W` incorrect options, award
  `marks × max(0, correct_selected/C - incorrect_selected/W)`. Selecting every
  option scores zero. For 4 marks with correct `{A,B}` and wrong `{C,D}`:
  `{A}` → 2, `{A,B}` → 4, `{A,C}` → 0, `{A,B,C}` → 2. Duplicate/unknown option
  IDs are rejected. No negative question scores.
- `fill_blank`: one blank per question, full marks for an accepted alternative,
  otherwise zero; Unicode NFC plus outer whitespace trimming. Case-insensitive
  by default with an author-controlled case-sensitive flag for code/identifiers.
  Accepted alternatives and normalization settings belong to private keys.
  No regex or AI grading in this phase.
- Validate bounded payloads, positive marks, nonempty questions/options/keys,
  integer positive attempt allowance/time limit, enough eligible questions and
  known skill tags. Publish blocks incomplete quizzes with `quiz_not_ready`.

## 4. Attempts, timer and autosave

The student lesson GET returns quiz rules, attempts used/remaining, the attempt-set
revision and any active attempt ID. Starting an attempt requires that revision
in `If-Match` (`0` before the first), then serializes against the same
enrollment/lesson/major using a database lock. Unique numbering and the partial
active-attempt index backstop concurrent starts. Every started attempt counts,
including failed, abandoned and expired attempts. Resume the active attempt;
starting one never silently abandons it. Minor releases do not reset the budget.

Persist `started_at` and `expires_at = started_at + time_limit` using the
Postgres clock. Return `server_now`, `expires_at` and attempt revision. Recheck
the database clock after acquiring the attempt lock on every answer write and
submission. Never trust a browser timer, client timestamp or elapsed
duration. Closing/reopening a tab or changing device does not stop the clock.

### Autosave durability (decision D2)

The client saves changed answers every **10 seconds**, with serialized mutations
and the current attempt `If-Match`; also try to save on navigation/visibility
changes. The API validates question membership/answer types and atomically
persists the accepted answer batch and new revision in **Postgres**. Every
acknowledged 10-second autosave is a committed Postgres write; resume reads
Postgres after access validation through DB/RLS. No Redis answer cache, revision
mirroring or answer-cache failure-mode tests are part of this phase. Retain the
separate-connection visibility test for every write and failed-commit hook tests.
No expiry job fires after a failed commit. Measure query counts and batch writes;
do not claim that 10-second database checkpoints have been proven at 100,000
concurrent users. A Redis read cache is a follow-up only if measured load requires it.

### Expiry and finalization

- At `server_now >= expires_at`, reject all new answer payloads, even if a
  client continues posting with fresh revisions. No client grace period.
- A manual submit before expiry may include the final answer batch; after
  expiry it scores only the last accepted checkpoint and ignores late answers.
  Lock the attempt and finalize once. Repeated/concurrent submits cannot create
  a second grade, completion or event.
- The UI disables input at zero and submits automatically, but server submission
  also works without a browser: enqueue an expiry task after start commits and
  run a Celery beat sweeper over due attempts (proposed default every 5 seconds,
  configurable in `.env.example`). Recheck eligibility/state/time in the worker;
  use bounded batches, row locks/skip-locked and explicit system transactions.
- The deadline is exact even if worker execution is delayed: no late answer is
  accepted. Finalization eventually catches up after worker restart. Answers and
  attempt state/results are durable in Postgres.
- A late autosave returns the standard `409 attempt_expired`/`attempt_closed`
  error without saving; scheduled expiry finalization is independent. Never
  stage a finalization then raise an error that rolls that finalization back.
- Results and histories require the same current enrollment/batch authorization
  as the player. Revocation prevents new access immediately. Preserve stored
  attempts; expiry may seal them without granting access or completing a revoked
  enrollment. Opting into another major closes old active attempts and stops them
  contributing new completion to the new major.
- Each attempt freezes the exact published quiz, questions, keys, reveal policy,
  marks and limits it started with. A later minor cannot change an active attempt
  or rewrite an existing score.

**Reveal decision D3:** each quiz has `reveal_mode` (`score_only`,
`correct_answers`, `explanations`) and `reveal_timing` (`immediately`,
`after_attempts_exhausted`). Defaults are **score_only / immediately**. Before
submission, no keys, correctness hints or explanations. Immediate mode permits
the configured solutions after that attempt submits, including expiry. Delayed
mode permits solutions only after the allowance for the same enrollment/lesson/
major is exhausted and all started attempts are terminal. Earlier submitted
results remain score-only until then. Both the DB reveal function and result
schemas enforce mode and timing independently. A later major has its own budget;
a minor does not reset it. Policies are frozen per attempt. Reveal data never
uses a shared/public cache.

## 5. Extending assignments without replacing them

### Migration and history

- Keep `assignments`, `assignment_submissions`, existing submission IDs, URLs,
  revision semantics, text/file limits and submission-file flow.
- Add nullable rubric JSON and late-policy JSON to draft definitions; old rows
  and old published snapshots default to **no rubric / accept late**.
- Add `submission_attempts` in the existing module. Each accepted submission,
  including replacement through the existing PUT, creates an immutable attempt:
  student org, parent submission, monotonic attempt number, course version,
  text/file, server submission time, frozen assignment rules and lateness.
- `assignment_submissions` remains the stable aggregate with exactly one
  `active_attempt_id`; its existing content/status fields project that active
  attempt for legacy callers. Switch the pointer, projection and revision
  atomically under the submission lock. Historical content is never overwritten.
- Drop **`uq_assignment_grades_submission`**. Add an attempt FK, grade sequence,
  rubric breakdown, raw score, penalty data and effective score to grades.
  New grades and corrections append rows; the active attempt's latest grade is
  selected deterministically. Preserve existing grade IDs and their score values.
- Backfill exactly one attempt from each surviving Phase 2.5 submission and link
  its existing grade. Keep version, timestamps, content, IDs, revision and
  completion. Earlier overwritten submissions/grades were not retained and
  cannot be reconstructed; do not fabricate historical attempts.
- Attempt/grade SELECT RLS permits the owning student and that student's org's
  graders, never other students or course-owner staff in another org. Content
  and grade history are append-only under app-role grants/guards. Grading checks
  the active attempt and submission revision, refuses self-grading and records
  audit before/after plus the grade event in the same transaction.
- Preserve `409 already_graded` for student replacement after grading. A grader
  can correct the active attempt's grade, retaining every previous grade. Only
  the active attempt is gradable; histories are read-only and cursor-paginated.

### Rubrics

Rubric JSON contains stable criterion IDs, label, description and maximum marks.
Criterion maximums sum exactly to assignment `max_marks`. Validate unique IDs,
bounded criterion count/text, positive maxima and two-place decimal values.
Without a rubric, the existing total-score grading form/body still works.

For a rubric assignment, require one score for every criterion, with no duplicate,
missing or unknown IDs and each score within its maximum. Derive the raw score
server-side from the sum; a client total cannot replace criterion scoring.
Freeze the rubric with each attempt so later author edits cannot change grading.
Grade corrections append the new breakdown and recalculate from that same frozen
late policy; never apply a penalty twice.

### Late policy (decision D4)

Policies: `accept`, `reject`, or `penalty` with decimal `percent_per_day` in
`(0,100]`. A missing due date means on time/no penalty; reject a penalty-policy
configuration without a due date so its meaning is explicit.

- Submission time is the server's accepted UTC timestamp, not upload start or
  client time. Exactly at the due instant is on time; any later instant is late.
- `late_days = ceil((submitted_at - due_at) / 24 hours)` for late submissions,
  otherwise 0. Use UTC elapsed periods, not local calendar dates.
- `accept`: keep late status and days; zero penalty.
- `reject`: after due, return `409 assignment_closed`; no submission, attempt,
  grade or learning-event row is created/changed. Recheck on actual submission
  even if an upload ticket was issued before due.
- `penalty_percent = min(100, late_days × percent_per_day)`;
  `penalty_marks = round_half_up(raw_score × penalty_percent / 100, 2)`;
  `score = max(0, raw_score - penalty_marks)`.
  Approved: deduct from **earned marks**.
  Example: 8/10, one begun late day at 10% → 0.80 penalty, **7.20/10**.
- Freeze due date, policy, days and percentage at submission. Later due-date or
  policy edits do not retroactively alter accepted work or previous grades.
  Student GET returns server time, late status and current projected percentage
  before submission; submit rechecks and returns the accepted values.

### Instructions images

Remove the current `instructions_images` prohibition and use the existing notes
allow-list, Tiptap `image` by `file_id`, size/signature confirmation, sanitized
HTML and `<img data-file-id>` rendering. Validate every image through
`media.service`: confirmed image, same course-owner org.

Extend content-source file references so published assignment images populate
`course_version_lessons.file_ids`; do not inspect the `{assignment_id}` draft
reference as if it held the instructions. Reuse existing image-URL endpoints for
assignment lessons as well as notes, plus an owner-only draft preview. Refresh
signed URLs as today; never persist them in snapshots. Historical work keeps its
submission-file access. If a student's history shows older instructions images,
use a narrow own-attempt/version media helper with current batch access, rather
than widening all historical course-file access.

### Endpoint/schema compatibility (decision D5)

Existing assignment endpoints keep their URLs, operation IDs and legacy request/
response fields. Extend those responses additively with nullable/optional rubric,
late data, active-attempt ID and grade breakdown. No `schema_version` parameter
or versioned response union. One active submission and its latest grade remain;
`grade.score` is the final effective score. Historical lists are separate cursor
endpoints, not an unbounded array replacing `submission`.

New assignment input fields are optional for old callers. Omitted Phase 3 fields
in a legacy definition save preserve existing rubric/policy values, rather than
accidentally clearing them. Explicit fields can set/clear them. The grade
endpoint accepts the existing score body for rubric-free assignments and a
body extended with criterion scores for rubrics; total-only grading cannot bypass an
assignment's rubric requirement (`422 rubric_scores_required`).

**Approved header tightening:** the existing
`POST .../submission-upload` and `POST /courses/{id}/versions` currently accept no
`If-Match`. Require the current submission revision (`0` initially)
on uploads and the course revision on publishing, updating first-party callers,
seed service calls and test setup. This preserves the endpoints/bodies but
tightens their header contracts; old callers that omit it would receive 428.
All new/extended Phase 3 mutation routes require `If-Match`. Existing unrelated
media/identity endpoints retain their contracts; their reuse is not a blanket
concurrency retrofit.

## 6. Completion and versioning

Quiz leaves `PLACEHOLDER_LESSON_TYPES`; new quiz lessons are required by default.
Lab remains a placeholder. Existing immutable optional quiz placeholders stay
optional; configuring one does not rewrite an old published version. New quiz
publishes require a ready quiz reference; changing `is_required` is major-only.
Manual `/complete` rejects quiz completion with `409 completed_by_quiz`.

- Quiz completion: at least one submitted attempt with `score >= pass_marks`.
  A failed attempt stays incomplete; passing records completion and recomputes
  progress in the scoring transaction. Later failures do not remove completion.
- Assignment completion: graded, regardless of numeric score or late penalty,
  as Phase 2.5. Replacement before grading is incomplete; regrading does not
  duplicate completion. No new assignment passing threshold.
- Enrollments gets **batched completion evidence** through assessments and
  assignments service hooks, wired centrally to avoid import cycles. Recompute,
  grade, quiz submit, expiry and major-upgrade paths use those interfaces only.
  Enrollment repositories query enrollment/progress tables, never assessment
  tables. Existing `app.lesson_graded`/`app.enrollment_graded` helpers are adapted
  to grade history without broadening staff's progress-column rights.
- Preserve completion for surviving stable lesson IDs on major opt-in, per
  CLAUDE.md, including assessment lessons. New required lessons reduce progress
  until done; removed lessons stop counting. Freeze past scores; budgets reset
  for the new major, not a minor. Old active attempts cannot mark new-major work
  complete. Recompute is batched and idempotent; completion events emit once.

Structural decisions (D6) are enforced in **both** preview and publish, with
named quiz/assignment diff entries and affected lesson IDs:

| Assessment | Structural: blocks minor (`409 minor_not_allowed`)                                                                                                                                                            | Minor-safe corrections                                                                                                                                 |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Quiz       | Explicit question membership; bank/eligible pool/filter/N; question or draw marks; total/max marks; pass marks; time limit; attempts allowed; question type, option identity or grading/accepted-answer rules | Title, prompt/option wording, explanations, skill tags and presentation randomization/reveal mode and timing                                           |
| Assignment | Max marks, allowed submission kinds, rubric criterion IDs/add/remove or criterion maximums                                                                                                                    | Title, instructions/images, due date, late-policy mode/rate (frozen per submission; future submissions only), rubric labels/descriptions/display order |

Private key/grading-rule differences are compared inside assessments through a
safe structural-diff hook; return change codes only, never key material in a
preview. Bank skill-filter membership changes are structural even if the tag
edit itself would otherwise be a correction. Bank edits do not take effect until
republish. Both manual and bank-pull quizzes get a deterministic comparison.
Active attempts and accepted assignment submissions always keep their exact
published rules. No automatic retrospective regrade or attempt refund is added.

## 7. Endpoint inventory and concurrency

All paths below are under `/api/v1`. Every method gets a MATRIX row and an
access-control entry when introduced; extend existing rows for changed headers,
additive fields and role expectations. Every list uses a cursor and bounded page.
Return 428 for missing `If-Match`, 409 for stale revisions and the standard
envelope; hidden cross-org/batch/student resources return 404.

| Endpoint                                                                            | Caller / revision for mutation                                                                 |
| ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `GET`, `POST /question-banks`                                                       | Active-org course editors; create uses `If-Match: 0`                                           |
| `GET`, `PATCH`, `DELETE /question-banks/{id}`                                       | Bank-owner editors; PATCH/archive uses bank revision                                           |
| `GET`, `POST /question-banks/{id}/questions`                                        | Bank-owner editors; search by type/skills/text; POST uses bank revision                        |
| `GET`, `PATCH`, `DELETE /questions/{id}`                                            | Bank-owner editors; mutations use parent bank revision                                         |
| `PUT /questions/{id}/skills`                                                        | Bank-owner editors; parent bank revision, validate global skill IDs                            |
| `GET`, `PUT /courses/{id}/lessons/{lesson_id}/quiz`                                 | Course-owner editors; PUT uses course revision                                                 |
| Existing `GET .../publish-preview`, `POST /courses/{id}/versions`                   | Course-owner editors; assessment diffs/snapshots; POST uses the course revision under D5       |
| `GET /courses/{id}/versions/{version_id}/lessons/{lesson_id}/quiz`                  | Published-course reader staff; safe question preview, no author keys                           |
| `GET /enrollments/{id}/lessons/{lesson_id}/quiz`                                    | Enrolled student with current batch access; rules/counters/active attempt                      |
| `GET`, `POST /enrollments/{id}/lessons/{lesson_id}/quiz-attempts`                   | Own student history/start; POST uses attempt-set revision                                      |
| `GET /quiz-attempts/{id}`                                                           | Own enrolled student; active projection or terminal state, never a key-bearing active question |
| `PUT /quiz-attempts/{id}/answers`                                                   | Own active attempt; attempt revision; batch autosave payload                                   |
| `POST /quiz-attempts/{id}/submit`                                                   | Own attempt; attempt revision; server scores/finalizes                                         |
| `GET /quiz-attempts/{id}/results`                                                   | Own submitted attempt; reveal and current access enforced in DB/service/schema                 |
| Existing `GET`, `PUT .../lessons/{lesson_id}/assignment`                            | Owner editors; PUT retains course revision; additive rubric/late inputs/outputs                |
| `GET .../lessons/{lesson_id}/assignment/preview`                                    | Owner editors; sanitized draft instructions and signed image URLs                              |
| Existing student assignment GET/upload/PUT submission                               | Own student; additive fields; submission revision for PUT and approved upload hardening        |
| `GET /enrollments/{id}/lessons/{lesson_id}/submission-attempts`                     | Own student attempt history, cursor-paginated                                                  |
| Existing submissions queue/detail/PUT grade                                         | Student-org graders; submission revision, active attempt only; additive rubric body and detail |
| `GET /assignment-submissions/{id}/attempts`                                         | Student-org graders; all attempts, cursor-paginated                                            |
| `GET /assignment-submissions/{id}/attempts/{attempt_id}`                            | Student-org graders; pinned instructions/work and latest historical grade                      |
| `GET /assignment-submissions/{id}/attempts/{attempt_id}/grades`                     | Student-org graders; all grade revisions, cursor-paginated                                     |
| `GET /enrollments/{id}/lessons/{lesson_id}/submission-attempts/{attempt_id}/grades` | Own student grade history, cursor-paginated                                                    |
| Existing draft/student/staff image-URL reads                                        | Extend type handling for assignment instructions; current access and pinned/history scope      |

Revisions are resource-specific: bank for question-bank edits, course for quiz/
assignment definition edits, monotonic started-attempt count for quiz starts,
attempt revision for autosave/submit and submission revision for submission/
grading. Bank edits and course edits do not share a counter. Mutations on one
resource serialize in the client; server locks and compare-and-update enforce
the rule across clients. Creation/deletion/archive/regrade are included, not
only PATCH/PUT. Internal expiry jobs use state/revision locks, not HTTP headers.

## 8. Events

Use the existing **registered `enrollment` aggregate → `learning.enrollments.v1`**
topic, keyed by enrollment ID, so assessment results and `lesson_completed` stay
ordered with the student's existing learning events. Register/document the new
event type explicitly, assert `TOPICS` resolves this aggregate, and test real
relay delivery instead of letting it use `platform.events.v1` by accident.

| Event                       | Payload and version                                                                                                                                                                                      |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `quiz_attempt_submitted` v1 | Attempt/quiz/enrollment/user/course/lesson/course-version IDs, attempt number, score/max/pass marks as exact decimal strings, passed boolean, manual/expiry reason, start/deadline/submission timestamps |
| `assignment_graded` v2      | Existing IDs and regrade/grader fields, plus attempt/grade IDs and sequence, raw score, rubric criterion IDs/scores/maxima, late days/percentage/penalty marks and final effective `score`               |
| `assignment_submitted` v1   | Preserve existing fields/meaning; replacements now retain an attempt behind the same aggregate                                                                                                           |
| `lesson_completed` v1       | Existing event; quiz type already belongs to its enum; no duplicate on repeat finalization/regrade                                                                                                       |

Add full JSON Schemas with `additionalProperties: false` to `docs/events.md` and
catalogue/topic documentation. Retain the v1 grade schema for queued/historical
events; producers emit v2 after the migration and consumers handle both versions.
Update `tests/test_event_schemas.py` to key schemas by **(type, version)**; it
currently discards the parsed version and asserts every emitted version is 1.
Validate actual v1 and v2 fixtures and real new producer flows. This changes the
test's schema lookup to match the documented versioning contract; it does not
relax payload validation. State, grade, audit, progress and outbox commit together.
Events contain no keys, explanations, submission text/file contents or raw quiz
answers. At-least-once consumers still dedupe by envelope ID.

## 9. UI

Read `apps/web/AGENTS.md` and the relevant installed Next.js docs before web
implementation. Use generated API types, React Hook Form/Zod and existing query,
mutation, upload, IST display and lazy editor conventions.

### Step 5: UI foundation and design system

Before coding step 5, present a short written mockup walkthrough for each role:
what its landing shows first, the two or three most frequent tasks and taps per task.
Wait for the user's approval of that walkthrough. Deliver `docs/design/ui-guidelines.md`
and working components covering:

- **Tokens and themes:** globals.css/Tailwind tokens for a deliberate type and
  spacing scale, one brand palette, semantic success/warning/danger/info colours,
  focus ring, radius and elevation; light and dark. Components use semantic tokens only. A test rejects raw Tailwind colour classes
  (including indigo-600) and colour literals outside the central token file.
  Theme defaults to the system setting with a persistent manual override.
- **Role shells:** one shell each for `/platform`, `/admin`, `/teach`, `/learn`;
  consistent header, desktop sidebar and mobile bottom navigation or drawer,
  breadcrumbs, page titles and persistent location/org/role indicator.
- **Shared states and patterns:** skeletons matching final layout; empty states
  with one clear action; inline/page errors with retry; toast conventions;
  destructive confirmation dialogs; fields with label/help/inline error and
  disabled/saving states; data tables with sticky header, mobile card fallback
  and cursor Load more; progress indicators. Use these throughout.
- **Accessibility:** keyboard reachable controls and visible focus; WCAG AA
  contrast checked by a test; aria-live for save/timer announcements; touch
  targets at least 44px; respect reduced motion.
- **Retrofit:** every existing platform/admin/teach/learn/auth/home chooser/catalog
  screen uses the shells and shared components. Existing flows retain their
  behavior; the approved landing summaries, first-run checklists and cross-course
  grading below are added. Existing assertions and timeouts remain unchanged.
- **Evidence:** Playwright shell screenshot assertions per role at 360px and
  1280px (content is not pixel-perfect); axe-core scans on every route with zero
  serious/critical violations.

### Approved role walkthrough (2026-10-06)

Tap counts count navigation, selections and actions, excluding typing, scrolling
and the OS file picker. Each additional cursor page takes one Load more tap.
The persistent header shows working area, actual active role and organization;
platform context says All organizations. Shared breadcrumbs and a page title
orient every screen. Account controls include organization, theme and sign out.

| Role           | Landing content, in order                                                                                                                                                                                                                            | Frequent phone tasks                                                                                                                                                                             | Desktop / mobile navigation                                                                                                                                |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Platform admin | Global organization, user, course, enrollment and activity counts, then role distribution                                                                                                                                                            | Create a college: 3 taps (Organizations, New, Create); invite its admin: 3 (Organizations, college, Send invitation); inspect a user: 2 (Users, user)                                            | Sidebar: Dashboard, Organizations, Users, Courses, Audit. Bottom nav: Dashboard, Organizations, Users, More; More opens Courses/Audit drawer               |
| College admin  | `/admin` overview: batches with completion percentage and last activity, highlighted granted courses with no batch assignment, pending invitations, running/failed imports                                                                           | Create a batch: 2 from Batches (New, Create); import students: 5 (Import, file, batch selector, batch, Start); assign a granted course: 3 (Courses, Choose batches, checkbox; saves immediately) | Sidebar and bottom nav: Batches, Courses, Members, Import; overview is the area/home link                                                                  |
| Instructor     | `/teach`: Needs attention above course list: ungraded count and oldest submission, assignments due within 7 days, students inactive for 7+ days in my courses, quiz pass/fail counts for the last 7 days. Owned and assigned courses remain distinct | Create course: 2 (New, Create); edit lesson: 3 (course, lesson, Save); grade: 3 (Grading, submission, Save grade)                                                                                | Sidebar and bottom nav: Courses, Grading, Question banks, Videos. Cross-course Grading orders ungraded first; Question banks is a placeholder until step 6 |
| Student        | Continue learning, Due soon (next 7 days, assignment links open the lesson directly), Recent results (latest grades and submitted quiz outcomes), then other courses                                                                                 | Resume: 1; submit text via outline: 4 (course, outline, assignment, Submit); view grade via outline: 3. Due soon opens its assignment in 1                                                       | Sidebar and bottom nav: My learning, Catalog. Player outline is visible on desktop and opens above lesson content on mobile                                |

New college-admin organizations show a first-run checklist: create a batch,
import or invite students, assign a course. New instructor organizations show:
create a course, add a lesson, publish. Completion is derived from real saved
state; steps tick off and the checklist disappears when all are complete.
Platform organization detail offers Invite admin as its first action; a student
with no assignments has a Browse catalog action and explains batch assignment.

The approved palette starts with indigo brand `#4338CA` / `#A5B4FC`; light
background/surface `#F8FAFC` / `#FFFFFF`, dark `#0F172A` / `#1E293B`;
primary/secondary text light `#0F172A` / `#475569`, dark `#F8FAFC` / `#CBD5E1`;
success `#166534` / `#86EFAC`, warning `#92400E` / `#FCD34D`, danger
`#B91C1C` / `#FCA5A5`, info `#1D4ED8` / `#93C5FD`. All component colours
reference semantic tokens in one file. Status uses text/icon as well as colour.
Actual foreground/background combinations, including dark and focus states,
are verified for WCAG AA rather than assuming this starting palette passes.

Keep Geist and Geist Mono. Type sizes: 12 caption, 14 metadata, 16 body/input,
20 section, 24 mobile page title, 32 desktop page title, 40 public hero.
Spacing: 4, 8, 12, 16, 20, 24, 32, 48, 64px; phone gutters 16px.
Radii: 8px controls, 12px cards, 16px dialogs. Restrained elevations and a
2px focus ring with 2px offset. Scores and timers use tabular numbers.

Shared set: role shell/header/context switcher/sidebar/mobile nav/drawer,
breadcrumbs/page header; page/card/table/form skeletons; actionable empty state;
inline/page errors with retry; form field label/help/error/saving pattern;
sticky-header data table/mobile cards/cursor Load more; confirmation dialog;
toast conventions; status badge/progress/upload progress/live announcements.
All controls are keyboard reachable, visibly focused and at least 44px touch
targets. Reduced motion is respected. Shell screenshots run at 360px and 1280px
for all four roles; axe scans cover every application page route with no
serious/critical violations.

Also approved for this commit: a configuration regression proving the actual
Celery beat schedule registers `assessments.sweep_expired` at the configured
interval, including a non-default interval. This complements the existing
browserless production-sweeper tests without claiming a live broker-clock test.

Steps 6 and 7 build on these components and guidelines. Each UI step's summary
lists the guideline sections applied and every new shared component.

### Steps 6 and 7: assessment interfaces

**Instructor `/teach`:**

- `/teach/question-banks` and `/teach/question-banks/[id]`: cursor-paginated
  search/filter, create/edit/archive questions, single/multi options or accepted
  blanks, explanations and the existing skills picker.
- Extend the existing course lesson page with a quiz builder: explicit question
  set or bank/N selection, marks/pass threshold, time limit, attempts,
  randomization, reveal mode/timing, ready-state validation and publish-preview diff.
- Extend the assignment editor in place: criteria and point totals, due date,
  late policy, existing kinds and notes image toolbar/preview. Course If-Match
  serialization and 409 recovery remain intact.
- Extend the existing assignment queue/review routes with an attempt timeline,
  pinned instructions, late details, historical grades and rubric score inputs.
  Only the active attempt has a grade action; criterion totals/penalty/final
  score preview agree with the server. Regrade retains a visible history.

**Student `/learn`, within the enrollment player:**

- Quiz landing shows time limit, threshold, allowance and Start/Resume; active
  attempt page under the existing lesson route shows safe questions, answer
  controls and a server-deadline countdown. Resynchronize time on responses and
  resume; the browser clock is for display only.
- Dirty answers autosave every 10 seconds. Show saving/saved/retry state, retain
  unsaved answers in memory on temporary failure and warn on navigation where
  supported. No correctness feedback during the attempt; refresh restores the
  accepted answers and original deadline. Multi-tab 409 prompts refresh rather
  than overwriting another device. Timer-zero forces submission and stops input.
- Results page respects reveal mode and timing, shows score/pass/remaining attempts
  and allowed retake/history, then refreshes player progress. It never derives
  reveal fields from the author schema or a client-only flag.
- Assignment page shows server-calculated on-time/late/closed status, days and
  projected penalty **before** submit. Show the accepted lateness afterward;
  after grading show rubric breakdown, earned marks, penalty and final score.
  Keep text/file submission behavior and add paginated attempt/grade history.
- Mobile first at **360px** for question editing, builder, rubric grading and
  student flows: keyboard-accessible controls, readable choices, adequate touch
  targets, no page overflow and focus/error announcements. Keep Tiptap and other
  authoring dependencies out of student bundles. No realtime stack is introduced.

## 10. Demo seed (decision D7)

Extend `make seed-demo` through the new services, preserving credentials reuse,
`.secrets/demo-credentials.txt`, password secrecy, existing local/nonlocal
`--reset`/`--rotate-passwords` guards and refusal in production.

- Python Foundations gains one required quiz spanning all three question types,
  Python skill tags, a finite timer and at least two attempts. Seed untouched,
  failed, failed-then-passed and expired cases, plus varied existing progress.
  Keep video/notes/PDF/assignment coverage and add rubric/late examples. Lab
  remains a Phase 4 placeholder; do not fabricate lab completion to claim it works.
- Freeze a seed due date once so reruns do not move it. Any synthetic historical
  timestamps/expiry preparation are confined to a guarded CLI helper, with
  `test_cli_boundaries` coverage. Quiz selection/scoring/finalization and rubric
  grading still run through the normal service interfaces, not direct score inserts.
- Fresh seeds build the full Phase 3 course. An existing six-lesson 1.0 demo
  requires a **major** publish to add the required quiz; a minor or silent
  snapshot edit is forbidden. Use the approved guarded `--upgrade-course` flag to
  add the missing demo content without resetting work or rotating passwords.
  Upgrade existing enrollments through the college-admin service for the
  **whole CSE batch**, never individual students. Refuse to overwrite an edited
  demo definition; report the required operator action.
- A normal rerun leaves existing human edits/work intact and reports if the
  course needs that explicit upgrade. An approved local upgrade runs once,
  then plain `make seed-demo` twice proves idempotence. `--reset` clears the
  demo's new attempts/grades/caches as well as existing progress, within its
  established guard; it does not automatically rotate credentials.
- Update seed assertions and read-only four-role smoke checks for the richer
  demo and recomputed percentages. Read-only smoke does not submit/grade work.

## 11. Test requirements

Use real compose Postgres/Redis/Keycloak/MinIO for API/integration tests, Vitest
for useful frontend behavior and Playwright for actual user journeys.

| Area                | Required evidence                                                                                                                                                                                                                                                             |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Migration           | Upgrade a populated 0011 database: existing submissions, grades, revisions, files and completions survive; exactly one backfilled attempt per existing submission; model/schema/FK-index/RLS guards pass                                                                      |
| DB secrecy          | Direct app-role SQL cannot read draft/published keys or explanations, reveal before submission, alter protected state/scores/deadlines, read other students' attempts/answers/grades/outbox result payloads, or use another org/batch/version                                 |
| Schema secrecy      | Privileged fixtures cannot serialize keys through active models; score-only excludes per-question grading/key data; correct-answers excludes explanations; nested extra fields and error payloads cannot leak; mode and timing enforced independently                         |
| Ownership           | Assigned org cannot author banks/quizzes/assignments; publisher cannot grade another org; local graders see all their students' attempts but grade only the active one; self-grade refused                                                                                    |
| Attempts            | Concurrent starts, one active, quota including expiry/abandonment, resume, reload, major budget vs minor continuity, stable draw/order and revoked access                                                                                                                     |
| Timer/Postgres      | DB-clock boundary including exact expiry; late posts cannot change answers; client clock tampering; no-browser worker submission; worker restart/replay; durable autosave/resume visibility and failed-commit hooks                                                           |
| Scoring             | All three types, multi-select partial marks/select-all/duplicates/unknown IDs, blank normalization/case rules, decimal rounding, forged scores and incomplete answer batches                                                                                                  |
| Assignments         | Immutable replacements, one active pointer, grade/regrade history, stale concurrent submit vs grade, frozen versions/rubrics, old endpoint bodies and additive nullable/optional fields without a version flag                                                                |
| Late/rubric         | Due equality, one second/24h/over 24h, cap at 100%, earned-mark math and rounding, missing due/policy validation, reject with no state/event change, every criterion bound and no double penalty on regrade                                                                   |
| Images              | Confirmed same-org images, XSS/URL allow-list, snapshot file references, signed URLs/expiry, cross-org rejection and narrowly authorized historical instructions                                                                                                              |
| Progress/versioning | Fail then pass; assignment completion at any grade; 100% only for all required lessons; repeat idempotence; service-only batched recomputation; stable-ID carryover; every structural field blocks minor in preview and publish; allowed corrections freeze active/past rules |
| Events              | Strict documented schemas by type/version, old grade v1 compatibility, grade v2 breakdown, expiry/manual quiz events, topic/key mapping, real relay and rollback/idempotence                                                                                                  |
| UI foundation       | Tokens and hex-value guard; light/dark WCAG AA contrast tests; keyboard/focus/touch/reduced-motion checks; per-role 360px/1280px shell screenshots; axe-core zero serious/critical violations on every route                                                                  |
| API coverage        | MATRIX row per method and access-control line per endpoint; 428/409 tests for every mutation and 404 for concealed resources                                                                                                                                                  |

**Every new write endpoint** must have an explicit regression case using the
pattern in `tests/test_request_transactions.py`: write through HTTP, observe
success-header delivery, read the expected mutation on a **separate engine/DB
connection**, assert a different `pg_backend_pid()` and assert durable visibility.
Cover create/update/archive, skills replacement, quiz definition, attempt start,
answer autosave and submission. For deletion/archive assert the durable removed/
archived state, not merely a returned ID. Extend the same coverage to modified
assignment definition/submission/upload/grading and publishing paths. Assert
accepted answer content for autosave, not just that an old attempt row exists.
Keep the failing-commit regression: standard envelope, no state/outbox rows and
neither hook fired. Endpoint coverage is enumerated; no representative-only claim.

Add focused UI tests for autosave scheduling/serialization, timer resync and
expiry, reveal modes/timing, rubric totals, late displays, If-Match/409 recovery and
360px overflow. Add Playwright `phase-3.spec.ts`:

1. Owner instructor creates a skill-tagged bank with all question types, builds
   a two-attempt quiz and an assignment with rubric, late policy and an
   instructions image, then publishes, grants and assigns to one batch.
2. Student fails the quiz, sees permitted results, starts a second attempt and
   passes. Verify no lesson completion after failure and completion after passing.
3. Student submits after the due time and sees the server's late/penalty preview
   and accepted lateness. Instructor sees history and grades the active attempt
   by criterion. Student sees earned marks, penalty, final score and 100% progress.
4. Another batch's student sees no course/attempt/work/grade; deep links return 404. Repeat with desktop and 360px viewports for both instructor and student.
5. Separate focused flow proves real Postgres autosave/resume and server expiry
   with a short test quiz. Use legitimate isolated setup and server time; do not
   add production test-only endpoints or weaken an existing assertion/timeout.

## 12. Numbered build steps and stop gates

Approval of this plan authorizes **step 1 only initially**. Each later step starts
only after the user's "continue". Tests and documentation ship with each step.

1. **Assessment data and security foundation.** Add the module, migrations,
   banks/questions/tags/keys and immutable quiz/attempt/answer models, tenant
   constraints/indexes, narrowly granted DB helpers and public/active/result
   schema separation. Add raw-SQL and independent schema secrecy tests and the
   sensitive-learning-event outbox SELECT repair. Keep existing published
   placeholders and assignment work untouched.
2. **Question/quiz authoring and publishing.** Bank/question/skill and lesson
   quiz endpoints, revisions, central content-source callbacks, safe course
   metadata and immutable question/key publication. Required-by-default quiz
   handling, publish blockers and structural diff in preview/publish, including
   private grading-rule comparison. Tests for ownership, snapshots, minor/major
   rules and visibility for every new mutation; require the course revision on
   publishing under D5 and update existing callers without weakening assertions.
3. **Quiz execution, results and completion.** Attempt start/history, quotas,
   DB-authoritative timer, 10-second Postgres-only autosave and resume,
   submit/scoring/reveal, expiry jobs and restart safety. Add batched enrollment
   completion service hooks, passing rule, major-upgrade behavior, quiz event
   schema/topic registration and all runtime/RLS/transaction regressions.
4. **Full assignments backend.** Migration/backfill/drop single-grade
   uniqueness; immutable attempts/grade revisions; rubric/late math; images;
   additive compatible schemas, header contract and history APIs. Adapt
   existing services/repositories and progress RLS helpers, emit grade v2 and
   make event-schema tests version-aware. All existing assignment flows stay
   covered; add migration, history, concurrency, late, rubric and durability tests.
5. **UI foundation and design system.** Deliver `docs/design/ui-guidelines.md`
   and tokens, light/dark themes, shared shells and state/form/table components.
   Retrofit every existing screen, retaining existing flows and adding the approved
   role landing summaries, cross-course grading and state-derived first-run
   checklists in section 9. Read summaries use module service interfaces, bounded
   cursor lists and existing visibility rules; every new endpoint gets MATRIX
   and access-control coverage. Add the beat-schedule interval regression. Add shell visual
   smoke at 360px/1280px, token/contrast checks and axe scans on every route with
   zero serious/critical violations. **Before coding, present written mockups per
   role**: first landing content, two or three frequent tasks and taps required; wait for
   the user's approval of that walkthrough. Section 9 defines the deliverables.
6. **Instructor UI.** Bank editor, course quiz builder, publish diff, extended
   assignment editor with images/rubric/late policy, active-attempt grading and
   history. Build on step 5 components/guidelines; list applied guideline
   sections and new shared components in the summary. Vitest and instructor
   Playwright coverage, desktop and 360px.
7. **Student UI.** Quiz start/resume/attempt/timer/autosave/results in the
   enrollment player; late-policy preview, rubric grade/penalty/history and
   progress refresh for assignments. Tests for save conflicts, reveal, timer,
   phone layout and the full fail/pass/late-submit/grade journey. Build on step 5
   components/guidelines; list applied guideline sections and new shared
   components in the summary.
8. **Demo seed and documentation.** Add Python Foundations quiz, varied attempts
   and rubric/late examples through services; guarded old-demo major upgrade,
   idempotence/reset tests, README Demo updates and read-only four-role smoke.
   Run `make seed-demo` and the approved guarded upgrade when needed; verify the
   normal rerun and existing credentials remain stable.
9. **Integrated acceptance and close-out.** Finish/verify every listed security,
   transaction, versioning and E2E case, including another batch and 360px; run
   the full Playwright suite with **0 failures**. Final endpoint/table inventory,
   completed-step commit/CI table, full deviation list and carried follow-ups;
   Apply the user's approved cheapest safe student bundle reductions: named
   Zod imports or Zod Mini, and toast loading on first use with durable queued
   messages. Measure every student route before/after, tighten fixed CI budgets,
   account for every shared module grouped by package and carry the remaining
   reduction plan/estimates toward 200 KiB as a follow-up. Mark complete with
   date only after all gates have passed. Push/check green CI, tag v0.3.0 on
   the final close-out commit, push/verify the remote tag, report and stop.

**Every step, including UI and close-out, ends with all of these:**

1. Record any deviation here as it is made; resolve new product/binding-rule
   changes with the user before implementing them.
2. Add/update every endpoint's MATRIX row and `docs/access-control.md` entry.
   For a step adding none, explicitly report that fact after checking coverage.
3. Run `make gen-api`; never hand-edit `apps/web/src/lib/api/schema.ts`.
4. Run **`make lint`** (both apps' lint, format and type-checks) and **`make test`**
   (all API, Vitest and Playwright tests), against the required running services.
   Fix failures without weakening assertions. A check not run is not a pass.
5. Review the diff and secrets exclusions, commit, **push**, resolve the full
   commit SHA, then run **`python scripts/ci_status.py <full-sha>`**. The checker
   must exit 0 for the Actions run with successful required jobs. Inspect/fix a
   red run and rerun local gates for any resulting change before pushing again.
6. Summary: exact changes, deviations, **checks I ran myself and their actual
   results**, full HEAD SHA, push result and **green CI run URL**. Local-only
   success does not complete a step. On this Windows checkout use the documented
   host-web fallback if necessary and state where Playwright ran.
7. **Stop and wait for "continue".** Do not start the next build step automatically.

## 13. Approved decisions (2026-10-04)

The user approved the plan with the following changes; these are binding for implementation.

| ID  | Approved decision                                                                                                                                                                                                                                                                  |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D1  | Absolute pass marks; fraction-minus-wrong-fraction multi-select scoring; normalized blank alternatives, case-insensitive by default with an author-controlled case flag                                                                                                            |
| D2  | Postgres-only autosave every 10 seconds and Postgres resume; no answer Redis cache or revision mirroring. Keep expiry sweeper and separate-connection visibility tests; consider a Redis read cache only after measured need                                                       |
| D3  | Per-quiz reveal mode and `reveal_timing: immediately / after_attempts_exhausted`; default score_only / immediately; DB reveal function and result schemas independently enforce both                                                                                               |
| D4  | Penalty on earned marks per begun UTC 24-hour period, capped at 100%, half-up to two decimals                                                                                                                                                                                      |
| D5  | Additive nullable/optional assignment fields, no schema_version parameter or versioned response unions; separate cursor history endpoints. Require If-Match on submission-upload and publish, update first-party callers. Keep assignment_graded v2 and version-aware schema tests |
| D6  | Question keys/types/option identity and required quiz/rubric structural fields block minor releases; late-policy mode/rate are minor-safe because they freeze per submission and affect only future work                                                                           |
| D7  | Fresh full demo; guarded --upgrade-course for existing demo, major publish and whole-CSE-batch enrollment upgrade                                                                                                                                                                  |

The outbox SELECT security repair is explicitly approved for step 1, including
historical assignment_graded v1 rows, and receives its own repair record.
The new UI foundation is step 5. Its written role walkthrough and the additions
in section 9 were approved on 2026-10-06 before coding; steps 6 and 7 use its guidelines/components.

## 14. Implementation record

All nine implementation steps completed with green pushed CI. After each green pushed step, report its full SHA and
CI URL in the step summary; carry known commit/run records into this table in
the next plan update. Do not invent a self-referential commit SHA or a CI URL
before the commit/run exists. The close-out summary records its own final run.

| Step | Status   | Commit                                     | CI run                                                                                   |
| ---- | -------- | ------------------------------------------ | ---------------------------------------------------------------------------------------- |
| 1    | Complete | `99b59e7b3e8d284fafce9fb40ef9533d20b503a9` | [37220970826](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37220970826) |
| 2    | Complete | `d660c7330e2f520a043cd58745aac4452576816e` | [37231238285](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37231238285) |
| 3    | Complete | `c9aac0b02a9964ddee43c5ba5304d027fa7c7a0e` | [37355212064](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37355212064) |
| 4    | Complete | `c34ddc9b265b2865e5ade2a8eb1c89b8f7eb5cf0` | [37494612624](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37494612624) |
| 5    | Complete | `c17c0f02ef413ca6979ab92395b58897f552f3a8` | [37517763653](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37517763653) |
| 6    | Complete | c23fd8e71dd0300c6eb2133b0b7e30a1a2d761fe   | [37787315118](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37787315118) |
| 7    | Complete | `a79fc494469e0e58f4c44fc38229f6a578f98f28` | [37811946835](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37811946835) |
| 8    | Complete | `4da76dfa07e3d4bbca8791a9621ca0b9b11ab223` | [37963596236](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37963596236) |
| 9    | Complete | `7ac8fd49c42468a4a355edb5953ca22d747685f5` | [37977673085](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37977673085) |

### Deviations

D1–D7 are approved as recorded above. Record implementation deviations as they occur.

Step 1: none outside the approved decisions and security repair.

Step 2: no product or architecture deviations. Existing placeholder-only builder
and progress test setup now uses `lab`, preserving its assertions and timeouts:
quiz is required by default and an unsaved definition must block publication.
The step adds explicit raw-SQL draft joins and payload assertions, plus an
upgrade regression that inserts a real v1 grade event on 0011, reproduces its
cross-student leak and proves 0012 hides it while preserving the owner's access.
The CourseApi revision helper now includes publishing, retaining the foreign-org
404 assertion with a valid conditional header. The MATRIX static builder now
returns a fresh request dictionary so its role
loop cannot consume another role's If-Match header. No assertion is weakened.
No answer Redis cache or response version parameter has been introduced.

Step 2 also adds migration 0013 for lower(name)/lower(prompt) trigram GIN
search indexes and the bank/archive/id cursor index; the pg_trgm extension is
already installed by 0002. This implements the binding indexing rule for the
new authoring filters and introduces no new table, event or product behavior.

Publication supplies a database timestamp to immutable quiz-version INSERTs:
the STABLE readability helper cannot see the new row during implicit INSERT
RETURNING. This avoids that lookup without broadening SELECT policies. Batched
content-source hooks resolve and copy question/key data within assessments;
the course is locked before the referenced banks. Manual sets are bounded at
100 questions, draws at 100 and each eligible frozen pool at 1,000; author lists
remain cursor endpoints. Larger eligible pools require narrower skill filters.

Step 3: no product or architecture deviations. Migration 0014 supplies guarded
runtime mutation functions while keeping direct app-role attempt/answer writes
revoked. Its frozen SQL lives in a migration-owned file; full Unicode casefold
and NFC normalization preserve the approved blank-answer rules. Answers and
resume remain Postgres-only. The existing pure progress test now requires the
quiz passing rule and retains the lab placeholder rule.

Verification repairs: ordinary course-assignment SELECT policies hide assignment
rows from students, so checking batch entitlement through that repository could
incorrectly suppress completion after a passing quiz. A narrowly scoped,
course-owned boolean SQL interface now checks live membership/batch assignment;
enrollments calls it through the courses service. Progress recalculation locks
enrollments before writing lesson progress and skips already-completed evidence:
Postgres checks INSERT RLS before resolving an ON CONFLICT, so re-inserting a
completed quiz from the assignment grader would violate its assignment-only
write scope. The concurrent quiz/assignment test requires both writes to succeed
and progress to reach 100%, without broader grader grants. New test setup uses
the existing assignment-removal route and question-archive 204 contract; no
assertion or timeout in existing tests was weakened.

Final review reproduced an old-major evidence bug: a revoked passing attempt
sealed without completing its lesson could create 100% progress during a later
major opt-in. Completion hooks now receive the displayed major through service
interfaces, and assessments filters passing evidence to that major. Existing
completed stable lesson IDs still carry forward; historical scores alone cannot
create new completion. The regression first failed with 100 instead of 0, then
retained the zero-progress assertion for the repair. The initial full suite was
stopped for this repair; the final gate reran all tests against the corrected code.

Local browser verification initially failed the existing catalog visibility
assertion in both projects: `/catalog` returned 500 with `DYNAMIC_SERVER_USAGE`.
The host standalone production launch incorrectly retained the development-only
`CATALOG_DATA_CACHE=off` setting, switching a prerendered route to `no-store` at
runtime. Correcting the launch to production/CI's default tagged cache resolves
that configuration mismatch. Catalog code, assertions and timeouts are unchanged;
the full local gate passed with the corrected production launch.

Step 4: no product/architecture deviation outside the approved changes. Before beginning the backend,
added `test_passing_expiry_sweeper_without_client_survives_restart`: the production
sweep function uses fresh app-role connections, is rerun after connection/worker
recreation and expiry-task replay, and reads Postgres before any client request.
It requires the same passing score/revision/submitted time, one completed lesson,
one completion event and one quiz event. This fills the passing-completion gap in
Step 3's existing zero-score sweeper regression; no existing assertion or timeout
was weakened. Both this regression and the existing late-PUT lock-boundary case
passed locally before Step 4 began. These tests invoke the beat task's production
sweep implementation; they do not claim a live-clock/broker test was run.

Compatibility test changes implement approved contracts: upload setup/callers
send If-Match; the unknown instruction image remains 422 with invalid_image;
the exact grade-response assertion includes all original fields and every new
field. Role assertions/timeouts remain unchanged. Historical migration setup
uses frozen pre-0012 SQL rather than today's ORM columns, preserving the actual
v1 payload leakage reproduction and all preservation/isolation assertions.

Step 4 verification repairs: add composite FK indexes required by the unchanged
schema-wide guard; make the event topic-table parser tolerate Markdown formatter
spacing while retaining its exact topic equality and payload/version assertions;
refresh the returned published-snapshot fixture after its database update;
allow SQL NULL/JSON null for an absent rubric breakdown; and drop the dependent
grade INSERT policy before its columns during downgrade. Audit captures the
definition before mutating its ORM identity. The legacy upgrade regression now
checks both text and file work, retained files, grades and completed progress.
The local Keycloak setup-email test also needed the existing Mailpit service
started. These repairs do not weaken assertions, change timeouts or deviate from
the approved product contracts.

Step 5 approved scope addition: the user explicitly approved functional landing
summaries, the cross-course grading queue and state-derived first-run checklists
alongside the visual retrofit. Implemented through existing module services;
no assessment authoring/attempt UI from steps 6/7 starts here. System-default
manual themes and a semantic-token-only guard replace the original hex-only
guard. The beat-schedule registration regression is explicitly approved.

Step 6: no product or architecture deviation. Additive author-only manual-question
summaries avoid per-question hydration requests. Repairs keep toast contrast AA
through transitions, derive rapid selections from current field-array values,
and refresh the visible enrollment list every ten seconds after asynchronous
fan-out. New test setup fixes lesson-ID navigation timing and taxonomy slugs;
the existing route inventory adds the bank detail route. The planned single
`phase-3.spec.ts` is realized as `assessment-authoring.spec.ts` and
`assessment-learning.spec.ts`, retaining the authoring and full learning journey
with desktop/360px coverage. Every original assertion
and timeout remains unchanged. The implementation record below gives the exact
failures, repairs and verification results; shipped in
`c23fd8e71dd0300c6eb2133b0b7e30a1a2d761fe`.

Step 7: no product or architecture deviation. The user additionally requested
before/after student bundle measurements and proof that authoring dependencies
are absent; the production audit also runs in CI. Verification findings and
new-test setup corrections are listed in the Step 7 implementation record.
Existing assertions and timeouts are unchanged.

Step 8: no product or architecture deviation. The user additionally requested
the production shared-module breakdown, a reduction plan toward 200 KiB and
current-baseline regression budgets. These ship as analysis/CI tooling only;
no application bundle reduction or dependency change is made in this step.
Seed repairs: await registered async cache callbacks after the CLI transaction
commits, preserve the student's existing resume pointer on reruns, and remove
only demo-scoped video buffers/dirty references on reset. Seed test scenarios now
use independent course slugs like the repository's factory conventions, so fresh
and legacy definitions cannot accidentally reuse one another. The approved demo
percentage assertions change from six to seven required lessons (83% → 85% for
Priya); new quiz assertions are stronger. Existing timeouts are unchanged. The
new smoke opens the existing native mobile outline and waits/scopes its quiz
link there; initial selector failures are verification repairs, not weakened
assertions or application behavior changes.
The first full API run exposed two new fixture leaks: a synthetic unrelated
Redis dirty marker and an extra batch in the canonical demo organization.
The new test now tears down only its own markers even on failure and uses
the existing canonical ECE batch. Existing video and identity seed assertions
and all timeouts remain unchanged; the full suite is rerun after these repairs.

Step 9: no product or architecture deviation. The user approved named/Mini Zod
imports and first-use toast loading, exact post-reduction CI ceilings and full
shared package attribution. Shared error-envelope validation uses Mini; deferred
quiz schemas retain their classic named imports and all existing rules. The toast
adapter covers all role callers because the host is global. New tests preserve
first-use delivery and accessible import-failure recovery. An existing test mock
now targets that adapter; its notification assertion and timeout are unchanged.
Verification repaired a missed `.ts` toast caller and import formatting. The
initial full `make test` passed all 1,723 API cases but the new real-render toast
test's wait included dynamic-import setup under the full web runner. The test now
explicitly settles its first-use import inside React `act` before the unchanged
visibility assertion; no prefetch, assertion or timeout change. All 244 web tests
then passed. The complete lint/test aggregate passed after that setup repair.

Bundle-analysis repair: an async chunk common to every route is still deferred;
shared attribution follows synchronous module edges and reconciles shared file
counts with actual production output. Repeated library source paths cannot promote
an unreachable async entry's chunk. Player-only SSR preload files are excluded
from shared membership. All modules and package groups are retained in the
attribution, with estimates explicitly separated from measured transfer. No
existing application/test assertion or timeout was weakened.
Step 9's application, test and analyzer repairs ship in
`7ac8fd49c42468a4a355edb5953ca22d747685f5`; its green CI is recorded in the table.

### Security repairs

- Step 1: approved sensitive-learning-event outbox SELECT repair, including existing
  `assignment_graded` v1 rows. Shipped in `99b59e7b3e8d284fafce9fb40ef9533d20b503a9`,
  [green CI 37220970826](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37220970826).

### Step 1 implementation

- Migration `0012` adds the ten assessment tables listed in section 3, with
  indexed tenant/FK columns, composite tenant constraints, attempt-number and
  one-active-attempt uniqueness, immutable publications and per-operation RLS.
- Six hardened SQL helpers enforce enrollment ownership, publication/attempt/
  selected-question access and reveal mode/timing. Private keys remain unreadable
  by students through ordinary SQL even after submission. Direct app-role writes
  to attempts/answers are revoked until step 3 introduces guarded mutation functions.
- Typed active/result projections and independent response tests enforce secrecy
  without relying on RLS. The outbox security repair above retains legitimate
  student INSERT/RETURNING and the relay's grants/policies.
- No HTTP endpoints added: the existing MATRIX remains complete. `make gen-api`
  ran and generated files are unchanged. Existing placeholders, assignment
  aggregates and session transaction handling retain their current behavior.
- Checks run personally: focused security/schema/transaction tests
  **62 passed**; `make gen-api` and `make lint` **passed**; `make migrate`
  **passed** against the local compose database; full `make test` **passed**:
  API **1,314 passed**, Vitest **157 passed**, Playwright **41 passed / 7 existing
  intentional skips / 0 failures**, using the running host production web server
  against compose services. The exact pushed-commit CI URL follows in the step
  summary and is carried into this record at the next update; local gates alone
  do not complete this step. No implementation deviation outside the approved
  changes was needed.

### Step 2 implementation

- Added 14 endpoint methods for cursor-paginated bank/question authoring,
  skill tagging, course quiz definitions and safe published-question preview.
  Each has a MATRIX row and an access-control entry. All nine write methods,
  including course publication, have separate-connection visibility tests and
  missing/stale If-Match rollback tests. Bank/question writes use bank revisions;
  quiz edits and publication use course revisions. First-party publish callers
  now send the required header.
- Quiz lessons default to required and store exactly `{quiz_id}`. Publication
  blocks unfinished definitions, locks the course before its referenced banks,
  and freezes immutable prompts, options, marks, tags and private grading keys.
  Public preview/snapshots/audits exclude keys and explanations. Structural and
  private grading changes appear in publish preview and require a major release;
  cosmetic edits remain minor-safe. Modules interact through service hooks.
- Migration `0013` adds the authoring filter/search indexes recorded above.
  Security coverage now explicitly tests draft-key joins and an actual historical
  v1 outbox row inserted before the security migration. The tests exercise raw
  SQL with the app role independently of HTTP and response schemas.
- Checks run personally: `make gen-api`, `make lint` (lint, format and strict
  type-checks), `make migrate`, `uv run alembic check` and the production web
  build **passed**. Final full `make test` **passed**: API **1,465 passed**,
  Vitest **157 passed**, Playwright **41 passed / 7 existing intentional skips /
  0 failures**. Playwright used the host production web server against the real
  compose services. Focused MATRIX coverage and route completeness also passed.
- Verification repairs: placeholder fixtures use `lab`, and conditional-header
  helpers retain the original assertions, as recorded under Deviations. An
  earlier Playwright run encountered login rate limiting because the host launch
  omitted the existing configured auth limit; exporting that setting restored
  the intended local configuration. No application rate limit, assertion or
  timeout was changed for this issue.
- Local gates do not complete the step. The exact commit, push result and green
  CI URL are reported in the step summary and carried into the table at the next
  authorized plan update. No later step has started.

### Step 3 implementation

- Added seven endpoint methods for student quiz rules, cursor attempt history,
  start, durable resume/autosave, submit and results. Each has a MATRIX row and
  access-control entry. All three write methods require If-Match and have
  separate-connection visibility checks at success-header delivery, missing/
  stale revision checks and real deferred-constraint commit-failure rollback
  checks, including dropped expiry hooks.
- Migration 0014 adds narrowly granted runtime/normalization/entitlement SQL
  functions, with no new tables. It validates an entire answer batch before
  writing, reads the database clock after enrollment/attempt locks, freezes
  selected questions/order and deadlines once, serializes quotas and rejects
  post-deadline answer changes. Scoring uses exact decimal half-up arithmetic;
  keys stay inside trusted grading, and frozen reveal mode/timing are enforced
  independently by the DB and response schemas.
- Expiry starts only after a successful commit, reschedules to the original
  database deadline and is recovered by a bounded five-second sweeper. Locked
  work is skipped and retried. Finalization is idempotent; accepted answers,
  score, progress and the registered `quiz_attempt_submitted` v1 enrollment-topic
  event commit together. Revoked attempts can be sealed without restoring access
  or completing the enrollment.
- Centrally wired assessments/assignments completion interfaces supply batched
  evidence to enrollments. Quiz evidence is scoped to the displayed major; an old
  score cannot create a new completion. A passing quiz completes its lesson; manual completion
  is rejected. Minor updates preserve active frozen rules; major opt-in abandons
  old active work and resets the attempt budget, retaining stable completed lesson
  IDs. Query-count measurements confirm that increasing an autosave from one
  answer to three adds no SQL round trips; validation/grading and answer writes
  are batched. No Redis answer layer was added.
- Checks run personally: `make gen-api`, `make lint` (lint, format and strict
  type-checks), `make migrate`, `uv run alembic check`, production web build and
  quiet Compose configuration validation **passed**. Final full `make test`
  **passed**: API **1,558 passed**, Vitest **157 passed**, Playwright **41 passed /
  7 existing intentional skips / 0 failures**. Playwright used the host production
  web server against real Compose services. The unchanged catalog spec also
  passed separately in both projects after the launch correction.
- Local gates do not complete the step. The exact commit, push result and green
  CI URL are reported in the step summary and carried into the table at the next
  authorized plan update. No later step has started.

### Step 4 implementation

- Migration 0015 extends the existing module with immutable submission attempts
  and append-only grade revisions. Each surviving Phase 2.5 submission backfills
  exactly one attempt; its IDs, work, timestamps, revision and existing grade
  survive. The aggregate retains its stable ID and projects one active attempt.
  Deferred composite constraints and app-role SQL guards prevent forged frozen
  rules, mismatched projections, invalid grading and edits to historical rows.
- Assignment definitions gain nullable rubric and late-policy fields. Every
  accepted submission freezes its published definition, displayed major and
  database-clock lateness. Reject closes late submissions; penalty uses earned
  marks per begun UTC day, caps at 100% and rounds half-up to two decimals.
  Regrading appends a revision and computes the penalty from earned marks once.
  Rubric criterion IDs/maxima remain structural; late mode/rate are minor-safe.
- Instructions use the existing notes document/image validation and rendering
  flow. Publishing freezes referenced image IDs. A narrowly scoped SQL media
  interface allows authorized historical instructions without granting broad
  access to old course versions or another student's work.
- Six GET methods add sanitized author preview and separate cursor-paginated
  submission/grade histories. All have MATRIX and access-control entries.
  Existing response fields and URLs remain; new fields are additive, with no
  schema-version parameter. Submission-upload now requires If-Match and its
  existing first-party caller sends the submission revision.
- Definition writes lock the course before publishing; submission/grading lock
  the enrollment before the aggregate. Completion evidence uses only the active
  grade for the displayed major through the assignments service. Historical
  scores cannot create new-major completion; existing completed stable lesson
  IDs retain their established carryover behavior.
- `assignment_graded` v2 includes the frozen rubric/late breakdown on the existing
  enrollment topic. Strict schema tests validate both the current producer and
  historical v1 payloads independently. Five modified writes (definition,
  submission, upload, grading and publication) each have separate-connection
  visibility-at-headers, missing/stale If-Match and failed-commit rollback tests.
- Checks run personally (2026-10-06): `make gen-api`, `make lint` (both apps'
  lint, format and strict type-checks), `make migrate`, `uv run alembic check`,
  production web build and repository hooks **passed**. The final full
  `make test` **passed**: API **1,651 passed**, Vitest **157 passed**, Playwright
  **41 passed / 7 existing intentional skips / 0 failures**, against the host
  production web server and real Compose services. The assignment UI journey
  and all four read-only demo role cases ran and passed; desktop duplicates
  retain their intentional skip conditions. No assertion or timeout weakened.
- The initial full API gate had 1,646 passed / 4 failed (two topic-table parsing
  cases, the missing Mailpit service, and the composite FK-index guard).
  The focused repaired gate passed all 10 cases, and the final full gate above
  reran the entire suite. Fresh migration tests and the schema-wide guards
  verify all four indexes; the already-migrated local dev database received the
  same index additions without changing data. Hooks needed Git Bash on PATH,
  as documented for this Windows checkout, after normalizing line endings.
- A step is complete only after its local gates and exact pushed-commit CI pass.
  The summary records that commit and run URL, and the next authorized plan
  update carries them into the table. Step 4 pushed commit `c34ddc9b265b2865e5ade2a8eb1c89b8f7eb5cf0` passed all
  required CI jobs: [37494612624](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37494612624).

### Step 5 implementation (2026-10-07)

- Updated section 9 with the approved role walkthrough and additions before
  coding. Delivered `docs/design/ui-guidelines.md`, sections 1-8: semantic
  tokens/themes; type/spacing/elevation; shells/navigation; landings/first run;
  loading/empty/error/save states; forms/tables/progress; accessibility;
  extension rules for the following UI steps.
- Shared set: RoleFrame/Breadcrumbs, ThemeControl, PageSkeleton, StatusBadge,
  FirstRunChecklist, FormField and LiveAnnouncement, DataTable. Existing
  PageTitle, EmptyState, ErrorAlert, LoadMore and ConfirmButton now live in
  the shared patterns directory; existing imports retain their public names.
  Every role and existing public/auth/catalog screen uses the shared tokens
  and controls. Progress has sticky desktop headers/name column and labelled
  mobile cards. Common validation forms preserve labels, validation and
  conditional-write behavior, with linked help/errors and saving states.
- Added the approved real admin/instructor/student landing panels and the
  cross-course grading page. Question banks remains a Step 6 placeholder.
  Checklists derive from saved state and disappear when complete. Dashboard
  queries refresh on remount, including return from saving a grade. Quiz and
  assignment authoring/attempt interfaces from Steps 6/7 remain unstarted.
- Seven read-only endpoints are documented in `docs/access-control.md` and
  MATRIX. Lists have bounded cursors and batched queries. Reports remains
  tableless and composes identity/courses/enrollments/assignments/assessments
  service interfaces. Owning modules expose their own dashboard read helpers
  through service.py. No new write endpoint or transaction path was added.
- Migration 0016 adds four filter/cursor indexes and an assessment-owned,
  fixed-search-path aggregate SQL interface for current-org, live-entitled
  seven-day quiz pass/fail counts. Existing staff attempt/answer visibility
  is preserved; keys and other-org work gain no grants. Raw app-role SQL and
  safe score-only HTTP response checks are independent. Dashboard regressions
  cover own-org queue ordering, pagination, invalid cursors, displayed majors,
  immediate batch revocation, latest active grades, real-state checklists,
  no-enrollment vs zero completion, and constant query count as batches grow.
  Recent quiz labels come from the assessment-owned frozen version; the result
  projection still excludes questions, keys, answers and explanations.
- Added `tests/test_beat_configuration.py`: fresh subprocesses import the
  actual Celery app/task registrations and verify exactly one configured beat
  entry for `assessments.sweep_expired`, at both 5.0 and 17.5 seconds. This
  closes the configuration-level registration/interval caveat, without
  claiming a live broker/clock test.
- Semantic guard rejects raw Tailwind palettes, hex and numeric CSS colour
  literals outside globals.css, including generated notes CSS. Pygments
  generation maps existing classes to semantic code tokens. Contrast checks
  cover both themes, status/code/video and opaque primary-hover pairs, plus
  focus/input contrast. Screenshots assert only shells at 360/1280 in both
  themes; the accessibility inventory enumerates every page.tsx route and
  exercises details, editors, redirects, chooser and auth result states.
  Axe runs all rules, with zero serious/critical violations required; no
  exclusion, assertion reduction or timeout increase is used.
- Verification repairs: invitations use a PostgreSQL-supported bound ANY
  predicate; the video canvas uses its semantic token. New test setup uses
  UUID identity values, the key table's actual question_id, the standard 400
  invalid_cursor envelope and same-origin BFF write headers. One initial new
  test incorrectly assumed staff had no own-org attempt SELECT; its exact raw
  result assertion now matches the existing documented RLS, while key denial
  remains explicit. No existing security assertion was changed.
- Browser repairs: the 44px controls crowded a placeholder lesson row to
  373px; module/lesson action groups now wrap on phones, retaining the original
  <=360 assertion in teach.spec.ts. The existing admin-courses spec selects
  the approved mobile cards for the same visible Welcome label and the same
  student's 0%, and also runs its original desktop table assertions. The
  platform spec opens More before selecting Audit on a phone. These are
  approved navigation/selector adaptations; original assertions and timeouts
  remain. Badge/button colours switch together rather than interpolating
  through an inaccessible contrast pair; primary hover is an opaque token.
- New browser readiness waits for layout loading states and fonts instead of
  network idle: video playback keeps network traffic active. Existing helper
  assertions/timeouts are untouched. The local production launch initially
  failed because a PowerShell .env reader left KEYCLOAK_PORT interpolation
  unresolved; python-dotenv supplies the same expanded settings as Compose.
  Corrected runtime configuration is local-only and no secrets are tracked.
- Initial full gate: API 1,713 passed, Vitest 165 passed; Playwright 49 passed,
  7 existing intentional skips, 6 failures (mobile-card selector, More
  navigation, two outline-width cases, two theme-transition contrast cases).
- Checks run personally: final `make lint` (lint, format and strict type-checks)
  and final full `make test` **passed**: API **1,713 passed**, Vitest **165
  passed**, Playwright **55 passed / 7 existing intentional skips / 0 failures**.
  The run includes all 30 page routes with zero serious/critical axe violations
  and all 16 shell baselines at 360/1280 in light/dark themes. `make gen-api`,
  `make migrate`, `uv run alembic check` and the production web build **passed**.
  Migration 0016 was also downgraded/reapplied locally without data changes.
  Playwright used the host production server against real Compose services.
- Local gates alone do not complete the step. The exact commit, push result
  and CI run URL are reported in the step summary and carried into the table
  at the next authorized plan update. No later step has started. There is no
  product or architecture deviation outside the approved scope additions;
  verification repairs and selector/navigation adaptations are listed above.

### Step 6 implementation (2026-10-07 to 2026-10-08)

- The user explicitly skipped the unspecified Step 5 UI follow-up and authorized
  Step 6. Step 5 is complete at `c17c0f02ef413ca6979ab92395b58897f552f3a8`,
  [green CI 37517763653](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37517763653).
- Reused the existing assessment/assignment endpoints and generated contracts.
  Delivered the bank/question editor and lesson quiz builder, then extended assignment
  instructions/rubric/late policy and active-attempt grading/history. Apply the
  shared guidelines and components; added Vitest and desktop/360px Playwright
  coverage, including accessibility and every new page route.
- Bank edits serialize with bank revisions; definition edits retain course
  revisions. Preserve entered work on failed/conflicting saves. Historical
  attempts are read-only. Grading sends the active attempt and submission
  revision, uses earned marks for regrades and previews the frozen penalty
  with the same exact decimal half-up rule as the backend.
- Applied UI guidelines sections 1-8: semantic tokens/themes; responsive type
  and spacing; existing role shell/navigation; existing Needs attention/grading
  landing; shared loading/empty/error/save states; field/cursor/confirmation
  patterns; keyboard/touch/focus/axe/mobile baseline; reusable shared patterns.
  New shared components: PublishedContent, LateStatus and GradeBreakdown,
  documented in the UI guidelines for reuse in Step 7. Existing NotesEditor
  now reports failed saves inline and supplies live document changes; the
  shared SkillsPicker has unique IDs when multiple pickers appear together,
  and LoadMore explicitly uses button semantics inside authoring forms.
- No new endpoint was added. Existing MATRIX rows cover bank/question edits,
  definition saves and grading/history; the all-routes-in-MATRIX assertion is
  included in the full suite. Updated the draft quiz access-control entries
  and regenerated OpenAPI-derived web types with make gen-api.
- Small API support addition: the existing author-only draft quiz GET/PUT
  response gains a bounded manual-question summary (id, bank, prompt, type,
  archived flag). One batched assessment-owned SELECT hydrates saved selections
  without one HTTP request per question. It returns no key or explanation,
  changes no permission or write contract and adds no endpoint.
- Verification repair (2026-10-08): the new instructor axe scan caught a
  partially transparent success toast at 3.85:1 while Sonner faded it out
  and a stacked toast title at 1.56:1 during its content fade.
  Shared toast/child CSS now transitions movement/height/elevation only; visibility
  changes immediately, preserving semantic status colours and AA contrast.
  The axe assertion remains zero serious/critical violations. No existing
  assertion or timeout was weakened. The existing route inventory and its
  setup only gained the new bank detail page and a real bank to scan.
- Verification repair: rapid quiz selections now derive checkbox membership
  from the field array rather than the broader value watch, and mutations
  consult the current form selection to avoid duplicate/stale changes. The
  new browser flow still requires all three choices to become checked and
  the selected marks fields and persisted definition to match.
- New browser setup waits for each lesson navigation before capturing IDs,
  uses a valid underscore-only taxonomy slug, and respects replacement only
  before grading. These fix the new test's setup; existing assertions/timeouts
  are unchanged and no product rule changed.
- Local verification resumed after an interruption with Docker Desktop stopped.
  Restarted Docker/Compose, kept the documented production host-web fallback,
  and pointed the local worker at that host for catalog revalidation. These are
  local environment repairs; no deployed server configuration changed.
- Verification repair: the first full gate passed 1,714 API and 197 Vitest
  tests, but Playwright had 56 passed, 7 existing intentional skips and one
  failure in learn.spec.ts:131, the unchanged 30-second course-card visibility
  assertion. Trace reads began before the fan-out worker's transaction committed;
  later cursor pages cannot include a newly inserted newest row. The dashboard
  did not refresh while open. Its existing enrollment query now refreshes every
  10 seconds while visible, with no background polling. A new unit regression
  starts with a nonempty paged read, then verifies discovery from a fresh first
  page after the worker commit and no polling after unsubscribe. No existing
  test, assertion or timeout changed; this is an app freshness repair, not
  Step 7 assessment UI.
- Checks run personally: make lint passed (API Ruff/format/mypy and web
  ESLint/Prettier/strict TypeScript); make gen-api and the final production web
  build passed. The final full make test passed against real Compose services:
  API 1,714 passed, Vitest 198 passed, Playwright 57 passed / 7 existing
  intentional skips / 0 failures. Both instructor flows at 360px/1280px,
  all 31 renderable page routes with zero serious/critical axe violations,
  and all 16 shell baselines in light/dark themes passed. Existing MATRIX
  coverage passed; no new endpoint needs a new row.
- Local gates alone do not complete Step 6. Its exact full SHA, push result
  and CI run URL are reported in the step summary and carried into the table
  at the next authorized plan update. No later step has started.

### Step 7 implementation (2026-10-08)

- Authorized by the user's continue after Step 6. Step 6's exact pushed commit
  and all-six-jobs green run are carried into the table above. No Step 8 work
  is authorized.
- Reuse student quiz/attempt/result and assignment/history endpoints. Deliver
  quiz start/resume, serialized ten-second Postgres autosave, monotonic display
  of the server deadline, expiry submission, reveal-aware results and cursor
  history inside the existing lesson route. Preserve entered answers on network
  failure; stale revisions require explicit refresh rather than overwriting
  another device. Submission and scoring remain server authoritative.
- Extend the existing assignment page with authorized instructions images,
  current late/closed preview, accepted frozen lateness, rubric/penalty grades
  and cursor attempt/grade histories; retain text/file and If-Match contracts.
  Reuse the Step 5/6 shared patterns. Added unit and full desktop/360px journeys,
  including autosave/reload/expiry, fail/pass, late work, grading, 100% progress,
  revoked/other-batch access and axe scans. The focused four browser cases passed
  in both projects, including temporary connection failure, explicit conflict
  refresh, delayed explanations, frozen instructions/images and grade polling.
- Applied UI guidelines sections 1-8: semantic tokens/themes; responsive
  type/spacing; the existing student shell and outline; Due soon/Recent results
  refresh; shared loading/error/save/confirmation states; labelled fields,
  cursor history and numeric progress; keyboard/touch/focus/live announcements
  and axe; feature-only assessment rules. New shared components: none.
  Reused PublishedContent, LateStatus, GradeBreakdown, FormField,
  PageSkeleton, StatusBadge, LiveAnnouncement, ErrorAlert, ConfirmButton and
  LoadMore. Quiz and assignment views use client-side dynamic imports.
- No API endpoint, schema, permission, table or event changed. Existing MATRIX
  and access-control rows cover every method used here. Runtime writes remain
  covered by test_runtime_transactions.py's start/save/submit visibility-at-
  headers cases; assignment submission/upload remain covered by
  assignments/tests/test_transactions.py's separate-connection cases. The
  full API gate runs these and their stale-header/failed-commit counterparts.
- Verification repairs: cached background-rule/status failures now display
  Retry while keeping the active quiz or assignment form mounted and retaining
  unsaved work. New regressions prove both. Large valid answer batches use an
  ordinary request instead of exceeding the bounded unload-request budget;
  Zod validates answer bounds before writes. Serialization tests cover edits
  during a save, returned revisions, forced expiry behind a pending save,
  worker-finalized attempts, monotonic timing and retry backoff.
  The sticky quiz timer clears the existing sticky app header while scrolling;
  both viewport journeys assert it remains below the header and within the screen.
  Results use the server's enrollment completion when a failed retake follows
  an earlier pass; a new UI regression verifies both the failed result and the
  retained completed lesson, with no client progress recomputation.
- New browser setup corrections: the answer selector names the textbox exactly
  because the existing submission-kind label also contains 'your answer';
  question creation uses its actual body (marks belong to quiz selection,
  skills use their separate links). Existing tests/assertions/timeouts were
  not weakened or changed. New deadline tests use a normal 25-second published
  quiz and delay a real PUT past expiry, preserving the 409 and exact score.
- Additional approved evidence: measure every /learn route before/after and
  prove no Tiptap/dnd-kit authoring code in any student bundle. The baseline
  production build is the verified Step 6 build at c23fd8e. The new reproducible
  student-bundles script reads Next 16.3.6 production route statistics and sums
  per-file gzip level 9 (Node), including shared/runtime first-load JS. Next's
  production analyzer graph independently audits every emitted client module,
  including deferred chunks; server/SSR modules are excluded by output namespace.
  Empty/missing audits fail except the two explicit server-only course redirects.
  Baseline bytes and graph counts are in
  docs/design/student-bundles-step7-before.json: /learn 1,196,527 raw /
  341,632 gzip; enrollment/player 1,226,197 / 352,158; video 1,174,487 /
  334,443. No Tiptap, dnd-kit or ProseMirror client module was present.
  The after build reports /learn 1,197,452 raw / 341,751 gzip;
  enrollment/player 1,207,162 / 345,419; video 1,174,487 / 334,442.
  This is 333.6 → 333.7 KiB, 343.9 → 337.3 KiB and 326.6 → 326.6 KiB
  respectively, including shared/runtime first-load JS. Deferred transfers
  are excluded from first-load totals but included in the dependency audit.
  All six route graphs remain free of authoring modules, including the quiz
  and assignment entry/engine/history chunks. Exact evidence, methodology
  and reproduction commands are in docs/design/student-bundles-step7.md
  and its before/after JSON files.
- Checks run personally: make gen-api, make lint (both apps' lint, format and
  strict type-checks), the production web build and production bundle audit
  passed. Full make test passed: API 1,714, Vitest 230, Playwright 61 passed /
  7 existing intentional skips / 0 failures, against real Compose services and
  the host production web server. The final retake-completion wording repair
  was made while that browser run was active; afterward make lint, make
  test-web (231 passed), a new production build/bundle audit and the full
  make test-e2e (61 passed / 7 existing intentional skips / 0 failures) passed
  on the final source. No API code changed after its full passing run.
  All 31 renderable page routes have zero serious/critical axe violations;
  all 16 shell baselines at 360px/1280px in light/dark themes passed.
- Local gates alone do not complete Step 7. Its exact full SHA, push result
  and CI URL are reported in the summary and carried into the table at the
  next authorized plan update, following the convention above. Steps 8/9
  remain unstarted.

### Step 8 implementation (2026-10-08 to 2026-10-09)

- Step 7's exact SHA and green Actions run are carried into the table. Step 9
  remains unstarted, including the candidate cheap bundle experiment.
- Fresh Python Foundations 1.0 contains the existing stable six lessons plus
  a seventh required quiz. Author the Python skill, bank, all three question
  types, marks, timer, two-attempt limit and delayed explanation reveal through
  the skills/assessments services. Quiz scoring, expiry and completion use the
  normal services; only expiry time preparation is guarded CLI-only SQL.
- Quiz fixtures: Priya 1/6 then 6/6, Aarav 6/6, Ananya 1/6 (multi-select partial
  credit), Rohan expired at 0/6, the other four untouched. Existing video, notes
  and PDF coverage remains. Progress is 85/100/42/28/57/14/0/0 percent for the
  eight students in account order. No lab execution/completion is invented.
- Fresh FizzBuzz definitions freeze the due date once, have a 6+4 rubric and
  10% penalty per started late day. Aarav's raw 9.00 becomes 8.10 after one late
  day; grade breakdown and accepted penalties are written by assignments.
  Existing legacy submissions/grades keep their original frozen rules.
- A normal six-lesson rerun reports `--upgrade-course` and leaves the course
  and work intact. The guarded flag verifies the unedited legacy definition,
  publishes 2.0 through courses and opts the **whole CSE batch** into it through
  the college-admin enrollment service. It preserves original lesson IDs,
  historical grades and passwords. Already upgraded/fresh runs are idempotent;
  a partial upgrade can safely resume the whole-batch operation. Edited legacy
  definitions require manual reconciliation. All risky flags retain nonlocal
  confirmation and absolute production refusal.
- Reset covers quiz answers/attempts, immutable assignment attempts/grades,
  progress and only these demo enrollments' video cache/dirty references.
  A normal run preserves unfinished human answers, original deadlines,
  resubmission history and resume pointers. Password rotation remains separate.
- Extended `test_seed_demo.py` and `test_cli_boundaries.py`: 26 focused cases
  pass against real Postgres/Redis/Keycloak/MinIO. Separate connections verify
  seeded scores/grades/due dates; compare reruns/reset, preserve human work,
  reject edited upgrades, prove CSE members outside `USERS` move and another
  assigned batch stays on major 1, and deny expiry preparation outside the
  guarded context. New test fixture corrections retain exact math/assertions.
  These and the previously affected video/identity seed tests pass together:
  47 targeted tests after fixture cleanup.
- Ran actual `make seed-demo`: detected the old local demo and left it intact.
  Ran the approved `--upgrade-course`, then plain `make seed-demo` twice. Major
  2.0 was published and all 11 existing CSE enrollments moved, including human
  members beyond the eight demo students; the credentials file was byte-for-byte
  unchanged in all three runs. No local reset or password rotation was performed.
- README documents the richer demo, guarded upgrade/reset/rotation and old-work
  behavior. The four-role mobile smoke passes and remains read-only: it also
  reads Priya's failed/passed history and submitted result without starting,
  answering, submitting or grading an assessment.
- No endpoint, response schema, event, migration or module boundary changes.
  Reused endpoint coverage remains in MATRIX/access-control. `make gen-api`
  regenerated unchanged API types; no manual generated-schema edits.
- Student bundle report and proposed savings are in
  [the Step 8 analysis](../design/student-bundles-step8.md). Actual first-load
  transfer remains unchanged from Step 7; module attribution uses analyzer
  level-6 DEFLATE with gzip-wrapper accounting, independently from actual
  whole-file gzip-9 totals. Exclude nomodule-only polyfills and all server output.
  CI now enforces fixed whole-KiB baseline ceilings in the existing audit,
  including the all-deferred-chunks authoring ban; the 200 KiB target is still
  an open follow-up. A fresh production build/analyzer reproduces the exact
  Step 7 transfer figures. Verified the audit passes current ceilings and
  fails on an exceeded ceiling, then restored the fixed budgets unchanged.
  No reduction is implemented in Step 8.
- Local `make gen-api` and `make lint` passed (Ruff, format, mypy, ESLint,
  Prettier, strict TypeScript). The final full `make test` passed against Compose
  backing services and the fresh Windows host production web fallback:
  **1,723 API**, **231 Vitest**, **61 Playwright**, **7 unchanged deliberate
  skips**, **0 failures**. Both-width/theme shell screenshots and route axe
  scans passed, as did the read-only four-role demo. Local gates alone do not complete this step;
  its own full SHA, pushed green CI and URL go in the summary and are carried
  into the table at the next authorized plan update.

### Step 9 acceptance and close-out (2026-10-09 to 2026-10-10)

- Approved bundle reduction implemented without dependency upgrades or backend
  changes. Exact production Windows/Linux figures and complete package/module
  attribution are in [the Step 9 report](../design/student-bundles-step9.md).
  Shared Windows transfer falls from 326.6 to 253.25 KiB. Student first-load totals fall
  from 333.7/337.3/337.3/326.6 to 260.4/264.0/264.0/253.25 KiB on Windows
  (Linux video: 253.3 KiB). The fixed CI
  ceilings use the exact larger measured platform bytes, not rounded budgets.
- The production audit passed the tightened limits and rejects an exceeded
  ceiling. Sonner is absent from initial student chunks; all emitted student
  graphs, including deferred feature chunks, remain free of Tiptap/dnd-kit/
  ProseMirror. The 200 KiB target remains an explicit measured follow-up.
- Shared component added: lazy notification host/adapter, using the existing
  Button for accessible failure recovery. Applied UI guidelines sections 1
  (tokens), 5 (states/notifications), 6 (forms/actions), 7 (accessibility) and
  8 (extending the shared set). Updated section 5 with first-use/queue/retry conventions.
- Integrated evidence checked in the full-suite inventory: raw app-role SQL
  secrecy/joins/outbox tests and historical v1 upgrade in assessments
  `test_rls.py`/`test_security_upgrade.py`; independently privileged response
  secrecy/reveal tests in `test_schemas.py`; publish/structural rule tests;
  runtime quota, partial scoring, timer boundary, no-browser sweep/restart/replay
  and concurrent progress tests; populated assignment-history upgrade, rubric/
  late math and own-org active-attempt grading tests. `test_beat_configuration.py`
  checks the registered interval. Every write contract is enumerated by
  assessments `test_write_visibility.py`/`test_runtime_transactions.py` and
  assignments `test_transactions.py`, asserting separate-connection visibility
  before success headers and rollback/error envelopes on failed commits.
  The two assessment Playwright specs provide the authored three-type quiz,
  rubric/image assignment, fail/pass, late grade/penalty/100%, other-batch 404,
  durable resume/expiry and desktop/360px flows. `ui-foundation.spec.ts` checks
  route axe coverage and role/theme shell screenshots; `demo-smoke.spec.ts`
  remains read-only. Those cases passed in the full-suite gate below.
- Nineteen focused tests passed, including new independent error-envelope,
  queue and real lazy-render tests plus the existing quiz/rollback checks.
  Production Windows build/analyzer and Linux Docker builder passed. Required
  `make gen-api` and final full `make lint` passed (Ruff/format/mypy,
  ESLint/Prettier/strict TypeScript). Final aggregate **`make test` passed**:
  **1,723 API**, **244 Vitest**, **61 Playwright**, **7 unchanged deliberate
  skips**, **0 failures**, against real Compose services and the final host
  production web fallback. Desktop and 360px assessment authoring/learning,
  no-late-edits expiry, all four read-only demo roles, 16 shell screenshots
  and every renderable route's zero serious/critical axe check passed.
  No new endpoint, response schema, event or migration in this step;
  existing MATRIX/access-control coverage passed. No production application
  code changed after its measured builds. Commit hooks passed; whitespace,
  JSON, size and secret exclusions were checked. Local gates alone do not
  complete a step. Acceptance commit `7ac8fd49c42468a4a355edb5953ca22d747685f5` was pushed;
  `python scripts/ci_status.py 7ac8fd49c42468a4a355edb5953ca22d747685f5` exited **0** for
  [run 37977673085](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37977673085), with all six jobs successful.
  Step 9 is complete in the nine-step implementation table. The subsequent
  documentation-only release commit must also pass its exact-SHA checker before
  the tag is created. Its own SHA/URL belong in the annotated `v0.3.0` tag and
  final release summary rather than an invented self-reference in this file.

- Close-out (2026-10-10): dated complete status, all nine known implementation
  commits/green runs, final endpoint/table inventory, approved deviations and
  repairs, and carried follow-ups are recorded here. README Demo includes the
  quiz/reveal tour, cross-course rubric grading, penalty/100% example, role
  landings, credential location and unchanged risky-flag guards. The bundle
  report maps its precommit build evidence to the accepted source SHA and
  accounts for all shared modules/packages. The under-200-KiB work remains an
  explicitly estimated follow-up with tightened CI regression ceilings.
  No Phase 4 or other feature work starts during close-out.

### Final endpoint and table inventory

Verified against the shipped routers, generated OpenAPI and migrations. Routes
below have the `/api/v1` prefix. Phase 3 adds **34 method/path contracts**:
21 assessments, 6 assignment definition/history reads, 7 landing/grading reads.
All eight existing assignment contracts remain available with additive fields;
submission-upload and publishing use the approved If-Match tightening. All lists
are cursor endpoints. MATRIX and access-control remain the role references.

Assessments (21 contracts):

| Method | Path                                                                  |
| ------ | --------------------------------------------------------------------- |
| GET    | `/question-banks`                                                     |
| POST   | `/question-banks`                                                     |
| GET    | `/question-banks/{bank_id}`                                           |
| PATCH  | `/question-banks/{bank_id}`                                           |
| DELETE | `/question-banks/{bank_id}`                                           |
| GET    | `/question-banks/{bank_id}/questions`                                 |
| POST   | `/question-banks/{bank_id}/questions`                                 |
| GET    | `/questions/{question_id}`                                            |
| PATCH  | `/questions/{question_id}`                                            |
| DELETE | `/questions/{question_id}`                                            |
| PUT    | `/questions/{question_id}/skills`                                     |
| GET    | `/courses/{course_id}/lessons/{lesson_id}/quiz`                       |
| PUT    | `/courses/{course_id}/lessons/{lesson_id}/quiz`                       |
| GET    | `/courses/{course_id}/versions/{version_id}/lessons/{lesson_id}/quiz` |
| GET    | `/enrollments/{enrollment_id}/lessons/{lesson_id}/quiz`               |
| GET    | `/enrollments/{enrollment_id}/lessons/{lesson_id}/quiz-attempts`      |
| POST   | `/enrollments/{enrollment_id}/lessons/{lesson_id}/quiz-attempts`      |
| GET    | `/quiz-attempts/{attempt_id}`                                         |
| PUT    | `/quiz-attempts/{attempt_id}/answers`                                 |
| POST   | `/quiz-attempts/{attempt_id}/submit`                                  |
| GET    | `/quiz-attempts/{attempt_id}/results`                                 |

Assignments (14 contracts, extending the Phase 2.5 module):

| Method | Path                                                                                       | Phase 3 status      |
| ------ | ------------------------------------------------------------------------------------------ | ------------------- |
| GET    | `/courses/{course_id}/lessons/{lesson_id}/assignment`                                      | Retained / extended |
| PUT    | `/courses/{course_id}/lessons/{lesson_id}/assignment`                                      | Retained / extended |
| GET    | `/courses/{course_id}/lessons/{lesson_id}/assignment/preview`                              | New                 |
| GET    | `/enrollments/{enrollment_id}/lessons/{lesson_id}/assignment`                              | Retained / extended |
| POST   | `/enrollments/{enrollment_id}/lessons/{lesson_id}/submission-upload`                       | Retained / extended |
| GET    | `/enrollments/{enrollment_id}/lessons/{lesson_id}/submission-attempts`                     | New                 |
| GET    | `/enrollments/{enrollment_id}/lessons/{lesson_id}/submission-attempts/{attempt_id}/grades` | New                 |
| PUT    | `/enrollments/{enrollment_id}/lessons/{lesson_id}/submission`                              | Retained / extended |
| GET    | `/courses/{course_id}/lessons/{lesson_id}/submissions`                                     | Retained / extended |
| GET    | `/assignment-submissions/{submission_id}`                                                  | Retained / extended |
| PUT    | `/assignment-submissions/{submission_id}/grade`                                            | Retained / extended |
| GET    | `/assignment-submissions/{submission_id}/attempts`                                         | New                 |
| GET    | `/assignment-submissions/{submission_id}/attempts/{attempt_id}`                            | New                 |
| GET    | `/assignment-submissions/{submission_id}/attempts/{attempt_id}/grades`                     | New                 |

Tableless reports service (7 new contracts; 3 existing report routes retained):

| Method | Path                                   | Phase 3 status      |
| ------ | -------------------------------------- | ------------------- |
| GET    | `/dashboards/admin`                    | New                 |
| GET    | `/dashboards/admin/batches`            | New                 |
| GET    | `/dashboards/admin/unassigned-courses` | New                 |
| GET    | `/dashboards/teach`                    | New                 |
| GET    | `/assignment-submissions`              | New                 |
| GET    | `/dashboards/learn/due`                | New                 |
| GET    | `/dashboards/learn/results`            | New                 |
| GET    | `/courses/{course_id}/progress`        | Retained / extended |
| GET    | `/courses/{course_id}/progress.csv`    | Retained / extended |
| GET    | `/batches/{batch_id}/courses`          | Retained / extended |

The existing course publish/preview/minor-major paths freeze assessment and
assignment snapshots and expose their structural changes in the diff. Existing
skills services link question skills; notes-image flows reuse media upload,
confirmation and scoped references. These reuse existing contracts and tables.
No Phase 3 endpoint is introduced during Step 9.

| Module      | Tables                                                                    | Final responsibility                                                                                   |
| ----------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| assessments | `question_banks`, `questions`, `question_skills`, `question_keys`         | Draft banks/prompts/skill links; separate private answer keys.                                         |
| assessments | `quizzes`, `quiz_versions`, `quiz_version_questions`, `quiz_version_keys` | Draft rules, immutable published rules/pools/keys; structural publish diff and gated SQL reveal.       |
| assessments | `quiz_attempts`, `quiz_answers`                                           | Frozen student selection/rules, Postgres autosave/resume, authoritative expiry/scoring/attempt limits. |
| assignments | `assignments` (extended)                                                  | Rubric, instruction images and late-policy definitions, frozen in course snapshots.                    |
| assignments | `assignment_submissions` (extended)                                       | Stable current-submission aggregate/revision and active attempt reference.                             |
| assignments | `submission_attempts` (new)                                               | Immutable accepted text/file/rules/late evidence; every submission retained.                           |
| assignments | `assignment_grades` (extended)                                            | Grade history per attempt, criterion scores, raw/penalty/final marks; one-grade constraint removed.    |
| reports     | No tables                                                                 | Batched role landing/grading reads through module services and documented SQL interfaces.              |

Migrations **0012-0016** implement the new ten assessments tables, one assignment
history table, RLS/column grants/private SQL interfaces, runtime guards,
immutable-history backfill, authoring indexes and scoped dashboard helpers.
Courses, enrollments, skills, media, audits and the outbox reuse existing tables;
modules query only their own tables, with documented SQL/service boundaries.
Progress recomputation calls assessments/assignments services. The outbox SELECT
repair covers historical grade v1 payloads. Published events/topics and versioned
JSON Schemas are in `docs/events.md`: `quiz_attempt_submitted` v1 and
`assignment_graded` v2, with retained v1 schema validation.

### Open follow-ups / carried forward

- Plagiarism detection, AI feedback and coding labs remain outside this brief;
  realtime grade notifications remain Phase 4.
- Existing Phase 2/2.5 operational follow-ups remain: real-credential Bunny
  smoke, publisher-directory rules before a second publisher, large picker/
  skills-cache limits, read-replica routing for writing GETs, heartbeat revocation
  window, deployment/backup operations and Windows bind-mount behavior.
- Measure checkpoint throughput before asserting the 100,000-user scale target;
  a Redis read cache may be added later only if measured query load requires it.
- Automatic regrading/attempt refunds after grading-key corrections, retakes
  after assignment grading and individual deadline accommodations need separate
  product decisions; this phase preserves the established completion rules.
- Student first-load performance: **under 200 KiB gzip on every `/learn` route**
  remains the target. Step 9 removes 73.4 KiB per route through narrow Zod
  imports/Mini and first-use toast loading. The largest measured Linux player
  is 264.01 KiB; more than 64.01 KiB is still needed. See the
  [full package attribution, before/after evidence and remaining reduction plan](../design/student-bundles-step9.md).
  Follow-ups: defer noninitial Base UI shell overlays (25-40 KiB), investigate
  class-conflict equivalence (6-8 KiB), narrow remaining Mini/core validation
  (5-10 KiB), and server-render static shell/read-only summaries (10-20 KiB).
  The combined additional 46-78 KiB is a hypothesis with overlap/compression
  uncertainty, not a guarantee of the target. Preserve validation, accessibility,
  learning cache/timer behavior and the authoring-dependency ban. CI now fails
  above the exact larger Windows/Linux measured byte figures: `/learn` 266,673,
  both players 270,349, video 259,366. Never raise budgets automatically.

### Planning verification

This planning turn read the binding/requested documents, relevant module code,
RLS/publication/transaction tests and verified HEAD/tag/initial clean status.
Document checks run personally: installed Prettier Markdown check (passed after
formatting), `git diff --check` and the untracked plan's
`git diff --no-index --check -- /dev/null docs/plans/phase-3.md` whitespace check.
The tracked check exited 0; the no-index comparison exited 1 for the new-file
difference and emitted no whitespace warnings.
At the initial planning gate only this plan was added; implementation checks,
commit, push and CI had not run. Step 1 subsequently passed its local and pushed-CI gates (recorded above).
Steps 2-5 passed their local and pushed-CI gates (recorded above). The user
explicitly skipped the unspecified Step 5 follow-up and authorized Step 6;
the user subsequently authorized steps 7-9. All nine steps now have local
passing gates and green pushed implementation CI recorded above. Release tagging
still waits for green CI on the final documentation close-out commit.
