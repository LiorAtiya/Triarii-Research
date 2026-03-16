from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Ingestion Service"
    APP_VERSION: str = "1.0.0"

    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # How many latest readings to return by default
    DEFAULT_LATEST_LIMIT: int = 10

    # How long to remember message_ids for deduplication (seconds)
    MESSAGE_DEDUP_TTL_SECONDS: int = 86400  # 24 hours

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
