from fastapi import APIRouter, HTTPException, Query, status
from datetime import datetime

from app.models.worker import ThroughputMetrics
from app.services import redis_service

router = APIRouter(prefix="/api/v1/metrics", tags=["Metrics"])


# ---------------------------------------------------------------------------
# GET /api/v1/metrics/throughput
# ---------------------------------------------------------------------------
@router.get(
    "/throughput",
    response_model=ThroughputMetrics,
    summary="Get current throughput metrics",
    description=(
        "Returns worker status counts and the total number of sensor readings "
        "that have been ingested, plus the average ingestion rate (TPS) over a "
        "configurable time window."
    ),
)
async def get_throughput(
    window: int = Query(
        60,
        ge=1,
        le=3600,
        description="Window size in seconds for the TPS calculation (default 60).",
    ),
) -> ThroughputMetrics:
    try:
        data = await redis_service.get_throughput_metrics(window_seconds=window)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to retrieve metrics: {exc}",
        )
    return ThroughputMetrics(
        total_workers=data["total_workers"],
        active_workers=data["active_workers"],
        idle_workers=data["idle_workers"],
        error_workers=data["error_workers"],
        stale_workers=data["stale_workers"],
        readings_ingested_total=data["readings_ingested_total"],
        throughput_window_seconds=data["throughput_window_seconds"],
        readings_per_second=data["readings_per_second"],
        timestamp=datetime.fromisoformat(data["timestamp"]),
    )
