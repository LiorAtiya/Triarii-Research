from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.routers import sensors
from app.services import redis_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise the Redis connection pool on startup; close it on shutdown."""
    await redis_service.init_pool()
    yield
    await redis_service.close_pool()


app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
    version=settings.APP_VERSION,
    description="IoT sensor data ingestion service backed by Redis.",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Register routers
app.include_router(sensors.router)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "service": settings.APP_NAME}
