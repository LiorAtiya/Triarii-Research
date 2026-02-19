import time
import uuid
import redis.asyncio as aioredis
from datetime import datetime, timezone
from typing import List, Optional

from app.core.config import settings
from app.models.worker import WorkerResponse, WorkerStatus

# ── Redis key layout ──────────────────────────────────────────────────────────
# Hash   worker:{worker_id}          – all fields for one worker
# Set    workers:all                 – set of all worker IDs
# ─────────────────────────────────────────────────────────────────────────────
WORKERS_SET = "workers:all"

# ── Connection pools (singletons, initialised via FastAPI lifespan) ───────────
_workers_redis: aioredis.Redis | None = None  # DB 1 – worker data
_ingestion_redis: aioredis.Redis | None = None  # DB 0 – ingestion data (read-only)


async def init_pools() -> None:
    """Create both Redis connection pools.  Called once at application startup."""
    global _workers_redis, _ingestion_redis
    _workers_redis = aioredis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=True,
        max_connections=50,
    )
    _ingestion_redis = aioredis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=0,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=True,
        max_connections=20,
    )


async def close_pools() -> None:
    """Gracefully drain and close all pools.  Called once at application shutdown."""
    global _workers_redis, _ingestion_redis
    if _workers_redis is not None:
        await _workers_redis.aclose()
        _workers_redis = None
    if _ingestion_redis is not None:
        await _ingestion_redis.aclose()
        _ingestion_redis = None


def get_workers_pool() -> aioredis.Redis:
    """Return the shared workers Redis client."""
    if _workers_redis is None:
        raise RuntimeError(
            "Redis workers pool not initialised – app must start via lifespan"
        )
    return _workers_redis


def get_ingestion_pool() -> aioredis.Redis:
    """Return the shared ingestion Redis client (read-only)."""
    if _ingestion_redis is None:
        raise RuntimeError(
            "Redis ingestion pool not initialised – app must start via lifespan"
        )
    return _ingestion_redis


# ── Helpers ───────────────────────────────────────────────────────────────────


def _worker_key(worker_id: str) -> str:
    return f"worker:{worker_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_response(data: dict) -> WorkerResponse:
    """Convert a raw Redis Hash (all values are strings) to a typed WorkerResponse.

    Explicit casts are required because Redis stores everything as strings:
    * ``processed_count`` → int
    * ``registered_at`` / ``last_heartbeat`` → datetime (via ISO-8601 parsing)
    """
    return WorkerResponse(
        worker_id=data["worker_id"],
        status=WorkerStatus(data["status"]),
        registered_at=datetime.fromisoformat(data["registered_at"]),  # str → datetime
        last_heartbeat=datetime.fromisoformat(data["last_heartbeat"]),  # str → datetime
        processed_count=int(data.get("processed_count", 0)),  # str → int
    )


# ── CRUD ──────────────────────────────────────────────────────────────────────


async def register_worker(worker_id: Optional[str] = None) -> WorkerResponse:
    """Create a new worker. If *worker_id* is not supplied a UUID is generated."""
    client = get_workers_pool()
    wid = worker_id or str(uuid.uuid4())
    now = _now_iso()
    data = {
        "worker_id": wid,
        "status": WorkerStatus.ACTIVE.value,
        "registered_at": now,
        "last_heartbeat": now,
        "processed_count": "0",
    }
    async with client.pipeline(transaction=True) as pipe:
        pipe.hset(_worker_key(wid), mapping=data)
        pipe.sadd(WORKERS_SET, wid)
        await pipe.execute()
    return _row_to_response(data)


async def list_workers() -> List[WorkerResponse]:
    """Return every registered worker, sorted by registration time.

    Uses a Pipeline to fetch all hashes in a single round-trip instead of
    issuing one ``HGETALL`` per worker, which would block on network latency
    for every iteration.
    """
    client = get_workers_pool()
    worker_ids = await client.smembers(WORKERS_SET)
    if not worker_ids:
        return []
    async with client.pipeline(transaction=False) as pipe:
        for wid in worker_ids:
            pipe.hgetall(_worker_key(wid))
        raw_list = await pipe.execute()
    results = [_row_to_response(raw) for raw in raw_list if raw]
    return sorted(results, key=lambda w: w.registered_at)


async def get_worker(worker_id: str) -> Optional[WorkerResponse]:
    """Return a single worker or None."""
    client = get_workers_pool()
    raw = await client.hgetall(_worker_key(worker_id))
    if not raw:
        return None
    return _row_to_response(raw)


async def deregister_worker(worker_id: str) -> bool:
    """Delete a worker. Returns True if it existed, False otherwise."""
    client = get_workers_pool()
    exists = await client.exists(_worker_key(worker_id))
    if not exists:
        return False
    async with client.pipeline(transaction=True) as pipe:
        pipe.delete(_worker_key(worker_id))
        pipe.srem(WORKERS_SET, worker_id)
        await pipe.execute()
    return True


async def update_health(
    worker_id: str,
    processed_count: Optional[int] = None,
) -> Optional[WorkerResponse]:
    """Heartbeat: set status=active, refresh last_heartbeat, optionally update processed_count.

    Returns the full updated worker object or None if the worker does not exist.
    """
    client = get_workers_pool()
    exists = await client.exists(_worker_key(worker_id))
    if not exists:
        return None

    now = _now_iso()
    updates: dict = {
        "status": WorkerStatus.ACTIVE.value,
        "last_heartbeat": now,
    }
    if processed_count is not None:
        updates["processed_count"] = str(processed_count)

    await client.hset(_worker_key(worker_id), mapping=updates)

    # Return the full refreshed object
    raw = await client.hgetall(_worker_key(worker_id))
    return _row_to_response(raw)


# ── Metrics ───────────────────────────────────────────────────────────────────


async def get_throughput_metrics(window_seconds: int = 60) -> dict:
    """Collect worker status counts, total readings, and TPS rate.

    The ingestion service increments ``throughput:{unix_second}`` counters for
    every inbound reading.  We fetch the last *window_seconds* buckets in a
    single ``MGET`` call and divide the sum by the window to get TPS.

    Note on time drift: ``int(time.time())`` is used on both the ingestion and
    metrics sides.  If the two hosts' clocks drift by a few seconds a small
    number of buckets may be missed, but for a homework/demo environment this
    is an acceptable trade-off.
    """
    workers_client = get_workers_pool()
    ingestion_client = get_ingestion_pool()

    worker_ids = await workers_client.smembers(WORKERS_SET)
    counts = {s: 0 for s in WorkerStatus}
    if worker_ids:
        async with workers_client.pipeline(transaction=False) as pipe:
            for wid in worker_ids:
                pipe.hget(_worker_key(wid), "status")
            statuses = await pipe.execute()
        for raw_status in statuses:
            if raw_status:
                try:
                    counts[WorkerStatus(raw_status)] += 1
                except ValueError:
                    pass

    # Count total sensor readings across all sensors in DB 0
    sensor_ids = await ingestion_client.smembers("sensors")
    readings_total = 0
    if sensor_ids:
        async with ingestion_client.pipeline(transaction=False) as pipe:
            for sid in sensor_ids:
                pipe.zcard(f"sensor:{sid}:readings")
            readings_total = sum(await pipe.execute())

    # ── TPS: sum per-second buckets over the window via a single MGET ────────
    now = int(time.time())
    tp_keys = [f"throughput:{now - i}" for i in range(window_seconds)]
    raw_counts = await ingestion_client.mget(*tp_keys)
    window_total = sum(int(v) for v in raw_counts if v is not None)
    readings_per_second = window_total / window_seconds

    return {
        "total_workers": len(worker_ids),
        "active_workers": counts[WorkerStatus.ACTIVE],
        "idle_workers": counts[WorkerStatus.IDLE],
        "error_workers": counts[WorkerStatus.ERROR],
        "stale_workers": counts[WorkerStatus.STALE],
        "readings_ingested_total": readings_total,
        "throughput_window_seconds": window_seconds,
        "readings_per_second": readings_per_second,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
