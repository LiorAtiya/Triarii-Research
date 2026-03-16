from prometheus_client import Counter, Gauge

# ── Business metrics — worker manager service ─────────────────────────────────

# Workers registered since startup
workers_registered_total = Counter(
    "workers_registered_total",
    "Total number of workers registered",
)

# Workers deregistered since startup
workers_deregistered_total = Counter(
    "workers_deregistered_total",
    "Total number of workers deregistered",
)

# Heartbeats received
worker_heartbeats_total = Counter(
    "worker_heartbeats_total",
    "Total number of worker heartbeat updates received",
    ["worker_id"],
)

# Workers marked stale by the background cleanup task
workers_marked_stale_total = Counter(
    "workers_marked_stale_total",
    "Total number of workers marked stale by the background cleanup task",
)

# Current number of active workers (gauge — goes up and down)
active_workers = Gauge(
    "active_workers_count",
    "Current number of workers with a recent heartbeat",
)
