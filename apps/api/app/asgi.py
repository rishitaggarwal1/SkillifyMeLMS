"""ASGI entrypoint: `uvicorn app.asgi:app`. Kept separate so importing `app.main` has no side
effects (tests and the OpenAPI exporter build their own app with explicit settings)."""

from app.main import create_app

app = create_app()
