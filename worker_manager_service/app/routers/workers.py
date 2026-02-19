from fastapi import APIRouter, HTTPException, status

from app.models.worker import (
    DeregisterResponse,
    HealthUpdateRequest,
    RegisterWorkerRequest,
    WorkerListResponse,
    WorkerResponse,
)
from app.services import redis_service

router = APIRouter(prefix="/api/v1/workers", tags=["Workers"])


# ---------------------------------------------------------------------------
# GET /api/v1/workers  – list all workers
# ---------------------------------------------------------------------------
@router.get(
    "",
    response_model=WorkerListResponse,
    summary="List all registered workers and their status",
)
async def list_workers() -> WorkerListResponse:
    try:
        workers = await redis_service.list_workers()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to retrieve workers: {exc}",
        )
    return WorkerListResponse(workers=workers, count=len(workers))


# ---------------------------------------------------------------------------
# POST /api/v1/workers  – register a new worker
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=WorkerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new worker",
    description="Create a worker with status=active, processed_count=0. "
    "If worker_id is omitted a UUID is generated automatically.",
)
async def register_worker(body: RegisterWorkerRequest) -> WorkerResponse:
    try:
        worker = await redis_service.register_worker(body.worker_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to register worker: {exc}",
        )
    return worker


# ---------------------------------------------------------------------------
# DELETE /api/v1/workers/{worker_id}  – deregister a worker
# ---------------------------------------------------------------------------
@router.delete(
    "/{worker_id}",
    response_model=DeregisterResponse,
    summary="Deregister a worker",
    description="Remove the worker hash and its membership from the workers set.",
)
async def deregister_worker(worker_id: str) -> DeregisterResponse:
    try:
        removed = await redis_service.deregister_worker(worker_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to deregister worker: {exc}",
        )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Worker '{worker_id}' not found.",
        )
    return DeregisterResponse(status="removed", worker_id=worker_id)


# ---------------------------------------------------------------------------
# PUT /api/v1/workers/{worker_id}/health  – heartbeat / health update
# ---------------------------------------------------------------------------
@router.put(
    "/{worker_id}/health",
    response_model=WorkerResponse,
    summary="Worker heartbeat / health update",
    description="Sets status=active, refreshes last_heartbeat to now. "
    "Optionally accepts processed_count in the body.",
)
async def update_worker_health(
    worker_id: str,
    body: HealthUpdateRequest = HealthUpdateRequest(),
) -> WorkerResponse:
    try:
        worker = await redis_service.update_health(worker_id, body.processed_count)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to update worker health: {exc}",
        )
    if worker is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Worker '{worker_id}' not found.",
        )
    return worker
