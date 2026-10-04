"""Model registry: import every module's models here so `Base.metadata` is complete for Alembic."""

from app.db.base import Base
from app.db.outbox import OutboxEvent
from app.modules.assessments.models import (
    Question,
    QuestionBank,
    QuestionKey,
    QuestionSkill,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizVersion,
    QuizVersionKey,
    QuizVersionQuestion,
)
from app.modules.assignments.models import Assignment, AssignmentGrade, AssignmentSubmission
from app.modules.audit.models import AuditLog
from app.modules.courses.models import (
    CatalogEntry,
    Course,
    CourseAssignment,
    CourseModule,
    CourseVersion,
    CourseVersionLesson,
    Lesson,
    LessonSkill,
)
from app.modules.enrollments.models import Enrollment, LessonProgress
from app.modules.identity.models import (
    Batch,
    BatchMember,
    ImportJob,
    ImportJobError,
    Invitation,
    Membership,
    Organization,
    User,
)
from app.modules.media.models import StoredFile, VideoAsset
from app.modules.skills.models import Skill

__all__ = [
    "Assignment",
    "AssignmentGrade",
    "AssignmentSubmission",
    "AuditLog",
    "Base",
    "Batch",
    "BatchMember",
    "CatalogEntry",
    "Course",
    "CourseAssignment",
    "CourseModule",
    "CourseVersion",
    "CourseVersionLesson",
    "Enrollment",
    "ImportJob",
    "ImportJobError",
    "Invitation",
    "Lesson",
    "LessonProgress",
    "LessonSkill",
    "Membership",
    "Organization",
    "OutboxEvent",
    "Question",
    "QuestionBank",
    "QuestionKey",
    "QuestionSkill",
    "Quiz",
    "QuizAnswer",
    "QuizAttempt",
    "QuizVersion",
    "QuizVersionKey",
    "QuizVersionQuestion",
    "Skill",
    "StoredFile",
    "User",
    "VideoAsset",
]
