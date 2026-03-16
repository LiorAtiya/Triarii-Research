import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from prometheus_fastapi_instrumentator import Instrumentator

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.core.tracing import setup_tracing
from app.routers import sensors
from app.services import redis_service, timescale_service
from app.services import stream_consumer

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise Redis + TimescaleDB pools and stream consumer on startup."""
    await redis_service.init_pool()
    await timescale_service.init_pool()
    consumer_task = asyncio.create_task(stream_consumer.run())
    yield
    consumer_task.cancel()
    await timescale_service.close_pool()
    await redis_service.close_pool()


app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
    version=settings.APP_VERSION,
    description="IoT sensor data ingestion service backed by Redis.",
    docs_url="/docs",
    redoc_url="/redoc",
)

setup_tracing(app)

# Register routers
app.include_router(sensors.router)

# Expose /metrics — HTTP metrics (latency, status codes) auto-instrumented
# Business metrics (duplicates, stream events) are updated in sensors.py + stream_consumer.py
Instrumentator().instrument(app).expose(app)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "service": settings.APP_NAME}
