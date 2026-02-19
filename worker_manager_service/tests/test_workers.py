"""
Tests for the Worker Manager Service - workers and metrics routers.

All Redis calls are mocked so no running infrastructure is needed.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def client():
    """Return a TestClient with lifespan Redis calls fully mocked."""
    with (
        patch("app.services.redis_service.init_pools", new_callable=AsyncMock),
        patch("app.services.redis_service.close_pools", new_callable=AsyncMock),
    ):
        from app.main import app

        with TestClient(app) as c:
            yield c


_FIXED_TS = datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc)

_WORKER_FIXTURE = {
    "worker_id": "worker-001",
    "status": "active",
    "registered_at": _FIXED_TS,
    "last_heartbeat": _FIXED_TS,
    "processed_count": 0,
}


def _make_worker_response(**overrides):
    from app.models.worker import WorkerResponse, WorkerStatus

    data = {**_WORKER_FIXTURE, **overrides}
    return WorkerResponse(**data)


# ── Health ─────────────────────────────────────────────────────────────────────


def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── GET /api/v1/workers ───────────────────────────────────────────────────────


def test_list_workers_empty(client):
    """No registered workers → 200 with empty list."""
    with patch(
        "app.services.redis_service.list_workers",
        new_callable=AsyncMock,
        return_value=[],
    ):
        resp = client.get("/api/v1/workers")

    assert resp.status_code == 200
    body = resp.json()
    assert body["workers"] == []
    assert body["count"] == 0


def test_list_workers_returns_all(client):
    """Two registered workers → 200 with count=2."""
    workers = [
        _make_worker_response(worker_id="worker-001"),
        _make_worker_response(worker_id="worker-002"),
    ]
    with patch(
        "app.services.redis_service.list_workers",
        new_callable=AsyncMock,
        return_value=workers,
    ):
        resp = client.get("/api/v1/workers")

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    worker_ids = {w["worker_id"] for w in body["workers"]}
    assert "worker-001" in worker_ids
    assert "worker-002" in worker_ids


# ── POST /api/v1/workers ──────────────────────────────────────────────────────


def test_register_worker_with_id(client):
    """POST with explicit worker_id → 201, worker_id echoed back."""
    with patch(
        "app.services.redis_service.register_worker",
        new_callable=AsyncMock,
        return_value=_make_worker_response(),
    ):
        resp = client.post("/api/v1/workers", json={"worker_id": "worker-001"})

    assert resp.status_code == 201
    assert resp.json()["worker_id"] == "worker-001"
    assert resp.json()["status"] == "active"


def test_register_worker_auto_id(client):
    """POST without worker_id → service generates one, still returns 201."""
    import uuid

    generated_id = str(uuid.uuid4())
    with patch(
        "app.services.redis_service.register_worker",
        new_callable=AsyncMock,
        return_value=_make_worker_response(worker_id=generated_id),
    ):
        resp = client.post("/api/v1/workers", json={})

    assert resp.status_code == 201
    assert resp.json()["worker_id"] == generated_id


def test_register_worker_redis_failure(client):
    """Redis error during registration → 503."""
    with patch(
        "app.services.redis_service.register_worker",
        side_effect=RuntimeError("Redis unavailable"),
    ):
        resp = client.post("/api/v1/workers", json={"worker_id": "worker-001"})

    assert resp.status_code == 503


# ── DELETE /api/v1/workers/{worker_id} ───────────────────────────────────────


def test_deregister_worker_success(client):
    """Worker exists → removed successfully, 200."""
    with patch(
        "app.services.redis_service.deregister_worker",
        new_callable=AsyncMock,
        return_value=True,
    ):
        resp = client.delete("/api/v1/workers/worker-001")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "removed"
    assert body["worker_id"] == "worker-001"


def test_deregister_worker_not_found(client):
    """Worker does not exist → 404."""
    with patch(
        "app.services.redis_service.deregister_worker",
        new_callable=AsyncMock,
        return_value=False,
    ):
        resp = client.delete("/api/v1/workers/ghost-worker")

    assert resp.status_code == 404


# ── PUT /api/v1/workers/{worker_id}/health ───────────────────────────────────


def test_update_worker_health_success(client):
    """Valid heartbeat → 200 with updated worker."""
    updated = _make_worker_response(processed_count=50)
    with patch(
        "app.services.redis_service.update_health",
        new_callable=AsyncMock,
        return_value=updated,
    ) as mock_fn:
        resp = client.put(
            "/api/v1/workers/worker-001/health",
            json={"processed_count": 50},
        )
        mock_fn.assert_awaited_once_with("worker-001", 50)

    assert resp.status_code == 200
    assert resp.json()["processed_count"] == 50
    assert resp.json()["status"] == "active"


def test_update_worker_health_no_body(client):
    """Empty body is allowed (processed_count is optional)."""
    with patch(
        "app.services.redis_service.update_health",
        new_callable=AsyncMock,
        return_value=_make_worker_response(),
    ) as mock_fn:
        resp = client.put("/api/v1/workers/worker-001/health", json={})
        mock_fn.assert_awaited_once_with("worker-001", None)

    assert resp.status_code == 200


def test_update_worker_health_not_found(client):
    """Worker not found → 404."""
    with patch(
        "app.services.redis_service.update_health",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = client.put("/api/v1/workers/ghost/health", json={})

    assert resp.status_code == 404


# ── GET /api/v1/metrics/throughput ───────────────────────────────────────────


def test_get_throughput_metrics(client):
    """Returns worker counts and TPS data."""
    metrics_data = {
        "total_workers": 3,
        "active_workers": 2,
        "idle_workers": 1,
        "error_workers": 0,
        "stale_workers": 0,
        "readings_ingested_total": 500,
        "throughput_window_seconds": 60,
        "readings_per_second": 8.33,
        "timestamp": _FIXED_TS.isoformat(),
    }
    with patch(
        "app.services.redis_service.get_throughput_metrics",
        new_callable=AsyncMock,
        return_value=metrics_data,
    ):
        resp = client.get("/api/v1/metrics/throughput")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_workers"] == 3
    assert body["active_workers"] == 2
    assert body["readings_ingested_total"] == 500
    assert body["readings_per_second"] == pytest.approx(8.33)


def test_get_throughput_metrics_custom_window(client):
    """Custom window query param is forwarded to the service."""
    metrics_data = {
        "total_workers": 0,
        "active_workers": 0,
        "idle_workers": 0,
        "error_workers": 0,
        "stale_workers": 0,
        "readings_ingested_total": 0,
        "throughput_window_seconds": 30,
        "readings_per_second": 0.0,
        "timestamp": _FIXED_TS.isoformat(),
    }
    with patch(
        "app.services.redis_service.get_throughput_metrics",
        new_callable=AsyncMock,
        return_value=metrics_data,
    ) as mock_fn:
        resp = client.get("/api/v1/metrics/throughput?window=30")
        mock_fn.assert_awaited_once_with(window_seconds=30)

    assert resp.status_code == 200


def test_get_throughput_metrics_redis_failure(client):
    """Redis error → 503."""
    with patch(
        "app.services.redis_service.get_throughput_metrics",
        side_effect=RuntimeError("Redis unavailable"),
    ):
        resp = client.get("/api/v1/metrics/throughput")

    assert resp.status_code == 503
