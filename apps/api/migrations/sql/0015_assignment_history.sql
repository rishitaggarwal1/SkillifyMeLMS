
CREATE TABLE submission_attempts (
	organization_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	user_id UUID NOT NULL,
	attempt_number INTEGER NOT NULL,
	major_version INTEGER NOT NULL,
	version_id UUID NOT NULL,
	kind VARCHAR(10) NOT NULL,
	text_body TEXT,
	file_id UUID,
	submitted_at TIMESTAMP WITH TIME ZONE NOT NULL,
	assignment_rules JSONB NOT NULL,
	late_data JSONB,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_submission_attempts PRIMARY KEY (id),
	CONSTRAINT fk_submission_attempts_submission FOREIGN KEY(submission_id, organization_id) REFERENCES assignment_submissions (id, organization_id) ON DELETE CASCADE,
	CONSTRAINT uq_submission_attempts_number UNIQUE (submission_id, attempt_number),
	CONSTRAINT uq_submission_attempts_identity UNIQUE (id, submission_id, organization_id),
	CONSTRAINT ck_submission_attempts_ck_submission_attempts_major_version CHECK (major_version >= 1),
	CONSTRAINT ck_submission_attempts_ck_submission_attempts_number CHECK (attempt_number >= 1),
	CONSTRAINT ck_submission_attempts_ck_submission_attempts_content CHECK ((kind = 'text' AND text_body IS NOT NULL AND file_id IS NULL) OR (kind = 'file' AND file_id IS NOT NULL AND text_body IS NULL)),
	CONSTRAINT ck_submission_attempts_ck_submission_attempts_text_length CHECK (text_body IS NULL OR char_length(text_body) <= 20000),
	CONSTRAINT fk_submission_attempts_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
	CONSTRAINT fk_submission_attempts_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	CONSTRAINT fk_submission_attempts_version_id_course_versions FOREIGN KEY(version_id) REFERENCES course_versions (id),
	CONSTRAINT fk_submission_attempts_file_id_files FOREIGN KEY(file_id) REFERENCES files (id)
)


-- statement
CREATE INDEX ix_submission_attempts_file_id ON submission_attempts (file_id)
-- statement
CREATE INDEX ix_submission_attempts_organization_id ON submission_attempts (organization_id)
-- statement
CREATE INDEX ix_submission_attempts_user_id ON submission_attempts (user_id)
-- statement
CREATE INDEX ix_submission_attempts_version_id ON submission_attempts (version_id)
-- statement
ALTER TABLE assignments ADD COLUMN rubric JSONB
-- statement
ALTER TABLE assignments ADD COLUMN late_policy JSONB
-- statement
ALTER TABLE assignment_submissions ADD COLUMN active_attempt_id UUID
-- statement
ALTER TABLE assignment_grades ADD COLUMN attempt_id UUID
-- statement
ALTER TABLE assignment_grades ADD COLUMN grade_sequence INTEGER
-- statement
ALTER TABLE assignment_grades ADD COLUMN rubric_breakdown JSONB
-- statement
ALTER TABLE assignment_grades ADD COLUMN raw_score NUMERIC(7, 2)
-- statement
ALTER TABLE assignment_grades ADD COLUMN penalty_percent NUMERIC(5, 2)
-- statement
ALTER TABLE assignment_grades ADD COLUMN penalty_marks NUMERIC(7, 2)
-- statement
ALTER TABLE assignment_grades DROP CONSTRAINT uq_assignment_grades_submission
-- statement
INSERT INTO submission_attempts (id,organization_id,submission_id,user_id,attempt_number,major_version,version_id,kind,text_body,file_id,submitted_at,assignment_rules,late_data,created_at,updated_at)
SELECT s.id,s.organization_id,s.id,s.user_id,1,v.major,s.version_id,s.kind,s.text_body,s.file_id,s.submitted_at,
  lesson->'content' || '{"rubric": null,"late_policy":{"mode":"accept","percent_per_day": null},"image_file_ids":[]}'::jsonb,
  NULL,s.created_at,s.updated_at
FROM assignment_submissions s JOIN course_versions v ON v.id=s.version_id,
 LATERAL jsonb_array_elements(v.snapshot->'modules') module,
 LATERAL jsonb_array_elements(module->'lessons') lesson
WHERE lesson->>'id'=s.lesson_id::text
-- statement
UPDATE assignment_submissions SET active_attempt_id=id
-- statement
UPDATE assignment_grades SET attempt_id=submission_id,grade_sequence=1,raw_score=score,penalty_percent=0,penalty_marks=0
-- statement
ALTER TABLE assignment_grades ALTER COLUMN attempt_id SET NOT NULL
-- statement
ALTER TABLE assignment_grades ALTER COLUMN grade_sequence SET NOT NULL
-- statement
ALTER TABLE assignment_grades ALTER COLUMN raw_score SET NOT NULL
-- statement
ALTER TABLE assignment_grades ALTER COLUMN penalty_percent SET NOT NULL
-- statement
ALTER TABLE assignment_grades ALTER COLUMN penalty_marks SET NOT NULL
-- statement
ALTER TABLE assignment_submissions ADD CONSTRAINT fk_assignment_submissions_active_attempt FOREIGN KEY(active_attempt_id, id, organization_id) REFERENCES submission_attempts (id, submission_id, organization_id) DEFERRABLE INITIALLY DEFERRED
-- statement
ALTER TABLE assignment_grades ADD CONSTRAINT uq_assignment_grades_attempt_sequence UNIQUE (attempt_id, grade_sequence)
-- statement
ALTER TABLE assignment_grades ADD CONSTRAINT fk_assignment_grades_attempt FOREIGN KEY(attempt_id, submission_id, organization_id) REFERENCES submission_attempts (id, submission_id, organization_id)
-- statement
ALTER TABLE assignment_grades ADD CONSTRAINT ck_assignment_grades_ck_assignment_grades_penalty CHECK (raw_score >= 0 AND raw_score <= max_marks AND penalty_percent BETWEEN 0 AND 100 AND penalty_marks >= 0 AND score = greatest(0, raw_score - penalty_marks))
-- statement
ALTER TABLE assignment_grades ADD CONSTRAINT ck_assignment_grades_ck_assignment_grades_sequence CHECK (grade_sequence >= 1)
-- statement
CREATE INDEX ix_assignment_submissions_active_attempt_id ON assignment_submissions (active_attempt_id)
-- statement
CREATE INDEX ix_assignment_grades_submission_id ON assignment_grades (submission_id)
-- statement
CREATE INDEX ix_submission_attempts_submission_org ON submission_attempts (submission_id, organization_id)
-- statement
CREATE INDEX ix_assignment_submissions_active_attempt_org ON assignment_submissions (active_attempt_id, id, organization_id)
-- statement
CREATE INDEX ix_assignment_grades_submission_org ON assignment_grades (submission_id, organization_id)
-- statement
CREATE INDEX ix_assignment_grades_attempt_org ON assignment_grades (attempt_id, submission_id, organization_id)
