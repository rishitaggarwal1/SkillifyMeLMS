"""Version snapshots and the minor/major rule (pure functions; no database access).

A snapshot is the full published outline, stored immutably in `course_versions.snapshot`:

    {"schema": 1,
     "course": {"id", "title", "description"},
     "modules": [{"id", "title", "position",
                  "lessons": [{"id", "title", "lesson_type", "position", "is_required",
                               "completion_threshold", "estimated_minutes", "content",
                               "skill_ids", "video_duration_seconds"}]}]}

`content` is the lesson's content, except notes: `{"html", "image_file_ids"}` rendered at publish.

A **minor** release may only correct content. Compared with the previous version it must keep the
same modules in the same order, the same lessons in the same modules and order, and each lesson's
type, `is_required` and `completion_threshold`.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.modules.courses import notes
from app.modules.courses.models import Course, CourseModule, Lesson, LessonType
from app.modules.courses.schemas import StructuralChange

SNAPSHOT_SCHEMA = 1
DEFAULT_VIDEO_THRESHOLD = Decimal("0.90")


def version_label(major: int, minor: int) -> str:
    return f"{major}.{minor}"


def threshold_text(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def published_content(lesson_type: LessonType, content: Mapping[str, Any]) -> dict[str, Any]:
    """What a version stores for a lesson's content. Notes are rendered once, at publish, to
    sanitized HTML (readers never receive the editor's JSON); images stay as `data-file-id`
    placeholders that readers resolve to short-lived signed URLs."""
    if lesson_type != LessonType.NOTES:
        return dict(content)
    doc = content.get("doc")
    return {
        "html": notes.render_html(doc),
        "image_file_ids": [str(i) for i in notes.image_file_ids(doc)] if doc else [],
    }


def lesson_file_ids(lesson_type: LessonType, content: Mapping[str, Any]) -> list[UUID]:
    """Files a published lesson uses (readable by its enrolled students)."""
    if lesson_type == LessonType.PDF and (file_id := content.get("file_id")):
        return [UUID(str(file_id))]
    if lesson_type == LessonType.NOTES and (doc := content.get("doc")):
        return notes.image_file_ids(doc)
    return []


def build_snapshot(
    course: Course,
    modules: Sequence[CourseModule],
    lessons: Sequence[Lesson],
    skill_ids: Mapping[UUID, Sequence[UUID]],
    video_durations: Mapping[UUID, int | None],
) -> dict[str, Any]:
    by_module: dict[UUID, list[Lesson]] = {m.id: [] for m in modules}
    for lesson in lessons:
        by_module[lesson.module_id].append(lesson)

    def lesson_json(lesson: Lesson) -> dict[str, Any]:
        video_id = lesson.content.get("video_asset_id")
        return {
            "id": str(lesson.id),
            "title": lesson.title,
            "lesson_type": lesson.lesson_type.value,
            "position": lesson.position,
            "is_required": lesson.is_required,
            "completion_threshold": threshold_text(lesson.completion_threshold),
            "estimated_minutes": lesson.estimated_minutes,
            "content": published_content(lesson.lesson_type, lesson.content),
            "skill_ids": [str(s) for s in skill_ids.get(lesson.id, [])],
            "video_duration_seconds": video_durations.get(UUID(video_id)) if video_id else None,
        }

    return {
        "schema": SNAPSHOT_SCHEMA,
        "course": {
            "id": str(course.id),
            "title": course.title,
            "description": course.description,
        },
        "modules": [
            {
                "id": str(module.id),
                "title": module.title,
                "position": module.position,
                "lessons": [
                    lesson_json(lesson)
                    for lesson in sorted(by_module[module.id], key=lambda x: x.position)
                ],
            }
            for module in sorted(modules, key=lambda m: m.position)
        ],
    }


def snapshot_lessons(snapshot: Mapping[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(module, lesson) pairs in outline order."""
    return [(m, lesson) for m in snapshot["modules"] for lesson in m["lessons"]]


def structural_changes(
    previous: Mapping[str, Any], draft: Mapping[str, Any]
) -> list[StructuralChange]:
    """What makes `draft` a major change relative to `previous` (empty: a minor is allowed)."""
    changes: list[StructuralChange] = []
    if [m["id"] for m in previous["modules"]] != [m["id"] for m in draft["modules"]]:
        changes.append(StructuralChange(code="modules_changed"))

    old = {lesson["id"]: (m["id"], lesson) for m, lesson in snapshot_lessons(previous)}
    new = {lesson["id"]: (m["id"], lesson) for m, lesson in snapshot_lessons(draft)}
    added = [UUID(i) for i in new if i not in old]
    removed = [UUID(i) for i in old if i not in new]
    if added:
        changes.append(StructuralChange(code="lessons_added", lesson_ids=added))
    if removed:
        changes.append(StructuralChange(code="lessons_removed", lesson_ids=removed))

    common = [i for i in new if i in old]

    def placement(lessons: Mapping[str, tuple[str, dict[str, Any]]]) -> dict[str, tuple[str, int]]:
        seen: dict[str, int] = {}
        result: dict[str, tuple[str, int]] = {}
        for lesson_id, (module_id, _lesson) in lessons.items():
            if lesson_id in common:
                seen[module_id] = seen.get(module_id, 0) + 1
                result[lesson_id] = (module_id, seen[module_id])
        return result

    old_place, new_place = placement(old), placement(new)
    moved = [UUID(i) for i in common if old_place[i] != new_place[i]]
    if moved:
        changes.append(StructuralChange(code="lessons_reordered", lesson_ids=moved))

    def settings(lesson: Mapping[str, Any]) -> tuple[Any, ...]:
        return lesson["lesson_type"], lesson["is_required"], lesson["completion_threshold"]

    changed = [UUID(i) for i in common if settings(old[i][1]) != settings(new[i][1])]
    if changed:
        changes.append(StructuralChange(code="lesson_settings_changed", lesson_ids=changed))
    return changes
