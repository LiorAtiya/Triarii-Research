from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class WorkerStatus(str, Enum):
    ACTIVE = "active"
    IDLE = "idle"
    ERROR = "error"
    STALE = "stale"


# ---------- Request bodies ----------


class RegisterWorkerRequest(BaseModel):
    """Body for POST /api/v1/workers.  worker_id is optional (UUID generated if omitted)."""

    worker_id: Optional[str] = Field(
        default=None,
        description="Optional worker ID. A UUID is generated automatically if omitted.",
    )

    model_config = {"json_schema_extra": {"example": {"worker_id": "worker-001"}}}


class HealthUpdateRequest(BaseModel):
    """Body for PUT /api/v1/workers/{worker_id}/health.  All fields optional."""

    processed_count: Optional[int] = Field(
        default=None,
        ge=0,
        description="Optionally report the current processed_count.",
    )

    model_config = {"json_schema_extra": {"example": {"processed_count": 15000}}}


# ---------- Response models ----------


class WorkerResponse(BaseModel):
    """The full worker object returned by every endpoint."""

    worker_id: str
    status: WorkerStatus
    registered_at: datetime
    last_heartbeat: datetime
    processed_count: int

    model_config = {
        "json_schema_extra": {
            "example": {
                "worker_id": "worker-001",
                "status": "active",
                "registered_at": "2024-01-15T10:00:00Z",
                "last_heartbeat": "2024-01-15T10:30:00Z",
                "processed_count": 15000,
            }
        }
    }


class WorkerListResponse(BaseModel):
    workers: List[WorkerResponse]
    count: int


class DeregisterResponse(BaseModel):
    status: str
    worker_id: str


# ---------- Metrics ----------


class ThroughputMetrics(BaseModel):
    total_workers: int
    active_workers: int
    idle_workers: int
    error_workers: int
    stale_workers: int
    readings_ingested_total: int = Field(
        ..., description="Total sensor readings stored in the ingestion service (DB 0)"
    )
    # ── Throughput rate ───────────────────────────────────────────────────────
    throughput_window_seconds: int = Field(
        ..., description="The time window (seconds) used to compute the TPS rate."
    )
    readings_per_second: float = Field(
        ...,
        description=(
            "Average ingestion rate over the last `throughput_window_seconds` seconds "
            "(readings / second). Computed from per-second counters written by the "
            "ingestion service."
        ),
    )
    timestamp: datetime
