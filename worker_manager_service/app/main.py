import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.core.tracing import setup_tracing
from app.core.metrics import workers_marked_stale_total
from app.routers import workers, metrics
from app.services import redis_service

setup_logging()
logger = structlog.get_logger(__name__)


async def _stale_worker_cleanup_loop() -> None:
    """Background task: periodically mark workers that missed their heartbeat as stale."""
    while True:
        await asyncio.sleep(settings.WORKER_STALE_SECONDS)
        try:
            marked = await redis_service.mark_stale_workers()
            if marked:
                workers_marked_stale_total.inc(marked)
                logger.info("stale_workers_marked", count=marked)
        except Exception as exc:
            logger.exception("stale_worker_cleanup_failed", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise both Redis connection pools on startup; close them on shutdown."""
    await redis_service.init_pools()
    cleanup_task = asyncio.create_task(_stale_worker_cleanup_loop())
    yield
    cleanup_task.cancel()
    await redis_service.close_pools()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Microservice Manager API – register, monitor, and manage IoT worker microservices.",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

setup_tracing(app)

app.include_router(workers.router)
app.include_router(metrics.router)

Instrumentator().instrument(app).expose(app)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "service": settings.APP_NAME}
