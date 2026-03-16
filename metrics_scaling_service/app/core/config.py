from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Metrics & Scaling Service"
    APP_VERSION: str = "1.0.0"

    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""

    # DB numbers used by other services (read-only from this service)
    INGESTION_REDIS_DB: int = 0  # throughput:{unix_second} keys live here
    WORKERS_REDIS_DB: int = 1  # worker:{id} hashes + workers:all set

    # Throughput calculation window
    THROUGHPUT_WINDOW_SECONDS: int = 10

    # Scaling thresholds
    SCALE_UP_THROUGHPUT_PER_WORKER: int = 1500
    SCALE_DOWN_THROUGHPUT_PER_WORKER: int = 1000
    MIN_WORKERS: int = 1
    MAX_WORKERS: int = 10

    # Heartbeat freshness – workers with last_heartbeat older than this are not "active"
    WORKER_STALE_SECONDS: int = 30

    # OpenTelemetry collector endpoint (Jaeger OTLP gRPC)
    OTEL_EXPORTER_ENDPOINT: str = "http://jaeger:4317"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
