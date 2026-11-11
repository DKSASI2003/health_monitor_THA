from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest

registry = CollectorRegistry(auto_describe=True)

checks_total = Counter(
    "health_checks_total",
    "Total number of endpoint health checks executed.",
    registry=registry,
)
check_latency_ms = Histogram(
    "health_check_latency_ms",
    "HTTP health check latency in milliseconds.",
    registry=registry,
)
active_endpoints = Gauge(
    "active_monitored_endpoints",
    "Current number of monitored endpoints.",
    registry=registry,
)
unhealthy_endpoints = Gauge(
    "unhealthy_monitored_endpoints",
    "Current number of monitored endpoints in the UNHEALTHY state.",
    registry=registry,
)
max_endpoint_interval_seconds = Gauge(
    "health_check_max_endpoint_interval_seconds",
    "Largest configured check interval among monitored endpoints.",
    registry=registry,
)
scheduler_last_scan_timestamp_seconds = Gauge(
    "health_check_scheduler_last_scan_timestamp_seconds",
    "Unix timestamp of the most recent successful scheduler database scan.",
    registry=registry,
)
last_check_completion_timestamp_seconds = Gauge(
    "health_check_last_completion_timestamp_seconds",
    "Unix timestamp of the most recent health check successfully persisted.",
    registry=registry,
)
failed_checks_total = Counter(
    "health_checks_failed_total",
    "Total number of health checks with an unexpected status or request error.",
    registry=registry,
)
scheduler_scan_errors_total = Counter(
    "health_check_scheduler_scan_errors_total",
    "Total number of scheduler database scan failures.",
    registry=registry,
)
check_persistence_errors_total = Counter(
    "health_check_persistence_errors_total",
    "Total number of failures while persisting health check results.",
    registry=registry,
)
retention_deleted_results_total = Counter(
    "health_check_retention_deleted_results_total",
    "Total number of historical health check results deleted by retention cleanup.",
    registry=registry,
)
retention_cleanup_errors_total = Counter(
    "health_check_retention_cleanup_errors_total",
    "Total number of failed health check result retention cleanups.",
    registry=registry,
)
health_state_changes_total = Counter(
    "health_state_transitions_total",
    "Number of endpoint state transitions recorded.",
    registry=registry,
)


def metrics_payload():
    return generate_latest(registry)


def metrics_content_type():
    return CONTENT_TYPE_LATEST
