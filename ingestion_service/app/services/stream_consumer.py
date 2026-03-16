import asyncio
import json

import structlog
from opentelemetry import propagate, trace

from app.services.redis_service import (
    CONSUMER_GROUP,
    CONSUMER_NAME,
    STREAM_KEY,
    ensure_consumer_group,
    get_pool,
    persist_reading,
)
from app.core.metrics import (
    readings_archived_total,
    readings_persisted_total,
    stream_consumer_errors_total,
    stream_pending_recovered_total,
)
from app.services.timescale_service import archive_reading

logger = structlog.get_logger(__name__)

_BATCH_SIZE = 10   # messages to fetch per XREADGROUP call
_BLOCK_MS = 2000   # block at most 2 s waiting for new messages


async def run() -> None:
    """Consume sensor readings from the Redis Stream and persist them.

    Uses a consumer group so that if multiple instances of this service run,
    each message is processed by exactly one consumer.

    Flow per iteration:
    1. XREADGROUP — fetch up to _BATCH_SIZE undelivered messages
    2. For each message: persist_reading (Lua script) + XACK
    3. If no new messages, also re-deliver any pending (unacked) messages
       that may have been abandoned after a crash.
    """
    await ensure_consumer_group()
    client = get_pool()

    while True:
        try:
            # Fetch new messages (never-delivered to this group)
            results = await client.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                streams={STREAM_KEY: ">"},
                count=_BATCH_SIZE,
                block=_BLOCK_MS,
            )

            if results:
                stream_name, messages = results[0]
                await _process_messages(client, messages)
            else:
                # No new messages — re-deliver any pending (crashed mid-process)
                await _recover_pending(client)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            stream_consumer_errors_total.inc()
            logger.exception("stream_consumer_error", error=str(exc))
            await asyncio.sleep(1)


async def _process_messages(client, messages: list) -> None:
    tracer = trace.get_tracer(__name__)
    for msg_id, fields in messages:
        # Extract trace context injected by publish_reading() so this span
        # appears as a child of the originating HTTP request in Jaeger.
        carrier = json.loads(fields.get("trace_carrier", "{}"))
        ctx = propagate.extract(carrier)

        with tracer.start_as_current_span(
            "stream_consumer.process_message",
            context=ctx,
            attributes={"sensor_id": fields["sensor_id"], "msg_id": msg_id},
        ):
            try:
                # PATH 2a — hot path: write to Redis Sorted Set (last 1h, fast queries)
                await persist_reading(
                    sensor_id=fields["sensor_id"],
                    timestamp_iso=fields["timestamp"],
                    readings_json=fields["readings"],
                    metadata_json=fields["metadata"],
                )
                readings_persisted_total.inc()
                # PATH 2b — cold path: archive to TimescaleDB (full history, aggregations)
                await archive_reading(
                    sensor_id=fields["sensor_id"],
                    timestamp_iso=fields["timestamp"],
                    readings_json=fields["readings"],
                    metadata_json=fields["metadata"],
                )
                readings_archived_total.inc()
                await client.xack(STREAM_KEY, CONSUMER_GROUP, msg_id)
            except Exception as exc:
                stream_consumer_errors_total.inc()
                trace.get_current_span().record_exception(exc)
                logger.exception("stream_message_failed", msg_id=msg_id, error=str(exc))


async def _recover_pending(client) -> None:
    """Re-process messages that were delivered but never acknowledged (e.g. after a crash)."""
    pending = await client.xreadgroup(
        groupname=CONSUMER_GROUP,
        consumername=CONSUMER_NAME,
        streams={STREAM_KEY: "0"},  # "0" = re-deliver pending messages
        count=_BATCH_SIZE,
    )
    if pending:
        _, messages = pending[0]
        if messages:
            stream_pending_recovered_total.inc(len(messages))
            logger.info("recovering_pending_messages", count=len(messages))
            await _process_messages(client, messages)
