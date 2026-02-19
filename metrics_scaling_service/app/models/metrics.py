from pydantic import BaseModel, Field
from enum import Enum


class ScalingAction(str, Enum):
    SCALE_UP = "SCALE_UP"
    SCALE_DOWN = "SCALE_DOWN"
    NO_ACTION = "NO_ACTION"


class ScalingRecommendation(BaseModel):
    current_throughput: float = Field(
        ...,
        description="Average ingestion rate over the measurement window (messages/second, TPS)",
    )
    active_workers: int = Field(..., description="Workers with a fresh heartbeat")
    recommended_action: ScalingAction
    recommended_workers: int = Field(
        ..., description="Suggested worker count (clamped to min/max)"
    )
    reason: str = Field(..., description="Human-readable explanation")

    model_config = {
        "json_schema_extra": {
            "example": {
                "current_throughput": 420.0,
                "active_workers": 2,
                "recommended_action": "SCALE_UP",
                "recommended_workers": 3,
                "reason": "tps 420.0 > 2 workers * 1500 threshold → scale up",
            }
        }
    }
