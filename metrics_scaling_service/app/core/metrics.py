from prometheus_client import Counter, Gauge

# ── Business metrics — metrics & scaling service ──────────────────────────────

# Scaling recommendations issued
scaling_recommendations_total = Counter(
    "scaling_recommendations_total",
    "Total number of scaling recommendations issued",
    ["action"],  # label: SCALE_UP / SCALE_DOWN / NO_ACTION
)

# Current TPS as seen by the scaling service (gauge — fluctuates)
current_tps = Gauge(
    "current_tps",
    "Current ingestion throughput in messages per second",
)

# Current active worker count as seen by the scaling service
scaling_active_workers = Gauge(
    "scaling_active_workers_count",
    "Number of active workers as seen by the scaling service",
)
