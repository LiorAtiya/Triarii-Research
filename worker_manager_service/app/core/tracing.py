from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor

from app.core.config import settings


def setup_tracing(app) -> None:
    """Configure OpenTelemetry to export traces to Jaeger via OTLP.

    Auto-instruments FastAPI (HTTP spans) and Redis.
    """
    resource = Resource.create({"service.name": settings.APP_NAME})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_ENDPOINT))
    )
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    RedisInstrumentor().instrument()
