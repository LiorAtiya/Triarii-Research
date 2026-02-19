"""
Tests for the Metrics & Scaling Service - scaling router.

Scaling logic is tested with mocked Redis so no running infrastructure is needed.
"""

import pytest
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


# ── Health ─────────────────────────────────────────────────────────────────────


def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── GET /api/v1/scaling/recommendation ───────────────────────────────────────
#
# Settings defaults:  SCALE_UP_THROUGHPUT_PER_WORKER=1500
#                     SCALE_DOWN_THROUGHPUT_PER_WORKER=1000
#                     THROUGHPUT_WINDOW_SECONDS=10
#
# tps  = total_messages / window
# SCALE_UP   when  tps > workers * 1500
# SCALE_DOWN when  tps < workers * 1000
# NO_ACTION  otherwise


def test_recommendation_scale_up(client):
    """
    window=10, total=30 000 → tps=3000
    1 active worker, threshold=1500 → SCALE_UP
    """
    with (
        patch(
            "app.services.redis_service.get_current_throughput",
            new_callable=AsyncMock,
            return_value=(10, 30_000),
        ),
        patch(
            "app.services.redis_service.get_active_worker_count",
            new_callable=AsyncMock,
            return_value=1,
        ),
    ):
        resp = client.get("/api/v1/scaling/recommendation")

    assert resp.status_code == 200
    body = resp.json()
    assert body["recommended_action"] == "SCALE_UP"
    assert body["current_throughput"] == pytest.approx(3000.0)
    assert body["active_workers"] == 1
    assert body["recommended_workers"] >= 1


def test_recommendation_scale_down(client):
    """
    window=10, total=8 000 → tps=800
    2 active workers, down_threshold=2000 → SCALE_DOWN
    """
    with (
        patch(
            "app.services.redis_service.get_current_throughput",
            new_callable=AsyncMock,
            return_value=(10, 8_000),
        ),
        patch(
            "app.services.redis_service.get_active_worker_count",
            new_callable=AsyncMock,
            return_value=2,
        ),
    ):
        resp = client.get("/api/v1/scaling/recommendation")

    assert resp.status_code == 200
    body = resp.json()
    assert body["recommended_action"] == "SCALE_DOWN"
    assert body["active_workers"] == 2


def test_recommendation_no_action(client):
    """
    window=10, total=12 000 → tps=1200
    1 active worker; 1000 <= 1200 <= 1500 → NO_ACTION
    """
    with (
        patch(
            "app.services.redis_service.get_current_throughput",
            new_callable=AsyncMock,
            return_value=(10, 12_000),
        ),
        patch(
            "app.services.redis_service.get_active_worker_count",
            new_callable=AsyncMock,
            return_value=1,
        ),
    ):
        resp = client.get("/api/v1/scaling/recommendation")

    assert resp.status_code == 200
    body = resp.json()
    assert body["recommended_action"] == "NO_ACTION"


def test_recommendation_no_workers(client):
    """
    0 active workers → effective_workers treated as 1.
    SCALE_UP when tps exceeds per-worker threshold.
    """
    with (
        patch(
            "app.services.redis_service.get_current_throughput",
            new_callable=AsyncMock,
            return_value=(10, 20_000),
        ),
        patch(
            "app.services.redis_service.get_active_worker_count",
            new_callable=AsyncMock,
            return_value=0,
        ),
    ):
        resp = client.get("/api/v1/scaling/recommendation")

    assert resp.status_code == 200
    assert resp.json()["recommended_action"] == "SCALE_UP"


def test_recommendation_recommended_workers_clamped(client):
    """Recommended workers must stay within [MIN_WORKERS=1, MAX_WORKERS=10]."""
    with (
        patch(
            "app.services.redis_service.get_current_throughput",
            new_callable=AsyncMock,
            return_value=(10, 10_000_000),  # absurdly high → would want 666+ workers
        ),
        patch(
            "app.services.redis_service.get_active_worker_count",
            new_callable=AsyncMock,
            return_value=1,
        ),
    ):
        resp = client.get("/api/v1/scaling/recommendation")

    assert resp.status_code == 200
    assert resp.json()["recommended_workers"] <= 10


def test_recommendation_redis_failure(client):
    """If Redis raises, the endpoint must return 503."""
    with patch(
        "app.services.redis_service.get_current_throughput",
        side_effect=RuntimeError("Redis unavailable"),
    ):
        resp = client.get("/api/v1/scaling/recommendation")

    assert resp.status_code == 503
