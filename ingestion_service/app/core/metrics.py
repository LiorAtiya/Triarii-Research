from prometheus_client import Counter, Gauge

# ── Business metrics — ingestion service ──────────────────────────────────────

# Total readings received (includes duplicates)
readings_received_total = Counter(
    "sensor_readings_received_total",
    "Total number of sensor readings received by the API",
    ["sensor_id"],
)

# Readings that were duplicates (dropped)
readings_duplicate_total = Counter(
    "sensor_readings_duplicate_total",
    "Total number of duplicate readings dropped (same message_id seen before)",
)

# Readings successfully published to the stream
readings_published_total = Counter(
    "sensor_readings_published_total",
    "Total number of readings successfully published to the Redis Stream",
)

# Readings persisted to Redis Sorted Set by the consumer
readings_persisted_total = Counter(
    "sensor_readings_persisted_total",
    "Total number of readings written to Redis by the stream consumer",
)

# Readings archived to TimescaleDB by the consumer
readings_archived_total = Counter(
    "sensor_readings_archived_total",
    "Total number of readings written to TimescaleDB by the stream consumer",
)

# Stream consumer errors
stream_consumer_errors_total = Counter(
    "stream_consumer_errors_total",
    "Total number of errors encountered by the stream consumer",
)

# Pending messages recovered after a crash
stream_pending_recovered_total = Counter(
    "stream_pending_recovered_total",
    "Total number of pending stream messages recovered after a crash",
)
