import math
from fastapi import APIRouter, HTTPException, status

from app.core.config import settings
from app.core.metrics import current_tps, scaling_active_workers, scaling_recommendations_total
from app.models.metrics import ScalingAction, ScalingRecommendation
from app.services import redis_service

router = APIRouter(prefix="/api/v1/scaling", tags=["Scaling"])


@router.get(
    "/recommendation",
    response_model=ScalingRecommendation,
    summary="Scaling recommendation",
    description="Compute current throughput and active workers, then apply scaling rules: "
    "if throughput > workers*1500 → SCALE_UP, "
    "if throughput < workers*1000 → SCALE_DOWN, "
    "else NO_ACTION.  Recommended workers clamped to [1, 10].",
)
async def scaling_recommendation() -> ScalingRecommendation:
    try:
        _window, throughput = await redis_service.get_current_throughput()
        active_workers = await redis_service.get_active_worker_count()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to gather metrics: {exc}",
        )

    # Convert window-total to messages/second (TPS) before threshold comparisons.
    # The thresholds (SCALE_UP/DOWN_THROUGHPUT_PER_WORKER) are defined in TPS units.
    tps = throughput / _window

    # Treat 0 active workers as 1 for threshold math (avoid division by zero)
    effective_workers = max(active_workers, 1)

    up_threshold = effective_workers * settings.SCALE_UP_THROUGHPUT_PER_WORKER
    down_threshold = effective_workers * settings.SCALE_DOWN_THROUGHPUT_PER_WORKER

    if tps > up_threshold:
        action = ScalingAction.SCALE_UP
        desired = math.ceil(tps / settings.SCALE_UP_THROUGHPUT_PER_WORKER)
        desired = min(max(desired, settings.MIN_WORKERS), settings.MAX_WORKERS)
        reason = (
            f"tps {tps:.2f} > {effective_workers} workers * "
            f"{settings.SCALE_UP_THROUGHPUT_PER_WORKER} threshold → scale up"
        )
    elif tps < down_threshold:
        action = ScalingAction.SCALE_DOWN
        desired = (
            math.ceil(tps / settings.SCALE_DOWN_THROUGHPUT_PER_WORKER) if tps > 0 else 1
        )
        desired = min(max(desired, settings.MIN_WORKERS), settings.MAX_WORKERS)
        reason = (
            f"tps {tps:.2f} < {effective_workers} workers * "
            f"{settings.SCALE_DOWN_THROUGHPUT_PER_WORKER} threshold → scale down"
        )
    else:
        action = ScalingAction.NO_ACTION
        desired = min(
            max(effective_workers, settings.MIN_WORKERS), settings.MAX_WORKERS
        )
        reason = (
            f"tps {tps:.2f} within normal range for "
            f"{effective_workers} workers → no action needed"
        )

    scaling_recommendations_total.labels(action=action.value).inc()
    current_tps.set(tps)
    scaling_active_workers.set(active_workers)

    return ScalingRecommendation(
        current_throughput=tps,
        active_workers=active_workers,
        recommended_action=action,
        recommended_workers=desired,
        reason=reason,
    )
