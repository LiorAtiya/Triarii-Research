import json
import redis.asyncio as aioredis
from datetime import datetime, timezone
from typing import List

from app.core.config import settings
from app.models.sensor import SensorReading, SensorReadingResponse

# ── Data flow ─────────────────────────────────────────────────────────────────
#
#  PATH 1 — API (synchronous, called by HTTP request):
#    POST /sensors/data
#      → publish_reading()       dedup check (SET NX) + XADD to stream
#      → returns HTTP 201 immediately
#
#  PATH 2 — Message Broker (asynchronous, runs in background):
#    stream_consumer.run()       XREADGROUP loop, one message at a time
#      → persist_reading()       Lua script: ZADD + SADD + INCR + XACK
#
#  Adding more consumers (e.g. ML, alerts) means creating a new consumer group
#  that reads from the same STREAM_KEY independently.
#
# ── Redis key layout ──────────────────────────────────────────────────────────
# Sorted Set  sensor:{sensor_id}:readings   score = unix-ts, member = JSON blob
# Set         sensors                        all known sensor IDs
# String      throughput:{unix_second}       per-second ingestion counter (TTL)
# String      dedup:{message_id}             sentinel key for dedup check (TTL)
# Stream      sensors:stream                 incoming readings queue (XADD / XREADGROUP)
# ─────────────────────────────────────────────────────────────────────────────

SENSORS_SET_KEY = "sensors"
THROUGHPUT_TTL_SECONDS = 3600  # keep per-second counters for 1 hour
READINGS_RETENTION_SECONDS = 3600  # trim readings older than 1 hour
STREAM_KEY = "sensors:stream"
STREAM_MAX_LEN = 100_000  # cap stream length to avoid unbounded memory growth
CONSUMER_GROUP = "ingestion-workers"
CONSUMER_NAME = "consumer-1"

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
# Atomically: write reading, register sensor, bump throughput counter.
# Dedup is handled before publishing to the stream, so it is not repeated here.
#
# KEYS: [1]=readings_key  [2]=sensors_key  [3]=throughput_key
# ARGV: [1]=timestamp_score  [2]=payload  [3]=cutoff_score
#       [4]=sensor_id  [5]=throughput_ttl
_STORE_SCRIPT = """
redis.call('ZADD', KEYS[1], ARGV[1], ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[3])
redis.call('SADD', KEYS[2], ARGV[4])
redis.call('INCR', KEYS[3])
redis.call('EXPIRE', KEYS[3], ARGV[5])
return 1
"""


# ── Publish (API layer) ───────────────────────────────────────────────────────


async def publish_reading(reading: SensorReading) -> bool:
    """Push a sensor reading onto the Redis Stream for async processing.

    Returns True if enqueued, False if the message_id was already seen (duplicate).

    The dedup check (SET NX) happens here so the HTTP response is immediate.
    The actual ZADD / SADD / INCR happen inside the stream consumer.
    """
    client = get_pool()

    ts = reading.timestamp or datetime.now(timezone.utc)
    reading.timestamp = ts  # resolve once; consumer will reuse this value

    # Fast duplicate check — if the key already exists, drop the message now
    is_new = await client.set(
        _dedup_key(reading.message_id), "1",
        nx=True, ex=settings.MESSAGE_DEDUP_TTL_SECONDS,
    )
    if not is_new:
        return False

    # Inject current trace context so the stream consumer can link its span
    # to the originating HTTP request span (cross-process propagation).
    from opentelemetry import trace, propagate
    from opentelemetry.propagators.textmap import DefaultGetter
    carrier: dict = {}
    propagate.inject(carrier)

    await client.xadd(
        STREAM_KEY,
        {
            "sensor_id": reading.sensor_id,
            "message_id": reading.message_id,
            "timestamp": ts.isoformat(),
            "readings": json.dumps(reading.readings),
            "metadata": json.dumps(reading.metadata),
            "trace_carrier": json.dumps(carrier),  # propagated trace context
        },
        maxlen=STREAM_MAX_LEN,
        approximate=True,
    )
    return True


# ── Persist (consumer layer) ──────────────────────────────────────────────────


async def persist_reading(
    sensor_id: str,
    timestamp_iso: str,
    readings_json: str,
    metadata_json: str,
) -> None:
    """Write one reading from the stream into the Sorted Set + counters.

    Called by the stream consumer after a message is successfully read.
    Executes the Lua script so all writes are atomic.
    """
    client = get_pool()

    ts = datetime.fromisoformat(timestamp_iso)
    timestamp_score = ts.timestamp()
    payload = json.dumps(
        {
            "sensor_id": sensor_id,
            "timestamp": timestamp_iso,
            "readings": json.loads(readings_json),
            "metadata": json.loads(metadata_json),
        }
    )

    await client.eval(
        _STORE_SCRIPT,
        3,  # number of KEYS (no dedup key — already checked in publish_reading)
        _readings_key(sensor_id),                      # KEYS[1]
        SENSORS_SET_KEY,                               # KEYS[2]
        _throughput_key(ts),                           # KEYS[3]
        timestamp_score,                               # ARGV[1]
        payload,                                       # ARGV[2]
        timestamp_score - READINGS_RETENTION_SECONDS,  # ARGV[3]
        sensor_id,                                     # ARGV[4]
        THROUGHPUT_TTL_SECONDS,                        # ARGV[5]
    )


async def ensure_consumer_group() -> None:
    """Create the consumer group if it does not exist yet."""
    client = get_pool()
    try:
        await client.xgroup_create(STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


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
