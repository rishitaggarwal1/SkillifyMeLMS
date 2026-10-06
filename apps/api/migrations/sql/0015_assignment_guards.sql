-- Application inserts cannot manufacture historical file access or frozen rules.
CREATE FUNCTION app.assignment_attempt_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
DECLARE s public.assignment_submissions; rules jsonb; version_major integer;
  due timestamptz; days integer; percent numeric; is_late boolean;
BEGIN
  IF current_setting('role') <> 'skillify_app' AND session_user <> 'skillify_app' THEN
    RETURN NEW;
  END IF;
  SELECT * INTO s FROM public.assignment_submissions WHERE id=NEW.submission_id;
  SELECT v.major, lesson->'content' INTO version_major,rules
    FROM public.course_versions v,
      LATERAL jsonb_array_elements(v.snapshot->'modules') module,
      LATERAL jsonb_array_elements(module->'lessons') lesson
    WHERE v.id=NEW.version_id AND v.course_id=s.course_id AND lesson->>'id'=s.lesson_id::text;
  rules := jsonb_build_object('rubric',NULL,'late_policy',
    jsonb_build_object('mode','accept','percent_per_day',NULL),'image_file_ids','[]'::jsonb) || rules;
  IF rules IS NULL OR NEW.organization_id<>s.organization_id OR NEW.user_id<>s.user_id
    OR NEW.major_version<>version_major OR NEW.version_id<>s.version_id
    OR NEW.assignment_rules - 'due_at' IS DISTINCT FROM rules - 'due_at'
    OR (NEW.assignment_rules->>'due_at')::timestamptz IS DISTINCT FROM (rules->>'due_at')::timestamptz
    OR NEW.attempt_number<>(SELECT coalesce(max(a.attempt_number),0)+1
      FROM public.submission_attempts a WHERE a.submission_id=s.id)
  THEN RAISE EXCEPTION 'attempt rules must match its published assignment' USING ERRCODE='23514';
  END IF;
  due := (rules->>'due_at')::timestamptz;
  is_late := coalesce(NEW.submitted_at>due,false);
  days := CASE WHEN is_late THEN ceil(extract(epoch FROM NEW.submitted_at-due)/86400) ELSE 0 END;
  percent := CASE WHEN rules->'late_policy'->>'mode'='penalty'
    THEN least(100,days*(rules->'late_policy'->>'percent_per_day')::numeric) ELSE 0 END;
  IF (is_late AND rules->'late_policy'->>'mode'='reject')
    OR NEW.submitted_at<>s.submitted_at OR NEW.kind<>s.kind
    OR NEW.file_id IS DISTINCT FROM s.file_id OR NEW.text_body IS DISTINCT FROM s.text_body
    OR NEW.late_data IS NULL
    OR (NEW.late_data->>'is_late')::boolean IS DISTINCT FROM is_late
    OR (NEW.late_data->>'late_days')::integer IS DISTINCT FROM days
    OR (NEW.late_data->>'penalty_percent')::numeric IS DISTINCT FROM percent
    OR (NEW.late_data->>'due_at')::timestamptz IS DISTINCT FROM due
    OR NEW.late_data->'policy' IS DISTINCT FROM rules->'late_policy'
    OR (NEW.late_data->>'closed')::boolean IS DISTINCT FROM false
  THEN RAISE EXCEPTION 'attempt lateness must match its accepted work' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $fn$;
-- statement
CREATE TRIGGER assignment_attempt_guard BEFORE INSERT ON submission_attempts
FOR EACH ROW EXECUTE FUNCTION app.assignment_attempt_guard();
-- statement
CREATE FUNCTION app.assignment_grade_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
DECLARE a public.submission_attempts; s public.assignment_submissions;
  criterion jsonb; earned numeric := 0; percent numeric; rubric jsonb; count_scores integer;
BEGIN
  IF current_setting('role') <> 'skillify_app' AND session_user <> 'skillify_app' THEN
    RETURN NEW;
  END IF;
  SELECT * INTO a FROM public.submission_attempts WHERE id=NEW.attempt_id;
  SELECT * INTO s FROM public.assignment_submissions WHERE id=NEW.submission_id;
  percent := coalesce((a.late_data->>'penalty_percent')::numeric,0);
  IF a.id IS NULL OR s.id IS NULL OR a.id<>s.active_attempt_id
    OR a.submission_id<>s.id OR NEW.user_id<>s.user_id OR NEW.organization_id<>s.organization_id
    OR NEW.max_marks<>(a.assignment_rules->>'max_marks')::integer
    OR NEW.grade_sequence<>(SELECT coalesce(max(g.grade_sequence),0)+1
      FROM public.assignment_grades g WHERE g.attempt_id=a.id)
    OR NEW.penalty_percent<>percent OR NEW.penalty_marks<>round(NEW.raw_score*percent/100,2)
  THEN RAISE EXCEPTION 'grade must use the active attempt frozen rules' USING ERRCODE='23514';
  END IF;
  rubric := a.assignment_rules->'rubric';
  IF rubric IS NOT NULL AND rubric<>'null'::jsonb THEN
    count_scores := jsonb_array_length(NEW.rubric_breakdown);
    IF count_scores IS DISTINCT FROM jsonb_array_length(rubric->'criteria')
      OR count_scores<>(SELECT count(DISTINCT b->>'criterion_id')
        FROM jsonb_array_elements(NEW.rubric_breakdown) b)
    THEN RAISE EXCEPTION 'rubric scores must cover every criterion' USING ERRCODE='23514'; END IF;
    FOR criterion IN SELECT c FROM jsonb_array_elements(rubric->'criteria') c LOOP
      SELECT (b->>'score')::numeric INTO percent FROM jsonb_array_elements(NEW.rubric_breakdown) b
        WHERE b->>'criterion_id'=criterion->>'id';
      IF percent IS NULL OR percent<0 OR percent>(criterion->>'max_marks')::numeric THEN
        RAISE EXCEPTION 'invalid rubric criterion score' USING ERRCODE='23514';
      END IF;
      earned := earned+percent;
    END LOOP;
    IF earned<>NEW.raw_score THEN
      RAISE EXCEPTION 'rubric total must equal earned marks' USING ERRCODE='23514';
    END IF;
  ELSIF NEW.rubric_breakdown IS NOT NULL AND NEW.rubric_breakdown<>'null'::jsonb THEN
    RAISE EXCEPTION 'unrubric grade must use a total' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $fn$;
-- statement
CREATE TRIGGER assignment_grade_guard BEFORE INSERT ON assignment_grades
FOR EACH ROW EXECUTE FUNCTION app.assignment_grade_guard();
