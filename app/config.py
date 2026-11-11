import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = "health-check-service"
    app_version: str = "0.1.0"
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./health_monitor.db")
    scheduler_enabled: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    api_prefix: str = "/api/v1"
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "5.0"))
    max_concurrency: int = int(os.getenv("MAX_CONCURRENCY", "20"))
    max_checks_per_second: float = float(os.getenv("MAX_CHECKS_PER_SECOND", "10"))
    check_result_retention_days: int = int(os.getenv("CHECK_RESULT_RETENTION_DAYS", "30"))


settings = Settings()
