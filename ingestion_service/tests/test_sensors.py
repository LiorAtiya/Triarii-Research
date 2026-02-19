"""
Tests for the Ingestion Service - sensors router.

All Redis calls are mocked so no running Redis or Docker is required.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def client():
    """Return a TestClient with lifespan Redis calls fully mocked."""
    with (
        patch("app.services.redis_service.init_pool", new_callable=AsyncMock),
        patch("app.services.redis_service.close_pool", new_callable=AsyncMock),
    ):
        from app.main import app

        with TestClient(app) as c:
            yield c


_FIXED_TS = "2026-02-19T12:00:00+00:00"
_FIXED_DT = datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc)


# ── Health ─────────────────────────────────────────────────────────────────────


def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── POST /api/v1/sensors/data ──────────────────────────────────────────────────


def test_ingest_sensor_data_success(client):
    """Valid reading is persisted → 201 with sensor_id echoed back."""

    async def _store_side_effect(reading):
        reading.timestamp = _FIXED_DT  # simulate what the real service does

    with patch(
        "app.services.redis_service.store_reading",
        side_effect=_store_side_effect,
    ):
        resp = client.post(
            "/api/v1/sensors/data",
            json={
                "sensor_id": "sensor-001",
                "timestamp": _FIXED_TS,
                "readings": {"temperature": 22.5},
            },
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "ok"
    assert body["sensor_id"] == "sensor-001"


def test_ingest_sensor_data_redis_failure(client):
    """If Redis raises, the endpoint returns 503."""
    with patch(
        "app.services.redis_service.store_reading",
        side_effect=RuntimeError("Redis unavailable"),
    ):
        resp = client.post(
            "/api/v1/sensors/data",
            json={"sensor_id": "sensor-001", "readings": {"temperature": 22.5}},
        )

    assert resp.status_code == 503


def test_ingest_sensor_data_missing_readings(client):
    """Request without required `readings` field → 422 Unprocessable Entity."""
    resp = client.post(
        "/api/v1/sensors/data",
        json={"sensor_id": "sensor-001"},
    )
    assert resp.status_code == 422


# ── GET /api/v1/sensors/{sensor_id}/data ──────────────────────────────────────


def test_get_sensor_data_success(client):
    """Existing sensor with readings → 200 list."""
    mock_readings = [
        {
            "sensor_id": "sensor-001",
            "timestamp": _FIXED_DT,
            "readings": {"temperature": 22.5},
            "metadata": None,
        }
    ]
    with patch(
        "app.services.redis_service.get_latest_readings",
        new_callable=AsyncMock,
        return_value=mock_readings,
    ):
        resp = client.get("/api/v1/sensors/sensor-001/data")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["sensor_id"] == "sensor-001"


def test_get_sensor_data_not_found(client):
    """No readings for sensor → 404."""
    with patch(
        "app.services.redis_service.get_latest_readings",
        new_callable=AsyncMock,
        return_value=[],
    ):
        resp = client.get("/api/v1/sensors/unknown-sensor/data")

    assert resp.status_code == 404


def test_get_sensor_data_redis_failure(client):
    """Redis error on read → 503."""
    with patch(
        "app.services.redis_service.get_latest_readings",
        side_effect=RuntimeError("Redis unavailable"),
    ):
        resp = client.get("/api/v1/sensors/sensor-001/data")

    assert resp.status_code == 503


def test_get_sensor_data_limit_param(client):
    """Limit query parameter is passed down correctly."""
    mock_readings = [
        {
            "sensor_id": "sensor-001",
            "timestamp": _FIXED_DT,
            "readings": {"temperature": 20.0},
            "metadata": None,
        }
    ] * 3

    with patch(
        "app.services.redis_service.get_latest_readings",
        new_callable=AsyncMock,
        return_value=mock_readings,
    ) as mock_fn:
        client.get("/api/v1/sensors/sensor-001/data?limit=3")
        mock_fn.assert_awaited_once_with("sensor-001", 3)


# ── GET /api/v1/sensors/{sensor_id}/data/range ────────────────────────────────


def test_get_sensor_data_range_invalid_dates(client):
    """end < start → 422."""
    with patch(
        "app.services.redis_service.get_readings_in_range",
        new_callable=AsyncMock,
        return_value=[],
    ):
        resp = client.get(
            "/api/v1/sensors/sensor-001/data/range"
            "?start=2026-02-19T23:00:00&end=2026-02-19T00:00:00"
        )
    assert resp.status_code == 422


def test_get_sensor_data_range_not_found(client):
    """Valid range, no data → 404."""
    with patch(
        "app.services.redis_service.get_readings_in_range",
        new_callable=AsyncMock,
        return_value=[],
    ):
        resp = client.get(
            "/api/v1/sensors/sensor-001/data/range"
            "?start=2026-02-19T00:00:00&end=2026-02-19T23:00:00"
        )
    assert resp.status_code == 404


# ── GET /api/v1/sensors ───────────────────────────────────────────────────────


def test_list_sensors(client):
    """List endpoint returns sensor names and count."""
    with patch(
        "app.services.redis_service.list_sensors",
        new_callable=AsyncMock,
        return_value=(0, ["sensor-001", "sensor-002"]),
    ):
        resp = client.get("/api/v1/sensors")

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert "sensor-001" in body["sensors"]
