# IoT Data Ingestion & Microservice Manager

A distributed IoT data pipeline built with **FastAPI**, **Redis**, and **Docker Compose**.  
Three independent microservices handle ingestion, worker management, and auto-scaling recommendations.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Services](#services)
3. [Redis Key Layout](#redis-key-layout)
4. [API Reference](#api-reference)
5. [Running the Project](#running-the-project)
6. [Running the Tests](#running-the-tests)
7. [Manual Testing](#manual-testing)
8. [What I Would Improve With More Time](#what-i-would-improve-with-more-time)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                          Docker Network                              │
│                                                                      │
│  ┌─────────────────┐     ┌─────────────────┐    ┌────────────────┐  │
│  │ Ingestion       │     │ Worker Manager  │    │ Metrics &      │  │
│  │ Service         │     │ Service         │    │ Scaling        │  │
│  │ :8000           │     │ :8001           │    │ Service :8002  │  │
│  │                 │     │                 │    │                │  │
│  │  POST /data     │     │  POST /workers  │    │  GET /scaling  │  │
│  │  GET  /data     │     │  DELETE/workers │    │  /recommendation│ │
│  │  GET  /range    │     │  PUT  /health   │    │                │  │
│  │  GET  /sensors  │     │  GET  /workers  │    │                │  │
│  │                 │     │  GET  /metrics  │    │                │  │
│  └────────┬────────┘     └────────┬────────┘    └───────┬────────┘  │
│           │ writes DB 0           │ writes DB 1          │ reads     │
│           │                       │                      │ DB 0 + 1  │
│           └───────────────────────┴──────────────────────┘          │
│                                   │                                  │
│                          ┌────────▼────────┐                        │
│                          │     Redis       │                        │
│                          │  DB 0 – sensor  │                        │
│                          │  DB 1 – workers │                        │
│                          └─────────────────┘                        │
└──────────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **IoT sensors** push readings to the **Ingestion Service** via `POST /api/v1/sensors/data`.
2. The Ingestion Service writes each reading into a Redis **Sorted Set** (score = Unix timestamp) and increments a per-second throughput counter (`throughput:{unix_second}`).
3. **Workers** register themselves with the **Worker Manager Service** and send periodic heartbeats via `PUT /api/v1/workers/{id}/health`.
4. The **Metrics & Scaling Service** reads the throughput counters from DB 0 and the worker heartbeats from DB 1, computes TPS, and returns a `SCALE_UP / SCALE_DOWN / NO_ACTION` recommendation.

---

## Services

### Ingestion Service (`port 8000`) - Part 1

Accepts sensor readings and persists them in Redis.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/sensors/data` | Ingest a sensor reading |
| `GET` | `/api/v1/sensors/{id}/data` | Latest N readings (newest first) |
| `GET` | `/api/v1/sensors/{id}/data/range` | Readings in a time range (cursor paginated) |
| `GET` | `/api/v1/sensors` | List all registered sensors (SSCAN paginated) |

### Worker Manager Service (`port 8001`) — Part 2

Tracks worker lifecycle, heartbeats, and throughput metrics.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/workers` | List all registered workers and their status |
| `POST` | `/api/v1/workers` | Register a new worker |
| `DELETE` | `/api/v1/workers/{worker_id}` | Deregister a worker |
| `PUT` | `/api/v1/workers/{worker_id}/health` | Worker heartbeat/health update |
| `GET` | `/api/v1/metrics/throughput` | Get current throughput metrics |

### Metrics & Scaling Service (`port 8002`) — Part 3

Tracks ingestion throughput and exposes a scaling recommendation.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/scaling/recommendation` | Returns scaling recommendation based on throughput |

**Scaling logic:**

| Condition | Action |
|-----------|--------|
| `tps > active_workers × 1500` | `SCALE_UP` |
| `tps < active_workers × 1000` | `SCALE_DOWN` |
| otherwise | `NO_ACTION` |

---

## Redis Key Layout

| Key pattern | Type | Written by | Description |
|---|---|---|---|
| `sensor:{id}:readings` | Sorted Set | Ingestion | All readings; score = Unix timestamp |
| `sensors` | Set | Ingestion | All known sensor IDs |
| `throughput:{unix_second}` | String | Ingestion | Per-second ingestion counter (TTL 1 h) |
| `worker:{id}` | Hash | Worker Manager | All fields for one worker |
| `workers:all` | Set | Worker Manager | All known worker IDs |

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
# Clone and start
git clone <repo-url>
cd Triarii-Research
docker compose up --build
```

All three services start automatically. Redis is healthy-checked before any service starts.

To stop:
```bash
docker compose down          # keep Redis volume
docker compose down -v       # also wipe Redis data
```

---

## Running the Tests

All tests are **unit tests** — Redis is fully mocked, so no running Docker or network connection is needed.

**Prerequisites:**
```bash
pip install pytest httpx
```

Or use each service's dedicated test requirements file:
```bash
pip install -r <service>/requirements-test.txt
```

### Run all services at once
```bash
# From the repo root
python -m pytest ingestion_service/tests/ \
                 metrics_scaling_service/tests/ \
                 worker_manager_service/tests/ -v
```

### Run per service
```bash
# Ingestion Service (11 tests)
cd ingestion_service
python -m pytest tests/ -v

# Metrics & Scaling Service (7 tests)
cd metrics_scaling_service
python -m pytest tests/ -v

# Worker Manager Service (14 tests)
cd worker_manager_service
python -m pytest tests/ -v
```

### What is covered

| Service | File | Tests | Coverage |
|---|---|---|---|
| Ingestion | `tests/test_sensors.py` | 11 | `POST /data`, `GET /data`, range endpoint, list sensors, Redis failure paths |
| Metrics & Scaling | `tests/test_scaling.py` | 7 | SCALE_UP / SCALE_DOWN / NO_ACTION logic, zero-worker edge case, max-workers clamp, Redis failure |
| Worker Manager | `tests/test_workers.py` | 14 | List / register / deregister / health-update workers, throughput metrics endpoint, Redis failure paths |

---

## Manual Testing

### 1. Register a worker
```bash
curl -X POST http://localhost:8001/api/v1/workers \
  -H "Content-Type: application/json" \
  -d '{"worker_id": "worker-001"}'
```

### 2. Ingest sensor readings
```bash
curl -X POST http://localhost:8000/api/v1/sensors/data \
  -H "Content-Type: application/json" \
  -d '{"sensor_id": "sensor-A", "readings": {"temperature": 22.5, "humidity": 60}, "metadata": {"location": "room-1"}}'
```

### 3. Send a heartbeat
```bash
curl -X PUT http://localhost:8001/api/v1/workers/worker-001/health \
  -H "Content-Type: application/json" \
  -d '{"processed_count": 42}'
```

### 4. Check scaling recommendation (run immediately after ingesting)
```bash
curl http://localhost:8002/api/v1/scaling/recommendation
```

### 5. Query stored readings
```bash
# Latest 5
curl "http://localhost:8000/api/v1/sensors/sensor-A/data?limit=5"

# By time range
curl "http://localhost:8000/api/v1/sensors/sensor-A/data/range?start=2026-02-19T00:00:00&end=2026-02-19T23:59:59"
```

---

## What I Would Improve With More Time

### Reliability & Correctness
- **Automatic stale-worker detection** — a background task (e.g. APScheduler or a Redis keyspace notification) that marks workers as `stale` automatically instead of relying on the metrics service to infer it at query time.
- **Idempotent ingestion** — add a `message_id` field and use a Redis Set to deduplicate readings so re-sent messages don't inflate counters.
- **Atomic sensor registration** — currently `ZADD` + `SADD` are in a single pipeline but if the process crashes between writes the `sensors` set could be out of sync. A Lua script would make this truly atomic.

### Scalability
- **Replace `SMEMBERS` with `SSCAN`** everywhere it still appears (e.g. `workers:all`) under high worker counts — already done for the sensors set, should be applied consistently.
- **Horizontal scaling of ingestion** — add a message queue (Redis Streams or Kafka) between the HTTP endpoint and the Redis writer so the ingest layer can scale independently without write contention.
- **Time-series database** — for long-term retention and aggregation, move historical readings to TimescaleDB or InfluxDB and keep Redis only as a hot cache.

### Observability
- **Structured logging** — replace plain `print`/default uvicorn logs with `structlog` or `python-json-logger` so logs are parseable by tools like Loki or CloudWatch.
- **Metrics endpoint** — expose a Prometheus `/metrics` endpoint from each service (via `prometheus-fastapi-instrumentator`) for latency, error rate, and throughput dashboards in Grafana.
- **Distributed tracing** — add OpenTelemetry to trace a request from ingestion through to the scaling recommendation.

### Testing
- **Integration tests** — spin up a real Redis instance in CI (GitHub Actions `services:`) and run end-to-end tests against the FastAPI `TestClient` to verify cross-service data flow (ingestion writes → scaling reads).
- **Load tests** — use `locust` or `k6` to verify the pipeline holds up at the stated 1 500 msg/s/worker threshold.

### Security
- **Redis password** — enforce `REDIS_PASSWORD` in production and rotate it via a secrets manager (Vault / AWS Secrets Manager).
- **API authentication** — add an API-key or JWT middleware so only authorised sensors and workers can call the endpoints.
- **Input validation hardening** — tighten `sensor_id` to a safe character set (regex validator on the Pydantic model) to prevent Redis key injection.
# Triarii-Research
