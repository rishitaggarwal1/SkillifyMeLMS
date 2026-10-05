-- Frozen runtime SQL interfaces. Private grading data never leaves the finalizer.
CREATE FUNCTION app.quiz_start(enrollment uuid, lesson uuid, published uuid, expected integer,
                               attempt uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE e public.enrollments; q public.quiz_versions; used integer; picked uuid[]; at timestamptz;
BEGIN
  SELECT * INTO e FROM public.enrollments WHERE id=enrollment
    AND organization_id=app.current_org_id() AND user_id=app.current_user_id() FOR UPDATE;
  IF e.id IS NULL OR e.organization_id IS DISTINCT FROM app.current_org_id()
     OR e.user_id IS DISTINCT FROM app.current_user_id() OR e.status <> 'active'
     OR NOT app.current_user_has_role(e.organization_id, '{student}'::text[])
     OR NOT app.course_readable(e.course_id) THEN RETURN '{"error":"not_found"}'; END IF;
  SELECT * INTO q FROM public.quiz_versions WHERE id=published AND course_id=e.course_id
    AND lesson_id=lesson AND major_version=e.major_version;
  IF q.id IS NULL OR q.course_version_id IS DISTINCT FROM (
    SELECT id FROM public.course_versions WHERE course_id=e.course_id AND major=e.major_version
    ORDER BY minor DESC LIMIT 1
  ) THEN RETURN '{"error":"quiz_changed"}'; END IF;
  SELECT count(*) INTO used FROM public.quiz_attempts
    WHERE enrollment_id=enrollment AND lesson_id=lesson AND major_version=e.major_version;
  IF used <> expected THEN RETURN '{"error":"stale_revision"}'; END IF;
  IF EXISTS (SELECT 1 FROM public.quiz_attempts WHERE enrollment_id=enrollment
    AND lesson_id=lesson AND major_version=e.major_version AND state='in_progress')
    THEN RETURN '{"error":"attempt_active"}'; END IF;
  IF used >= q.attempts_allowed THEN RETURN '{"error":"attempt_limit"}'; END IF;
  SELECT array_agg(id ORDER BY sort_order) INTO picked FROM (
    SELECT id, CASE WHEN q.randomize_order THEN random() ELSE position::double precision END sort_order
    FROM (
      SELECT id, position FROM public.quiz_version_questions WHERE quiz_version_id=q.id
      ORDER BY CASE WHEN q.selection_mode='bank' THEN random() ELSE position::double precision END
      LIMIT CASE WHEN q.selection_mode='bank' THEN (q.selection->>'draw_count')::integer ELSE 100 END
    ) selected
  ) ordered;
  IF picked IS NULL OR (q.selection_mode='bank' AND cardinality(picked) <> (q.selection->>'draw_count')::integer)
    THEN RETURN '{"error":"quiz_not_ready"}'; END IF;
  at := clock_timestamp();
  INSERT INTO public.quiz_attempts(id,organization_id,enrollment_id,user_id,course_id,lesson_id,
    quiz_version_id,major_version,attempt_number,question_ids,state,revision,started_at,expires_at,max_marks)
  VALUES(attempt,e.organization_id,e.id,e.user_id,e.course_id,lesson,q.id,e.major_version,
    used+1,picked,'in_progress',1,at,at+make_interval(secs=>q.time_limit_seconds),q.max_marks);
  RETURN jsonb_build_object('id',attempt,'newly',true);
END $$;
-- statement
CREATE FUNCTION app.quiz_mutate(attempt uuid, expected integer, answers jsonb,
                               submitting boolean, system_job boolean) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE a public.quiz_attempts; e public.enrollments; q public.quiz_versions;
  at timestamptz; item jsonb; v jsonb; p public.quiz_version_questions; k jsonb;
  selected text[]; allowed text[]; correct text[]; c integer; w integer; earned numeric;
  total numeric := 0; response text; expired boolean; answer_text text;
  prompts jsonb; grading record; awards jsonb := '[]'::jsonb;
BEGIN
  IF system_job THEN
    IF NOT app.current_user_is_platform_admin() OR app.current_user_id() IS NOT NULL
      THEN RETURN '{"error":"not_found"}'; END IF;
    SELECT en.* INTO e FROM public.enrollments en JOIN public.quiz_attempts x ON x.enrollment_id=en.id
      WHERE x.id=attempt AND x.organization_id=app.current_org_id() FOR UPDATE OF en SKIP LOCKED;
  ELSE
    SELECT en.* INTO e FROM public.enrollments en JOIN public.quiz_attempts x ON x.enrollment_id=en.id
      WHERE x.id=attempt AND x.organization_id=app.current_org_id()
        AND x.user_id=app.current_user_id() FOR UPDATE OF en;
  END IF;
  IF e.id IS NULL THEN RETURN '{"error":"not_found"}'; END IF;
  IF system_job THEN
    SELECT * INTO a FROM public.quiz_attempts WHERE id=attempt FOR UPDATE SKIP LOCKED;
  ELSE
    SELECT * INTO a FROM public.quiz_attempts WHERE id=attempt FOR UPDATE;
  END IF;
  IF a.id IS NULL THEN RETURN '{"error":"not_found"}'; END IF;
  at := clock_timestamp();
  IF NOT system_job AND (e.status <> 'active' OR e.major_version <> a.major_version
    OR NOT app.current_user_has_role(e.organization_id, '{student}'::text[])
    OR NOT app.course_readable(e.course_id)) THEN RETURN '{"error":"not_found"}'; END IF;
  IF a.state='submitted' AND submitting THEN RETURN jsonb_build_object('id',a.id,'newly',false); END IF;
  IF a.state <> 'in_progress' THEN RETURN '{"error":"attempt_closed"}'; END IF;
  IF system_job AND e.major_version <> a.major_version THEN
    UPDATE public.quiz_attempts SET state='abandoned',revision=revision+1,updated_at=at WHERE id=a.id;
    RETURN jsonb_build_object('id',a.id,'newly',false);
  END IF;
  IF NOT system_job AND a.revision <> expected THEN RETURN '{"error":"stale_revision"}'; END IF;
  expired := at >= a.expires_at;
  IF system_job AND NOT expired THEN RETURN jsonb_build_object('id',a.id,'newly',false,'expires_at',a.expires_at); END IF;
  IF expired AND NOT submitting THEN RETURN '{"error":"attempt_expired"}'; END IF;
  IF NOT expired THEN
    IF answers IS NULL OR jsonb_typeof(answers) <> 'array' OR jsonb_array_length(answers)>100
      THEN RETURN '{"error":"invalid_answers"}'; END IF;
    IF (SELECT count(*) <> count(DISTINCT x->>'question_id') FROM jsonb_array_elements(answers) x)
      THEN RETURN '{"error":"invalid_answers"}'; END IF;
    SELECT jsonb_object_agg(prompt_row.id::text,to_jsonb(prompt_row)) INTO prompts
      FROM public.quiz_version_questions prompt_row
      WHERE prompt_row.id=ANY(a.question_ids) AND prompt_row.quiz_version_id=a.quiz_version_id;
    -- Validate the whole batch before changing any answer or revision.
    FOR item IN SELECT * FROM jsonb_array_elements(answers) LOOP
      IF jsonb_typeof(item) <> 'object' OR (item - ARRAY['id','question_id','answer']) <> '{}'::jsonb
        OR item->>'id' IS NULL OR item->>'question_id' IS NULL
        THEN RETURN '{"error":"invalid_answers"}'; END IF;
      p := jsonb_populate_record(NULL::public.quiz_version_questions,prompts->(item->>'question_id'));
      IF p.id IS NULL THEN RETURN '{"error":"invalid_answers"}'; END IF;
      v := item->'answer';
      IF v IS NULL OR jsonb_typeof(v) <> 'object' OR (v - ARRAY['option_ids','text']) <> '{}'::jsonb
         OR jsonb_typeof(v->'option_ids') IS DISTINCT FROM 'array'
         OR jsonb_array_length(v->'option_ids')>100
         OR (v->'text' <> 'null'::jsonb AND jsonb_typeof(v->'text') <> 'string')
         OR length(coalesce(v->>'text',''))>20000
        THEN RETURN '{"error":"invalid_answers"}'; END IF;
      IF EXISTS (SELECT 1 FROM jsonb_array_elements(v->'option_ids') x
        WHERE jsonb_typeof(x) <> 'string') THEN RETURN '{"error":"invalid_answers"}'; END IF;
      SELECT coalesce(array_agg(x), '{}'::text[]) INTO selected FROM jsonb_array_elements_text(v->'option_ids') x;
      SELECT coalesce(array_agg(x->>'id'), '{}'::text[]) INTO allowed FROM jsonb_array_elements(p.options) x;
      IF (SELECT count(*) <> count(DISTINCT x) FROM unnest(selected) x)
        OR NOT selected <@ allowed
        OR (p.question_type='mcq_single' AND cardinality(selected)>1)
        OR (p.question_type='fill_blank' AND cardinality(selected)>0)
        OR (p.question_type<>'fill_blank' AND v->>'text' IS NOT NULL)
        THEN RETURN '{"error":"invalid_answers"}'; END IF;
    END LOOP;
    INSERT INTO public.quiz_answers(id,organization_id,attempt_id,question_id,answer,revision)
      SELECT (x->>'id')::uuid,a.organization_id,a.id,(x->>'question_id')::uuid,x->'answer',a.revision+1
      FROM jsonb_array_elements(answers) x
      ON CONFLICT (attempt_id,question_id) DO UPDATE SET answer=EXCLUDED.answer,
        revision=EXCLUDED.revision,updated_at=at;
  END IF;
  IF NOT submitting THEN
    UPDATE public.quiz_attempts SET revision=revision+1,updated_at=at WHERE id=a.id;
    RETURN jsonb_build_object('id',a.id,'newly',false);
  END IF;
  SELECT * INTO q FROM public.quiz_versions WHERE id=a.quiz_version_id;
  FOR grading IN
    SELECT prompt_row.id,prompt_row.question_type,prompt_row.options,prompt_row.marks,key_data.answer_key,ans.answer
    FROM public.quiz_version_questions prompt_row JOIN public.quiz_version_keys key_data ON key_data.question_id=prompt_row.id
    LEFT JOIN public.quiz_answers ans ON ans.question_id=prompt_row.id AND ans.attempt_id=a.id
    WHERE prompt_row.id=ANY(a.question_ids) AND prompt_row.quiz_version_id=a.quiz_version_id
  LOOP
    k := grading.answer_key;
    v := grading.answer;
    earned := 0;
    IF v IS NOT NULL THEN
      IF grading.question_type='fill_blank' THEN
        answer_text := app.quiz_normalize(v->>'text',coalesce((k->>'case_sensitive')::boolean,false));
        IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(k->'accepted_answers') x
          WHERE app.quiz_normalize(x,coalesce((k->>'case_sensitive')::boolean,false))=answer_text)
          THEN earned := grading.marks; END IF;
      ELSE
        SELECT coalesce(array_agg(x), '{}'::text[]) INTO selected FROM jsonb_array_elements_text(v->'option_ids') x;
        SELECT array_agg(x) INTO correct FROM jsonb_array_elements_text(k->'correct_option_ids') x;
        IF grading.question_type='mcq_single' THEN
          IF selected=correct THEN earned := grading.marks; END IF;
        ELSE
          SELECT count(*) FILTER (WHERE x=ANY(correct)),count(*) FILTER (WHERE NOT x=ANY(correct))
            INTO c,w FROM unnest(selected) x;
          earned := grading.marks * greatest(0, c::numeric/cardinality(correct)
            - w::numeric/(jsonb_array_length(grading.options)-cardinality(correct)));
        END IF;
      END IF;
    END IF;
    earned := round(earned,2);
    total := total + earned;
    awards := awards || jsonb_build_array(jsonb_build_object('question_id',grading.id,'marks',earned));
  END LOOP;
  UPDATE public.quiz_answers ans SET awarded_marks=(r->>'marks')::numeric,updated_at=at
    FROM jsonb_array_elements(awards) r WHERE ans.attempt_id=a.id AND ans.question_id=(r->>'question_id')::uuid;
  UPDATE public.quiz_attempts SET state='submitted',score=total,passed=(total>=q.pass_marks),
    submitted_at=at,revision=revision+1,updated_at=at WHERE id=a.id;
  response := CASE WHEN expired THEN 'expiry' ELSE 'manual' END;
  RETURN jsonb_build_object('id',a.id,'newly',true,'reason',response);
END $$;
-- statement
CREATE FUNCTION app.quiz_save_answers(attempt uuid,expected integer,answers jsonb) RETURNS jsonb
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public
AS $$ SELECT app.quiz_mutate(attempt,expected,answers,false,false) $$;
-- statement
CREATE FUNCTION app.quiz_submit(attempt uuid,expected integer,answers jsonb) RETURNS jsonb
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public
AS $$ SELECT app.quiz_mutate(attempt,expected,answers,true,false) $$;
-- statement
CREATE FUNCTION app.quiz_finalize_due(attempt uuid) RETURNS jsonb
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public
AS $$ SELECT app.quiz_mutate(attempt,0,'[]'::jsonb,true,true) $$;
-- statement
CREATE FUNCTION app.quiz_close_old_major(enrollments uuid[],major integer) RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE changed integer;
BEGIN
  UPDATE public.quiz_attempts a SET state='abandoned',revision=revision+1,updated_at=clock_timestamp()
    FROM public.enrollments e WHERE a.enrollment_id=e.id AND e.id=ANY(enrollments)
      AND e.organization_id=app.current_org_id() AND e.major_version=major AND a.major_version<major
      AND a.state='in_progress' AND (app.current_user_is_platform_admin()
        OR app.current_user_has_role(e.organization_id,'{org_admin}'::text[]));
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed;
END $$;
-- statement
CREATE FUNCTION app.student_course_assigned(student uuid,org uuid,course uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT (app.current_user_is_platform_admin() OR (org=app.current_org_id() AND (
    student=app.current_user_id() OR app.current_user_has_role(org,'{instructor,org_admin}'::text[]))))
    AND EXISTS (
      SELECT 1 FROM public.course_assignments ca
      JOIN public.batch_members bm ON bm.batch_id=ca.batch_id AND bm.organization_id=ca.organization_id
      JOIN public.memberships m ON m.organization_id=bm.organization_id AND m.user_id=bm.user_id
      JOIN public.organizations o ON o.id=m.organization_id
      WHERE ca.course_id=course AND ca.organization_id=org AND bm.user_id=student
        AND m.role='student' AND o.status='active'
    )
$$;
