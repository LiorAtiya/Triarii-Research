import logging
import structlog


def setup_logging() -> None:
    """Configure structlog to emit JSON logs.

    Every log line will be a JSON object with at minimum:
      timestamp, level, service, logger, event

    Example output:
      {"timestamp": "2026-03-17T10:23:41Z", "level": "info",
       "logger": "app.main", "event": "stale_workers_marked", "count": 3}
    """
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
