import json
import redis.asyncio as aioredis
from datetime import datetime, timezone
from typing import List

from app.core.config import settings
from app.models.sensor import SensorReading, SensorReadingResponse

# ── Redis key layout ──────────────────────────────────────────────────────────
# Sorted Set  sensor:{sensor_id}:readings   score = unix-ts, member = JSON blob
# Set         sensors                        all known sensor IDs
# String      throughput:{unix_second}       per-second ingestion counter (TTL)
# ─────────────────────────────────────────────────────────────────────────────

SENSORS_SET_KEY = "sensors"
THROUGHPUT_TTL_SECONDS = 3600  # keep per-second counters for 1 hour

# ── Connection pool (singleton, initialised via FastAPI lifespan) ─────────────
_redis: aioredis.Redis | None = None


async def init_pool() -> None:
    """Create the shared Redis connection pool.  Called once at application startup."""
    global _redis
    _redis = aioredis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=True,
        max_connections=50,
    )


async def close_pool() -> None:
    """Gracefully drain and close the pool.  Called once at application shutdown."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def get_pool() -> aioredis.Redis:
    """Return the shared Redis client.  Raises if the pool has not been initialised."""
    if _redis is None:
        raise RuntimeError("Redis pool not initialised – app must start via lifespan")
    return _redis


# ── Key helpers ───────────────────────────────────────────────────────────────


def _readings_key(sensor_id: str) -> str:
    return f"sensor:{sensor_id}:readings"


def _throughput_key(ts: datetime) -> str:
    """Return a throughput bucket key for the given second."""
    return f"throughput:{int(ts.timestamp())}"


# ── Store ─────────────────────────────────────────────────────────────────────


async def store_reading(reading: SensorReading) -> None:
    """Persist a sensor reading and bump the throughput counter.

    * Sorted-set member is a JSON blob; score is the UNIX timestamp.
    * The ``sensors`` set tracks every known sensor_id.
    * A per-second counter (``throughput:{unix_second}``) is incremented
      so the metrics service can query ingestion rate.
    """
    client = get_pool()

    # Default timestamp to now (UTC) if the caller didn't provide one
    ts = reading.timestamp or datetime.now(timezone.utc)
    timestamp_score = ts.timestamp()

    payload = json.dumps(
        {
            "sensor_id": reading.sensor_id,
            "timestamp": ts.isoformat(),
            "readings": reading.readings,
            "metadata": reading.metadata,
        }
    )

    tp_key = _throughput_key(ts)

    async with client.pipeline(transaction=True) as pipe:
        pipe.zadd(_readings_key(reading.sensor_id), {payload: timestamp_score})
        pipe.sadd(SENSORS_SET_KEY, reading.sensor_id)
        pipe.incr(tp_key)
        pipe.expire(tp_key, THROUGHPUT_TTL_SECONDS)
        await pipe.execute()

    # Store the resolved timestamp back so the router can return it
    reading.timestamp = ts


# ── Query ─────────────────────────────────────────────────────────────────────


async def get_latest_readings(
    sensor_id: str, limit: int = 10
) -> List[SensorReadingResponse]:
    """Return the *limit* most recent readings for a sensor (newest first)."""
    client = get_pool()
    raw_items = await client.zrevrange(_readings_key(sensor_id), 0, limit - 1)
    return [SensorReadingResponse(**json.loads(item)) for item in raw_items]


async def get_readings_in_range(
    sensor_id: str,
    start: datetime,
    end: datetime,
    limit: int = 1000,
    offset: int = 0,
) -> List[SensorReadingResponse]:
    """Return readings whose timestamp falls within [start, end], newest first.

    * Results are ordered descending (newest first) via ``zrevrangebyscore``.
    * ``limit`` caps the number of returned items (default 1 000) to prevent
      memory exhaustion when a wide time range is requested.
    * ``offset`` enables page-based navigation through large result sets.
    """
    client = get_pool()
    raw_items = await client.zrevrangebyscore(
        _readings_key(sensor_id),
        max=end.timestamp(),
        min=start.timestamp(),
        start=offset,
        num=limit,
    )
    return [SensorReadingResponse(**json.loads(item)) for item in raw_items]


async def list_sensors(
    cursor: int = 0,
    count: int = 1000,
) -> tuple[int, List[str]]:
    """Return a page of registered sensor IDs using cursor-based iteration.

    Uses ``SSCAN`` instead of ``SMEMBERS`` so that large sets are read
    incrementally and Redis is never blocked for a long atomic operation.

    Returns ``(next_cursor, sorted_sensor_ids)``.
    A ``next_cursor`` of ``0`` means the full set has been traversed.
    """
    client = get_pool()
    next_cursor, members = await client.sscan(
        SENSORS_SET_KEY, cursor=cursor, count=count
    )
    return next_cursor, sorted(members)
