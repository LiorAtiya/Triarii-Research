# IoT Data Ingestion & Microservice Manager

A distributed IoT data pipeline built with **FastAPI**, **Redis**, and **Docker Compose**.
Three independent microservices handle ingestion, worker management, and auto-scaling recommendations — with a full observability stack (Prometheus, Grafana, Jaeger, Loki).

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Services](#services)
3. [Observability Stack](#observability-stack)
4. [Redis Key Layout](#redis-key-layout)
5. [API Reference](#api-reference)
6. [Running the Project](#running-the-project)
7. [Running the Tests](#running-the-tests)
8. [Manual Testing](#manual-testing)
9. [Configuration](#configuration)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Docker Network                                  │
│                                                                              │
│  ┌─────────────────┐     ┌─────────────────┐     ┌────────────────────┐     │
│  │ Ingestion       │     │ Worker Manager  │     │ Metrics & Scaling  │     │
│  │ Service  :8000  │     │ Service  :8001  │     │ Service     :8002  │     │
│  │                 │     │                 │     │                    │     │
│  │ POST /data      │     │ POST /workers   │     │ GET /recommendation│     │
│  │ GET  /latest    │     │ DELETE /workers │     │                    │     │
│  │ GET  /sensors   │     │ PUT  /health    │     │                    │     │
│  └──────┬──────────┘     └──────┬──────────┘     └────────┬───────────┘     │
│         │ XADD (stream)         │ writes DB 1             │ reads DB 0+1    │
│         │ dedup SET NX          │                         │                 │
│         ▼                       ▼                         │                 │
│  ┌──────────────┐      ┌─────────────────┐                │                 │
│  │ Redis Stream │      │   Redis DB 1    │◄───────────────┘                 │
│  │ sensors:     │      │   workers:all   │                                  │
│  │ stream       │      │   worker:{id}   │                                  │
│  └──────┬───────┘      └─────────────────┘                                  │
│         │ XREADGROUP                                                         │
│         ▼                                                                    │
│  ┌──────────────┐      ┌─────────────────┐                                  │
│  │ Stream       │      │   Redis DB 0    │                                  │
│  │ Consumer     ├─────►│ sensor:{id}:    │                                  │
│  │ (background) │      │ readings (ZSET) │                                  │
│  └──────┬───────┘      └─────────────────┘                                  │
│         │                                                                    │
│         ▼                                                                    │
│  ┌──────────────┐                                                            │
│  │ TimescaleDB  │  ← long-term cold storage                                 │
│  │    :5432     │                                                            │
│  └──────────────┘                                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **IoT sensors** push readings to the **Ingestion Service** via `POST /api/v1/sensors/data`.
2. Ingestion checks a **dedup key** (`SET NX`) — duplicate `message_id` returns `status: duplicate`.
3. New readings are published to a **Redis Stream** (`XADD`) — HTTP responds immediately.
4. A **background stream consumer** (`XREADGROUP`) picks up messages, persists them to Redis Sorted Set (hot storage) and **TimescaleDB** (cold storage), then `XACK`s only after both succeed.
5. **Workers** register with the Worker Manager and send periodic heartbeats. A background task marks stale workers automatically.
6. The **Metrics & Scaling Service** reads throughput counters (DB 0) and worker heartbeats (DB 1) to return `SCALE_UP / SCALE_DOWN / NO_ACTION`.

---

## Services

### Ingestion Service (`port 8000`)

Accepts sensor readings, deduplicates, publishes to Redis Stream, and serves stored data.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/sensors/data` | Ingest a sensor reading (idempotent via `message_id`) |
| `GET` | `/api/v1/sensors/{id}/latest` | Latest N readings (newest first) |
| `GET` | `/api/v1/sensors/{id}/data/range` | Readings in a time range (cursor paginated) |
| `GET` | `/api/v1/sensors` | List all registered sensors (SSCAN paginated) |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/health` | Health check |

### Worker Manager Service (`port 8001`)

Tracks worker lifecycle, heartbeats, and throughput metrics.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/workers` | List all registered workers and their status |
| `POST` | `/api/v1/workers` | Register a new worker |
| `DELETE` | `/api/v1/workers/{worker_id}` | Deregister a worker |
| `PUT` | `/api/v1/workers/{worker_id}/health` | Worker heartbeat/health update |
| `GET` | `/api/v1/metrics/throughput` | Current throughput metrics |
| `GET` | `/metrics` | Prometheus metrics |

### Metrics & Scaling Service (`port 8002`)

Reads both Redis DBs and returns a scaling recommendation.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/scaling/recommendation` | Scaling recommendation based on TPS vs workers |
| `GET` | `/metrics` | Prometheus metrics |

**Scaling logic:**

| Condition | Action |
|-----------|--------|
| `tps > active_workers × 1500` | `SCALE_UP` |
| `tps < active_workers × 1000` | `SCALE_DOWN` |
| otherwise | `NO_ACTION` |

---

## Observability Stack

| Tool | Port | Purpose |
|------|------|---------|
| **Prometheus** | 9090 | Scrapes `/metrics` from all 3 services every 15s |
| **Grafana** | 3000 | Dashboards and PromQL/LogQL queries (`admin`/`admin`) |
| **Jaeger** | 16686 | Distributed tracing — full request timeline across services |
| **Loki** | 3100 | Log aggregation (queried via Grafana Explore) |
| **Promtail** | — | Collects Docker logs and ships to Loki |

### Key Prometheus Metrics

| Metric | Service | Description |
|--------|---------|-------------|
| `sensor_readings_received_total` | ingestion | Total readings received |
| `sensor_readings_published_total` | ingestion | Readings published to stream |
| `sensor_readings_duplicate_total` | ingestion | Deduplicated readings blocked |
| `sensor_readings_persisted_total` | ingestion | Readings persisted to Redis |
| `sensor_readings_archived_total` | ingestion | Readings archived to TimescaleDB |
| `active_workers_count` | worker_manager | Current active worker count |
| `workers_registered_total` | worker_manager | Total workers ever registered |
| `current_tps` | metrics_scaling | Current throughput per second |
| `scaling_recommendations_total` | metrics_scaling | Recommendations by action label |

### Useful PromQL Queries

```promql
# Throughput over time
rate(sensor_readings_published_total[1m])

# Duplicate rate
rate(sensor_readings_duplicate_total[1m]) / rate(sensor_readings_received_total[1m])

# Average HTTP latency
rate(http_request_duration_seconds_sum[1m]) / rate(http_request_duration_seconds_count[1m])

# Active workers
active_workers_count
```

### Loki Log Queries (in Grafana Explore)

```logql
# All ingestion logs
{service="ingestion_service"}

# Errors only
{service="ingestion_service"} |= "error"

# Filter by sensor_id (structured JSON logs)
{service="ingestion_service"} | json | sensor_id = "sensor-A"
```

---

## Redis Key Layout

| Key pattern | DB | Type | Description |
|---|---|---|---|
| `sensor:{id}:readings` | 0 | Sorted Set | All readings; score = Unix timestamp |
| `sensors` | 0 | Set | All known sensor IDs |
| `throughput:{unix_second}` | 0 | String | Per-second ingestion counter (TTL 1 h) |
| `dedup:{message_id}` | 0 | String | Dedup marker (TTL 24 h) |
| `sensors:stream` | 0 | Stream | Redis Stream for async processing |
| `worker:{id}` | 1 | Hash | All fields for one worker |
| `workers:all` | 1 | Set | All known worker IDs |

---

## API Reference

Full interactive docs are available when the stack is running:

- Ingestion: http://localhost:8000/docs
- Worker Manager: http://localhost:8001/docs
- Metrics & Scaling: http://localhost:8002/docs

---

## Running the Project

**Prerequisites:** Docker + Docker Compose

```bash
git clone <repo-url>
cd Triarii-Research
docker compose up --build
```

Services start on:
- Ingestion Service: http://localhost:8000
- Worker Manager Service: http://localhost:8001
- Metrics & Scaling Service: http://localhost:8002
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (admin / admin)
- Jaeger: http://localhost:16686
- Loki: http://localhost:3100

To stop:
```bash
docker compose down        # keep volumes
docker compose down -v     # wipe all data
```

### Connect Grafana to Prometheus (once)
1. Open http://localhost:3000 → **Connections → Data Sources → Add**
2. Select **Prometheus** → URL: `http://prometheus:9090` → **Save & Test**

### Connect Grafana to Loki (once)
1. **Connections → Data Sources → Add**
2. Select **Loki** → URL: `http://loki:3100` → **Save & Test**

---

## Running the Tests

All tests are **unit tests** — Redis is fully mocked, no Docker needed.

```bash
# Install test deps per service
pip install -r ingestion_service/requirements-test.txt
pip install -r worker_manager_service/requirements-test.txt
pip install -r metrics_scaling_service/requirements-test.txt

# Run all tests from repo root
python -m pytest ingestion_service/tests/ \
                 metrics_scaling_service/tests/ \
                 worker_manager_service/tests/ -v
```

| Service | Tests | Coverage |
|---------|-------|----------|
| Ingestion | 11 | POST /data, GET /latest, range, list sensors, Redis failures |
| Metrics & Scaling | 7 | SCALE_UP/DOWN/NO_ACTION, zero-worker edge case, Redis failure |
| Worker Manager | 14 | List/register/deregister/health-update, throughput metrics, Redis failures |

---

## Manual Testing

### Simulate load
```bash
# Send 30 sensor readings
for i in $(seq 1 30); do
  curl -s -X POST http://localhost:8000/api/v1/sensors/data \
    -H "Content-Type: application/json" \
    -d "{\"sensor_id\": \"sensor-$((RANDOM % 3 + 1))\", \"readings\": {\"temperature\": $((RANDOM % 30 + 10))}, \"metadata\": {}}" > /dev/null
done

# Test deduplication — second call returns status: duplicate
curl -X POST http://localhost:8000/api/v1/sensors/data \
  -H "Content-Type: application/json" \
  -d '{"sensor_id": "sensor-A", "message_id": "test-001", "readings": {"temperature": 25}, "metadata": {}}'

curl -X POST http://localhost:8000/api/v1/sensors/data \
  -H "Content-Type: application/json" \
  -d '{"sensor_id": "sensor-A", "message_id": "test-001", "readings": {"temperature": 25}, "metadata": {}}'
```

### Register workers and check scaling
```bash
# Register 3 workers
for i in 1 2 3; do
  curl -s -X POST http://localhost:8001/api/v1/workers \
    -H "Content-Type: application/json" \
    -d "{\"worker_id\": \"worker-$i\"}"
done

# Check scaling recommendation
curl http://localhost:8002/api/v1/scaling/recommendation

# Send heartbeat
curl -X PUT http://localhost:8001/api/v1/workers/worker-1/health \
  -H "Content-Type: application/json" \
  -d '{"processed_count": 100}'
```

### Query stored readings
```bash
# Latest 10 readings
curl "http://localhost:8000/api/v1/sensors/sensor-A/latest?limit=10"

# List all sensors
curl http://localhost:8000/api/v1/sensors
```

---

## Configuration

All settings are read from environment variables (set in `docker-compose.yml`):

```env
# All services
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=
OTEL_EXPORTER_ENDPOINT=http://jaeger:4317

# Ingestion Service (DB 0)
REDIS_DB=0
DEFAULT_LATEST_LIMIT=10
MESSAGE_DEDUP_TTL_SECONDS=86400
TIMESCALE_DSN=postgresql://iot:iot_secret@timescaledb:5432/iot_readings

# Worker Manager Service (DB 1)
REDIS_DB=1
WORKER_STALE_SECONDS=30

# Metrics & Scaling Service
INGESTION_REDIS_DB=0
WORKERS_REDIS_DB=1
THROUGHPUT_WINDOW_SECONDS=10
SCALE_UP_THROUGHPUT_PER_WORKER=1500
SCALE_DOWN_THROUGHPUT_PER_WORKER=1000
MIN_WORKERS=1
MAX_WORKERS=10
WORKER_STALE_SECONDS=30
```
