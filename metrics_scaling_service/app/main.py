from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.core.tracing import setup_tracing
from app.routers import scaling
from app.services import redis_service

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise both Redis connection pools on startup; close them on shutdown."""
    await redis_service.init_pools()
    yield
    await redis_service.close_pools()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Metrics & Scaling Service – provides throughput metrics and scaling recommendations.",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

setup_tracing(app)

app.include_router(scaling.router)

Instrumentator().instrument(app).expose(app)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "service": settings.APP_NAME}
