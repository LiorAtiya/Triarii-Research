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
# String      dedup:{message_id}             sentinel key for dedup check (TTL)
# ─────────────────────────────────────────────────────────────────────────────

SENSORS_SET_KEY = "sensors"
THROUGHPUT_TTL_SECONDS = 3600  # keep per-second counters for 1 hour
READINGS_RETENTION_SECONDS = 3600  # trim readings older than 1 hour

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


def _dedup_key(message_id: str) -> str:
    return f"dedup:{message_id}"


# ── Lua script ────────────────────────────────────────────────────────────────
# Atomically: check dedup, write reading, register sensor, bump throughput.
# Returns 0 if duplicate, 1 if stored.
#
# KEYS: [1]=dedup_key  [2]=readings_key  [3]=sensors_key  [4]=throughput_key
# ARGV: [1]=dedup_ttl  [2]=timestamp_score  [3]=payload
#       [4]=cutoff_score  [5]=sensor_id  [6]=throughput_ttl
_STORE_SCRIPT = """
local is_new = redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[1])
if is_new == false then
    return 0
end
redis.call('ZADD', KEYS[2], ARGV[2], ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[4])
redis.call('SADD', KEYS[3], ARGV[5])
redis.call('INCR', KEYS[4])
redis.call('EXPIRE', KEYS[4], ARGV[6])
return 1
"""


# ── Store ─────────────────────────────────────────────────────────────────────


async def store_reading(reading: SensorReading) -> bool:
    """Persist a sensor reading and bump the throughput counter.

    Returns True if the reading was stored, False if it was a duplicate.

    Executes a Lua script so that the dedup check, ZADD, SADD, and INCR
    are one atomic operation — no partial writes if the process crashes mid-way.
    """
    client = get_pool()

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

    result = await client.eval(
        _STORE_SCRIPT,
        4,  # number of KEYS
        _dedup_key(reading.message_id),       # KEYS[1]
        _readings_key(reading.sensor_id),     # KEYS[2]
        SENSORS_SET_KEY,                      # KEYS[3]
        _throughput_key(ts),                  # KEYS[4]
        settings.MESSAGE_DEDUP_TTL_SECONDS,   # ARGV[1]
        timestamp_score,                      # ARGV[2]
        payload,                              # ARGV[3]
        timestamp_score - READINGS_RETENTION_SECONDS,  # ARGV[4]
        reading.sensor_id,                    # ARGV[5]
        THROUGHPUT_TTL_SECONDS,               # ARGV[6]
    )

    if result == 1:
        reading.timestamp = ts
    return result == 1


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
