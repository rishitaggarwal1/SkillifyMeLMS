# Phase 3 — Quizzes and full assignments

**Status: approved (2026-10-04); steps 1 and 2 complete; step 3 local gates passed, pushed CI pending.** Decisions D1–D7 and
the security repair below include the user's approved revisions.

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
  focus ring, radius and elevation; light and dark. No ad-hoc hex colours in
  components after this step, enforced by a lint rule or grep test.
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
  screen uses the shells and shared components. No functional changes; existing
  tests stay unchanged apart from selectors.
- **Evidence:** Playwright shell screenshot assertions per role at 360px and
  1280px (content is not pixel-perfect); axe-core scans on every route with zero
  serious/critical violations.

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
   Retrofit every existing screen without functional changes. Add shell visual
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
   mark complete with date only after all gates have passed. Report and stop.

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
The new UI foundation is step 5. Its written role walkthrough requires separate
approval before coding that step; steps 6 and 7 use its guidelines/components.

## 14. Implementation record

Steps 1 and 2 completed with green pushed CI. After each green pushed step, report its full SHA and
CI URL in the step summary; carry known commit/run records into this table in
the next plan update. Do not invent a self-referential commit SHA or a CI URL
before the commit/run exists. The close-out summary records its own final run.

| Step | Status                     | Commit                                     | CI run                                                                                   |
| ---- | -------------------------- | ------------------------------------------ | ---------------------------------------------------------------------------------------- |
| 1    | Complete                   | `99b59e7b3e8d284fafce9fb40ef9533d20b503a9` | [37220970826](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37220970826) |
| 2    | Complete                   | `d660c7330e2f520a043cd58745aac4452576816e` | [37231238285](https://github.com/rishitaggarwal1/SkillifyMeLMS/actions/runs/37231238285) |
| 3    | Local verified; CI pending | —                                          | —                                                                                        |
| 4    | Not started                | —                                          | —                                                                                        |
| 5    | Not started                | —                                          | —                                                                                        |
| 6    | Not started                | —                                          | —                                                                                        |
| 7    | Not started                | —                                          | —                                                                                        |
| 8    | Not started                | —                                          | —                                                                                        |
| 9    | Not started                | —                                          | —                                                                                        |

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
Step 2 passed its local and pushed-CI gates (recorded above). Step 3 is authorized
and its verification is recorded in its implementation entry and step summary;
later steps remain gated on the user's "continue".
