import json
import asyncpg

from app.core.config import settings

# ── Connection pool (singleton, initialised via FastAPI lifespan) ─────────────
_pool: asyncpg.Pool | None = None

# ── Schema ────────────────────────────────────────────────────────────────────
# hypertable: sensor_readings
#   sensor_id  TEXT        — which sensor
#   time       TIMESTAMPTZ — partition key (TimescaleDB chunks by this)
#   readings   JSONB       — {"temperature": 22.5, "humidity": 60}
#   metadata   JSONB       — {"location": "room-1"}
# ─────────────────────────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS sensor_readings (
    sensor_id  TEXT        NOT NULL,
    time       TIMESTAMPTZ NOT NULL,
    readings   JSONB       NOT NULL,
    metadata   JSONB
);
"""

_CREATE_HYPERTABLE = """
SELECT create_hypertable(
    'sensor_readings', 'time',
    if_not_exists => TRUE
);
"""

_INSERT = """
INSERT INTO sensor_readings (sensor_id, time, readings, metadata)
VALUES ($1, $2, $3, $4)
ON CONFLICT DO NOTHING;
"""


async def init_pool() -> None:
    """Create the asyncpg connection pool and ensure the hypertable exists."""
    global _pool
    _pool = await asyncpg.create_pool(settings.TIMESCALE_DSN, min_size=2, max_size=10)
    async with _pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE)
        await conn.execute(_CREATE_HYPERTABLE)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("TimescaleDB pool not initialised – app must start via lifespan")
    return _pool


# ── Write ─────────────────────────────────────────────────────────────────────


async def archive_reading(
    sensor_id: str,
    timestamp_iso: str,
    readings_json: str,
    metadata_json: str,
) -> None:
    """Write one reading to TimescaleDB for long-term storage.

    Called by the stream consumer alongside persist_reading() (Redis hot path).
    ON CONFLICT DO NOTHING makes this idempotent — safe to retry after a crash.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            _INSERT,
            sensor_id,
            timestamp_iso,                   # asyncpg parses ISO-8601 → timestamptz
            json.dumps(json.loads(readings_json)),
            json.dumps(json.loads(metadata_json)) if metadata_json != "null" else None,
        )
