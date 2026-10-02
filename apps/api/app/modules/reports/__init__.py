"""Read-only progress reports for staff (Phase 2.5). This module owns no tables: it composes the
identity, courses, enrollments and assignments service interfaces, so it has no models.py or
repository.py.

STOPGAP: these reports read the OLTP tables (enrollments, lesson_progress, submissions) page by
page. Phase 5 replaces them with ClickHouse analytics fed by the learning events; keep the API
shape so the web app doesn't change when the source does."""
