from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, List
from datetime import datetime


class SensorReading(BaseModel):
    """Payload sent by an IoT sensor."""

    sensor_id: str = Field(..., description="Unique identifier for the sensor")
    timestamp: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp of the reading. Defaults to server time if omitted.",
    )
    readings: Dict[str, float] = Field(
        ...,
        description="Key-value pairs of measurement names to numeric values, "
        'e.g. {"temperature": 22.5, "humidity": 60.1}',
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional free-form metadata (location, firmware version, etc.)",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "sensor_id": "sensor-001",
                "timestamp": "2024-01-15T10:30:00Z",
                "readings": {"temperature": 23.5, "humidity": 65.2},
                "metadata": {"location": "warehouse-A", "device_type": "DHT22"},
            }
        }
    }


class SensorReadingResponse(BaseModel):
    sensor_id: str
    timestamp: datetime
    readings: Dict[str, float]
    metadata: Optional[Dict[str, Any]] = None


class SensorListResponse(BaseModel):
    sensors: List[str]
    count: int
    next_cursor: int = 0  # 0 means no more pages


class IngestResponse(BaseModel):
    status: str
    sensor_id: str
    timestamp: datetime
