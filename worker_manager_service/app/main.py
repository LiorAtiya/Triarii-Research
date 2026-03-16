import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.routers import workers, metrics
from app.services import redis_service

logger = logging.getLogger(__name__)


async def _stale_worker_cleanup_loop() -> None:
    """Background task: periodically mark workers that missed their heartbeat as stale."""
    while True:
        await asyncio.sleep(settings.WORKER_STALE_SECONDS)
        try:
            marked = await redis_service.mark_stale_workers()
            if marked:
                logger.info("Stale-worker cleanup: marked %d worker(s) as stale", marked)
        except Exception:
            logger.exception("Stale-worker cleanup failed")


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

app.include_router(workers.router)
app.include_router(metrics.router)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "service": settings.APP_NAME}
