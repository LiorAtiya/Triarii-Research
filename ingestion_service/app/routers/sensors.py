from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.models.sensor import (
    IngestResponse,
    SensorListResponse,
    SensorReading,
    SensorReadingResponse,
)
from app.services import redis_service
from app.core.config import settings

router = APIRouter(prefix="/api/v1/sensors", tags=["Sensors"])


# ---------------------------------------------------------------------------
# POST /api/v1/sensors/data
# ---------------------------------------------------------------------------
@router.post(
    "/data",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest sensor reading",
    description="Accept a sensor reading and persist it in Redis.",
)
async def ingest_sensor_data(reading: SensorReading) -> IngestResponse:
    try:
        stored = await redis_service.publish_reading(reading)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to store reading: {exc}",
        )
    return IngestResponse(
        status="ok" if stored else "duplicate",
        sensor_id=reading.sensor_id,
        timestamp=reading.timestamp,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/sensors/{sensor_id}/data
# ---------------------------------------------------------------------------
@router.get(
    "/{sensor_id}/data",
    response_model=List[SensorReadingResponse],
    summary="Retrieve latest readings for a sensor",
    description="Return the most recent readings for the given sensor. "
    "Use the `limit` query parameter to control how many records are returned (default 10).",
)
async def get_sensor_data(
    sensor_id: str,
    limit: int = Query(
        default=settings.DEFAULT_LATEST_LIMIT,
        ge=1,
        le=1000,
        description="Number of latest readings to return",
    ),
) -> List[SensorReadingResponse]:
    try:
        readings = await redis_service.get_latest_readings(sensor_id, limit)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to retrieve readings: {exc}",
        )
    if not readings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No readings found for sensor '{sensor_id}'.",
        )
    return readings


# ---------------------------------------------------------------------------
# GET /api/v1/sensors/{sensor_id}/data/range
# ---------------------------------------------------------------------------
@router.get(
    "/{sensor_id}/data/range",
    response_model=List[SensorReadingResponse],
    summary="Retrieve readings within a time range",
    description="Return readings for the given sensor between `start` and `end` (inclusive), "
    "ordered newest first. Use `limit` and `offset` for pagination. "
    "Both time parameters are ISO-8601 UTC datetimes.",
)
async def get_sensor_data_range(
    sensor_id: str,
    start: datetime = Query(
        ..., description="Range start (ISO-8601 UTC), e.g. 2026-02-19T00:00:00"
    ),
    end: datetime = Query(
        ..., description="Range end   (ISO-8601 UTC), e.g. 2026-02-19T23:59:59"
    ),
    limit: int = Query(
        1000,
        ge=1,
        le=10000,
        description="Maximum number of readings to return (default 1000, max 10000).",
    ),
    offset: int = Query(
        0, ge=0, description="Number of readings to skip for pagination."
    ),
) -> List[SensorReadingResponse]:
    if end < start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`end` must be greater than or equal to `start`.",
        )
    try:
        readings = await redis_service.get_readings_in_range(
            sensor_id, start, end, limit=limit, offset=offset
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to retrieve readings: {exc}",
        )
    if not readings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No readings found for sensor '{sensor_id}' in the given time range.",
        )
    return readings


# ---------------------------------------------------------------------------
# GET /api/v1/sensors
# ---------------------------------------------------------------------------
@router.get(
    "",
    response_model=SensorListResponse,
    summary="List all registered sensors",
    description="Return a page of sensors using cursor-based pagination backed by SSCAN. "
    "Pass the returned `next_cursor` as `cursor` in the next request. "
    "A `next_cursor` of `0` in the response means there are no more pages.",
)
async def list_sensors(
    cursor: int = Query(
        0, ge=0, description="Pagination cursor (0 to start from the beginning)."
    ),
    count: int = Query(
        1000, ge=1, le=10000, description="Hint for number of sensors per page."
    ),
) -> SensorListResponse:
    try:
        next_cursor, sensors = await redis_service.list_sensors(
            cursor=cursor, count=count
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to retrieve sensor list: {exc}",
        )
    return SensorListResponse(
        sensors=sensors, count=len(sensors), next_cursor=next_cursor
    )
