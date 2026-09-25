"""Domain modules of the modular monolith.

Each module is a package `app/modules/<module>/` containing:

    router.py      FastAPI routes (thin: parse input, call the service, shape output)
    schemas.py     Pydantic request/response models
    models.py      SQLAlchemy models owned by this module
    service.py     Business logic + authorization. The ONLY public interface of the module.
    repository.py  Data access for this module's own tables
    tests/         pytest tests for the module

Rules:
- Other modules call `service.py` functions only. Never import another module's repository or models
  to query its tables.
- Tenant-owned tables use `TenantMixin` and get an RLS policy in their migration.
- Register the module's models in `app/db/models.py` so Alembic sees them.
"""
