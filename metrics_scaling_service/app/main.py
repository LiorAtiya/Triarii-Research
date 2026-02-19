from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.routers import scaling
from app.services import redis_service


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

app.include_router(scaling.router)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "service": settings.APP_NAME}
