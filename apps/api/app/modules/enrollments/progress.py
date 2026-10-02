"""Completion rules and the course progress percentage (pure functions).

- video: completes automatically once the watched share reaches the lesson's threshold (0.9 by
  default); watching is reported by the player heartbeat (video step)
- notes: the student marks it complete
- pdf: the file must have been opened (a signed URL issued), then the student marks it complete
- assignment: completes when a grade is recorded (the assignments module calls
  `complete_graded_lesson`)
- quiz, lab: placeholders; they can't be completed and never count toward progress
"""

from decimal import Decimal
from enum import StrEnum

from app.modules.courses.models import PLACEHOLDER_LESSON_TYPES, LessonType

DEFAULT_VIDEO_THRESHOLD = Decimal("0.90")


class CompletionRule(StrEnum):
    MANUAL = "manual"
    MANUAL_AFTER_OPENING = "manual_after_opening"
    WATCHED = "watched"
    GRADED = "graded"
    NOT_COMPLETABLE = "not_completable"


def completion_rule(lesson_type: LessonType) -> CompletionRule:
    if lesson_type in PLACEHOLDER_LESSON_TYPES:
        return CompletionRule.NOT_COMPLETABLE
    return {
        LessonType.VIDEO: CompletionRule.WATCHED,
        LessonType.NOTES: CompletionRule.MANUAL,
        LessonType.PDF: CompletionRule.MANUAL_AFTER_OPENING,
        LessonType.ASSIGNMENT: CompletionRule.GRADED,
    }[lesson_type]


def video_watched(watched_ratio: Decimal | None, threshold: Decimal | None) -> bool:
    return watched_ratio is not None and watched_ratio >= (threshold or DEFAULT_VIDEO_THRESHOLD)


def course_percent(completed: int, required: int) -> int:
    """Completed required lessons / required lessons, rounded down, so 100 means everything is
    done. A course with no required lessons has nothing to complete: 0."""
    if required <= 0:
        return 0
    return min(completed, required) * 100 // required
