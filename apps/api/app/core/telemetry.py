"""OpenTelemetry tracing. Enabled only when OTEL_EXPORTER_OTLP_ENDPOINT is configured."""

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings

# Not traced. Webhook URLs carry a shared secret in the path, which spans would export as
# http.target / url.full.
EXCLUDED_URLS = "health/live,health/ready,api/v1/webhooks/"


def configure_tracing(app: FastAPI, settings: Settings) -> None:
    if not settings.otel_exporter_otlp_endpoint:
        return
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.service_name,
                "deployment.environment": settings.environment,
            }
        )
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces")
        )
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls=EXCLUDED_URLS)
    RedisInstrumentor().instrument()


def instrument_engine(engine: AsyncEngine, settings: Settings) -> None:
    if settings.otel_exporter_otlp_endpoint:
        SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
