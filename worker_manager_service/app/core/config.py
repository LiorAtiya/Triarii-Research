from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Worker Manager Service"
    APP_VERSION: str = "1.0.0"

    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 1  # separate DB from ingestion service (DB 0)
    REDIS_PASSWORD: str = ""

    # Seconds after last heartbeat before a worker is considered stale
    WORKER_STALE_SECONDS: int = 30

    # OpenTelemetry collector endpoint (Jaeger OTLP gRPC)
    OTEL_EXPORTER_ENDPOINT: str = "http://jaeger:4317"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
