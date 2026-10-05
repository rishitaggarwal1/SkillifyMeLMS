"""Guarded quiz runtime and trusted grading interfaces.

Revision ID: 0014
Revises: 0013
"""

import json
from pathlib import Path

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

FUNCTIONS = (
    "app.quiz_normalize(text,boolean)",
    "app.quiz_start(uuid,uuid,uuid,integer,uuid)",
    "app.quiz_mutate(uuid,integer,jsonb,boolean,boolean)",
    "app.quiz_save_answers(uuid,integer,jsonb)",
    "app.quiz_submit(uuid,integer,jsonb)",
    "app.quiz_finalize_due(uuid)",
    "app.quiz_close_old_major(uuid[],integer)",
    "app.student_course_assigned(uuid,uuid,uuid)",
)


def upgrade() -> None:
    # Full Unicode casefold, rather than SQL lower(), matches author validation.
    # ASCII JSON keeps this migration portable across shell/source encodings.
    folds = {chr(i): chr(i).casefold() for i in range(0x110000) if chr(i).casefold() != chr(i)}
    mapping = json.dumps(folds, ensure_ascii=True).replace("'", "''")
    op.execute(f"""
      CREATE FUNCTION app.quiz_normalize(value text,sensitive boolean) RETURNS text
      LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, public AS $fn$
      DECLARE value_normalized text; output text := ''; ch text;
        folds constant jsonb := '{mapping}'::jsonb;
      BEGIN
        value_normalized := normalize(btrim(value,E' \t\n\r\v\f' || chr(133) || chr(160) ||
          chr(5760) || chr(8192) || chr(8193) || chr(8194) || chr(8195) || chr(8196) ||
          chr(8197) || chr(8198) || chr(8199) || chr(8200) || chr(8201) || chr(8202) ||
          chr(8232) || chr(8233) || chr(8239) || chr(8287) || chr(12288) ||
          chr(28) || chr(29) || chr(30) || chr(31)), NFC);
        IF sensitive THEN RETURN value_normalized; END IF;
        FOR i IN 1..coalesce(length(value_normalized),0) LOOP
          ch := substr(value_normalized,i,1);
          output := output || coalesce(folds->>ch,ch);
        END LOOP;
        RETURN CASE WHEN value IS NULL THEN NULL ELSE output END;
      END $fn$
    """)
    sql = (Path(__file__).resolve().parents[1] / "sql" / "0014_quiz_runtime.sql").read_text(
        encoding="utf-8"
    )
    for statement in sql.split("-- statement"):
        op.execute(statement)
    for signature in FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        if "quiz_mutate" not in signature:
            op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO skillify_app")


def downgrade() -> None:
    for signature in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
