"""Cross-module hooks, connected in one place.

Modules talk to each other through their service interfaces. When a lower-level module needs
something a higher-level module provides (publishing, in `courses`, needs assignment content from
`assignments`, which itself depends on `courses`), the lower module defines a hook and this module
connects it. Nothing else registers hooks.

Every entry point gets the wiring without remembering to call anything: `courses.content_sources`
imports this module the first time it needs a source (API, Celery workers, CLI seeds, tests).
`app.main.create_app` also calls `wire()` at startup, so a broken import fails at boot rather than
at the first publish. `wire()` is idempotent.
"""

from app.modules.assessments.service import QuizContentSource
from app.modules.assignments.service import AssignmentContentSource
from app.modules.courses import content_sources
from app.modules.courses.models import LessonType


def wire() -> None:
    content_sources.register(LessonType.ASSIGNMENT, AssignmentContentSource())
    content_sources.register(LessonType.QUIZ, QuizContentSource())
