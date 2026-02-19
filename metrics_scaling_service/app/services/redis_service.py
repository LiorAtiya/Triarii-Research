import time
import redis.asyncio as aioredis
from datetime import datetime, timezone, timedelta

from app.core.config import settings

# ── Connection pools (singletons, initialised via FastAPI lifespan) ───────────
_ingestion_redis: aioredis.Redis | None = None  # DB 0 – throughput counters
_workers_redis: aioredis.Redis | None = None  # DB 1 – worker hashes


async def init_pools() -> None:
    """Create both Redis connection pools.  Called once at application startup."""
    global _ingestion_redis, _workers_redis
    _ingestion_redis = aioredis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.INGESTION_REDIS_DB,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=True,
        max_connections=20,
    )
    _workers_redis = aioredis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.WORKERS_REDIS_DB,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=True,
        max_connections=20,
    )


async def close_pools() -> None:
    """Gracefully drain and close all pools.  Called once at application shutdown."""
    global _ingestion_redis, _workers_redis
    if _ingestion_redis is not None:
        await _ingestion_redis.aclose()
        _ingestion_redis = None
    if _workers_redis is not None:
        await _workers_redis.aclose()
        _workers_redis = None


def get_ingestion_pool() -> aioredis.Redis:
    """Return the shared ingestion Redis client."""
    if _ingestion_redis is None:
        raise RuntimeError(
            "Redis ingestion pool not initialised – app must start via lifespan"
        )
    return _ingestion_redis


def get_workers_pool() -> aioredis.Redis:
    """Return the shared workers Redis client."""
    if _workers_redis is None:
        raise RuntimeError(
            "Redis workers pool not initialised – app must start via lifespan"
        )
    return _workers_redis


# ── Throughput ────────────────────────────────────────────────────────────────


async def get_current_throughput(
    window_seconds: int | None = None,
) -> tuple[int, float]:
    """Sum ``throughput:{unix_second}`` counters over the last *window* seconds.

    Returns ``(window_seconds, total_messages)``.
    The ingestion service writes ``INCR throughput:{unix_second}`` for every
    reading it stores, so we just need to sum the relevant keys.
    """
    window = window_seconds or settings.THROUGHPUT_WINDOW_SECONDS
    now_ts = int(time.time())

    client = get_ingestion_pool()
    # Build key list for the window
    keys = [f"throughput:{now_ts - i}" for i in range(window)]

    # MGET is O(n) and avoids a pipeline round-trip per key
    values = await client.mget(keys)
    total = sum(int(v) for v in values if v is not None)
    return window, total


# ── Active workers ────────────────────────────────────────────────────────────


async def get_active_worker_count() -> int:
    """Count workers whose ``last_heartbeat`` is within WORKER_STALE_SECONDS.

    Uses a Pipeline to fetch all ``last_heartbeat`` fields in a single
    round-trip instead of one ``HGET`` per worker.
    """
    client = get_workers_pool()
    worker_ids = await client.smembers("workers:all")
    if not worker_ids:
        return 0

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(seconds=settings.WORKER_STALE_SECONDS)

    async with client.pipeline(transaction=False) as pipe:
        for wid in worker_ids:
            pipe.hget(f"worker:{wid}", "last_heartbeat")
        heartbeats = await pipe.execute()

    active = 0
    for hb_raw in heartbeats:
        if hb_raw:
            try:
                if datetime.fromisoformat(hb_raw) >= stale_cutoff:
                    active += 1
            except (ValueError, TypeError):
                pass

    return active
