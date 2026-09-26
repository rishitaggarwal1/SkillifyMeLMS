"""A content-sharing world for the course RLS tests (created once per module, as the owner role).

Organizations
  P  publisher (is_content_publisher) - owns courses K, K2, K3, K4
  C  college with batches CSE and ECE - owns private course Q
  O  unrelated college

Assignments
  K   org grant P->C (publisher-made) + C's org_admin narrowed it to CSE
  K2  publisher assigned directly to C's ECE batch (publisher-made batch row)
  K3  assigned to P's own batch (owner-org students)
  K4  org grant P->C only (no batch rows)
  Q   none (private to C)
"""

from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.courses.models import Course, CourseAssignment, CourseVersion, Lesson
from app.modules.identity.models import Batch, Organization, User
from tests.factories import Factory


@dataclass
class ContentWorld:
    p: Organization
    c: Organization
    o: Organization
    p_author: User  # P instructor
    p_admin: User  # P org_admin
    p_lab: User  # P lab_author
    p_student: User  # P student in p_batch
    c_admin: User
    c_instructor: User
    cse: User  # C student in CSE
    ece: User  # C student in ECE
    o_admin: User
    o_student: User
    platform_admin: User
    p_batch: Batch
    cse_batch: Batch
    ece_batch: Batch
    o_batch: Batch
    k: Course
    k2: Course
    k3: Course
    k4: Course
    q: Course
    k_lesson: Lesson
    k_version: CourseVersion
    q_version: CourseVersion
    grant_c: CourseAssignment  # K -> C (publisher-made grant)
    narrow_cse: CourseAssignment  # K -> CSE, made by C's org_admin
    k2_ece: CourseAssignment  # K2 -> ECE, publisher-made batch row


@pytest.fixture(scope="module")
async def cw(owner_sessionmaker: async_sessionmaker[AsyncSession]) -> ContentWorld:
    f = Factory(owner_sessionmaker)
    p = await f.org(name="Publisher", publisher=True)
    c, o = await f.org(name="College C"), await f.org(name="College O")

    p_batch = await f.batch(p)
    cse_batch, ece_batch = await f.batch(c, name="CSE"), await f.batch(c, name="ECE")
    o_batch = await f.batch(o)
    p_student, cse, ece = (
        await f.member(p, "student"),
        await f.member(c, "student"),
        await f.member(c, "student"),
    )
    o_student = await f.member(o, "student")
    await f.add_to_batch(p_batch, p_student)
    await f.add_to_batch(cse_batch, cse)
    await f.add_to_batch(ece_batch, ece)
    await f.add_to_batch(o_batch, o_student)

    k, k2, k3, k4 = [await f.course(p, title=t) for t in ("K", "K2", "K3", "K4")]
    q = await f.course(c, title="Q")
    k_module = await f.module(k)
    k_lesson = await f.lesson(k_module, lesson_type="video")
    k_version = await f.version(k, k_lesson)
    for course in (k2, k3, k4):
        await f.version(course, await f.lesson(await f.module(course)))
    q_version = await f.version(q, await f.lesson(await f.module(q)))

    grant_c = await f.assignment(k, c)
    narrow_cse = await f.assignment(k, c, batch=cse_batch, by_receiver=True, parent=grant_c)
    k2_ece = await f.assignment(k2, c, batch=ece_batch)
    await f.assignment(k3, p, batch=p_batch)
    await f.assignment(k4, c)

    return ContentWorld(
        p=p, c=c, o=o,
        p_author=await f.member(p, "instructor"),
        p_admin=await f.member(p, "org_admin"),
        p_lab=await f.member(p, "lab_author"),
        p_student=p_student,
        c_admin=await f.member(c, "org_admin"),
        c_instructor=await f.member(c, "instructor"),
        cse=cse, ece=ece,
        o_admin=await f.member(o, "org_admin"),
        o_student=o_student,
        platform_admin=await f.user(),
        p_batch=p_batch, cse_batch=cse_batch, ece_batch=ece_batch, o_batch=o_batch,
        k=k, k2=k2, k3=k3, k4=k4, q=q,
        k_lesson=k_lesson, k_version=k_version, q_version=q_version,
        grant_c=grant_c, narrow_cse=narrow_cse, k2_ece=k2_ece,
    )  # fmt: skip
