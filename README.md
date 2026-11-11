# Health Check Service

A FastAPI service that registers HTTP endpoints, checks them on a schedule, stores check history, and records health-state transitions.

## Features

- Register, list, and delete monitored endpoints
- Asynchronous HTTP checks with configurable timeout, concurrency, and outbound request rate
- PostgreSQL persistence in Docker Compose; SQLite by default for local development
- Historical results and health-state transition queries
- Prometheus metrics, alert rules, and an operational runbook
- Result retention cleanup

## Requirements

- Python 3.11 or newer for local development
- Docker Desktop with Docker Compose for the containerized setup

## Build and run

### Option A: Docker Compose (PostgreSQL)

From the project directory in Command Prompt (CMD), create `.env` from the example if needed:

```cmd
if not exist .env copy .env.example .env
```

Start and inspect the services:

```cmd
docker compose up --build -d
docker compose ps
docker compose logs -f app
```

Compose reads local settings and database credentials from `.env`. The starter credentials are for local development only. `.env` is ignored by Git; change its values before using this setup outside a local machine.

Open:

- API documentation: <http://localhost:8000/docs>
- Liveness: <http://localhost:8000/health>
- Readiness (database connectivity): <http://localhost:8000/ready>
- Prometheus metrics: <http://localhost:8000/metrics>

Stop the containers while preserving database data:

```cmd
docker compose down
```

PostgreSQL data is kept in the named `postgres_data` volume. `docker compose down -v` removes that volume and its data; only use it when you intentionally want to delete the local database.

### Option B: Run locally with SQLite

In Command Prompt (CMD):

```cmd
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

When run directly, the app defaults to `sqlite+aiosqlite:///./health_monitor.db`. The `.env` file is loaded by Docker Compose; the local Python process does not load it automatically. Set environment variables in the shell if you want to override the local defaults.

## API

The endpoint-management API is versioned under `/api/v1`. Interactive OpenAPI documentation is available at `/docs`.

### Register an endpoint

`POST /api/v1/endpoints` — creates a monitor and returns `201 Created`.

Request:

```json
{
  "url": "https://example.com",
  "interval_seconds": 30,
  "expected_status_code": 200
}
```

Use `curl` to register an endpoint:

```cmd
curl -X POST http://localhost:8000/api/v1/endpoints -H "Content-Type: application/json" -d "{\"url\":\"https://example.com\",\"interval_seconds\":30,\"expected_status_code\":200}"
```

Example response:

```json
{
  "id": 1,
  "url": "https://example.com",
  "interval_seconds": 30,
  "expected_status_code": 200,
  "current_state": "HEALTHY",
  "last_checked_at": null,
  "created_at": "2026-01-01T12:00:00"
}
```

The first scheduled check is due immediately, so the state and `last_checked_at` may change shortly after registration. `interval_seconds` must be between 1 and 86400; `expected_status_code` must be between 100 and 599. Only one monitor is allowed per normalized URL (a trailing slash at the end is removed). A duplicate returns `409 Conflict`; invalid input returns `422 Unprocessable Entity`.

### List endpoints

`GET /api/v1/endpoints` returns an array of endpoint objects using the registration response shape.

```cmd
curl http://localhost:8000/api/v1/endpoints
```

### Get check history

`GET /api/v1/endpoints/{endpoint_id}/history` returns check results newest first. Optional `start_time` and `end_time` query parameters filter by check time; provide timezone-aware ISO 8601 timestamps. The start is inclusive and the end is exclusive. Either bound may be omitted.

```cmd
curl http://localhost:8000/api/v1/endpoints/1/history
```

For example, retrieve results checked during January 1, 2026 UTC:

```cmd
curl "http://localhost:8000/api/v1/endpoints/1/history?start_time=2026-01-01T00:00:00Z&end_time=2026-01-02T00:00:00Z"
```

Naive timestamps without a timezone or a start time that is not earlier than the end time return `422 Unprocessable Entity`.

Example response:

```json
[
  {
    "id": 12,
    "endpoint_id": 1,
    "status_code": 200,
    "response_time_ms": 184,
    "is_healthy": true,
    "error_message": null,
    "checked_at": "2026-01-01T12:00:30"
  }
]
```

### Get state transitions

`GET /api/v1/endpoints/{endpoint_id}/transitions` returns state changes newest first. It records changes, not every successful or failed check.

```cmd
curl http://localhost:8000/api/v1/endpoints/1/transitions
```

Example response:

```json
[
  {
    "id": 3,
    "endpoint_id": 1,
    "previous_state": "HEALTHY",
    "new_state": "UNHEALTHY",
    "changed_at": "2026-01-01T12:01:00"
  }
]
```

### Delete an endpoint

`DELETE /api/v1/endpoints/{endpoint_id}` returns `204 No Content`. The endpoint, its check results, and transitions are deleted.

```cmd
curl -X DELETE http://localhost:8000/api/v1/endpoints/1
```

History and transition requests for an unknown endpoint return `404 Not Found`.

### Service and monitoring endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness: the HTTP application is responding |
| `GET` | `/ready` | Readiness: verifies a database query succeeds; returns `503` otherwise |
| `GET` | `/metrics` | Prometheus-formatted application and scheduler metrics |

These are operational endpoints and are intentionally not under `/api/v1`.

## Configuration

`.env.example` lists the Docker Compose settings. Copy it to `.env` before starting Compose. The local Python process does not automatically read `.env`.

| Variable | Default | Description |
|---|---:|---|
| `POSTGRES_DB` | `healthcheck` | Compose PostgreSQL database name |
| `POSTGRES_USER` | `postgres` | Compose PostgreSQL user |
| `POSTGRES_PASSWORD` | `postgres` | Local-only Compose database password |
| `DATABASE_URL` | SQLite URL when running locally | SQLAlchemy database connection; Compose constructs its PostgreSQL URL |
| `SCHEDULER_ENABLED` | `true` | Starts the in-process scheduler |
| `HTTP_TIMEOUT_SECONDS` | `5.0` | Timeout for each outbound HTTP request |
| `MAX_CONCURRENCY` | `20` | Maximum in-flight checks per app process |
| `MAX_CHECKS_PER_SECOND` | `10` | Maximum outbound check request starts per second per app process |
| `CHECK_RESULT_RETENTION_DAYS` | `30` | Age after which detailed check-result rows expire |

## Architecture and tradeoffs

- **Modular monolith:** FastAPI API routes, scheduler, health checker, SQLAlchemy models, and monitoring are kept in one deployable application. This keeps the MVP straightforward to build and run, at the cost of scaling the API and worker separately.
- **Async HTTP checks:** `httpx.AsyncClient` lets checks wait on network I/O without blocking the event loop. This improves I/O concurrency, but targets can still be slow or unreachable; timeouts and concurrency/rate limits bound the impact.
- **Polling scheduler:** One in-process scheduler scans the database about once per second and launches due checks. It is simple and requires no broker, but timing can be slightly delayed and it is not safe to run multiple scheduler-enabled replicas: each may check the same endpoints. The rate limiter and concurrency cap are per process, not global.
- **PostgreSQL datastore:** Docker Compose uses PostgreSQL, a relational datastore suitable for endpoint records, indexed history queries, foreign keys, and transactional result/state updates. SQLite is the no-setup local-development fallback. For large history volume, PostgreSQL requires retention/partitioning and capacity planning; this MVP does not use a dedicated time-series database.
- **Three related tables:** `monitored_endpoints` stores configuration/current state, `health_check_results` stores individual observations, and `state_transitions` stores state changes. This makes current status and history easy to query, but detailed results grow over time, so retention removes old result rows. Transition history is currently retained indefinitely.
- **Schema initialization:** Startup uses SQLAlchemy `create_all` for a simple MVP. It creates missing tables but does not migrate existing schemas; production schema changes should use a migration tool such as Alembic.
- **Metrics and operations:** Prometheus metrics and alert rules expose scheduler/check failures, and the runbook documents recovery. Prometheus must load the supplied config/rules, and Alertmanager must be configured separately for notifications.

For larger deployments, use distributed scheduling/partitioned endpoint ownership and durable work distribution rather than running this in-process scheduler in multiple replicas. See [DESIGN.md](DESIGN.md) for the scaling discussion and [RUNBOOK.md](RUNBOOK.md) for failure response.

## Retention and alerts

Detailed check results are cleaned up daily, beginning 24 hours after app startup. By default, results older than 30 days are deleted. Endpoint records and transition history are not removed by this policy. Configure this with `CHECK_RESULT_RETENTION_DAYS`.

Prometheus alert rules and a starter scrape configuration are in `monitoring/health-check-alerts.yml` and `monitoring/prometheus.yml`. Configure Prometheus to load these files and set up Alertmanager receivers to deliver notifications.

## Tests

Run all unit and integration tests:

```cmd
python -m pytest -q
```

The tests mock outbound HTTP requests. The integration test exercises a scheduled check, stores its result, and queries the result and state transition through the API.

## Kubernetes with Helm

The chart under `helm/health-check-service` deploys both the service and a single-replica PostgreSQL StatefulSet with a persistent volume claim. The PostgreSQL password is stored in a Kubernetes Secret. The default password is for local development only; use a different URL-safe password for any shared environment. The in-process scheduler is enabled by default, so keep `replicaCount: 1` to avoid duplicate checks.

### Deploy to Minikube

Start Minikube and build the application image inside its image cache:

```cmd
minikube start
minikube image build -t health-check-service:0.1.0 .
```

Install or upgrade the complete stack:

```cmd
helm upgrade --install health-check-service .\helm\health-check-service --namespace health-monitor --create-namespace --set image.repository=health-check-service --set image.tag=0.1.0 --set image.pullPolicy=IfNotPresent
```

This uses the PostgreSQL password default from `values.yaml`, which is intended only for local development. The PostgreSQL data uses a PVC (1 GiB by default); Minikube's default storage provisioner normally creates it automatically. Check the rollout:

```cmd
kubectl get pods,pvc,svc -n health-monitor
kubectl rollout status statefulset/health-check-service-postgresql -n health-monitor
kubectl rollout status deployment/health-check-service -n health-monitor
kubectl logs -f deployment/health-check-service -n health-monitor
```

Open the API locally:

```cmd
kubectl port-forward svc/health-check-service 8000:80 -n health-monitor
```

Then visit <http://localhost:8000/docs>. To remove the Helm release, run `helm uninstall health-check-service -n health-monitor`. PVCs may remain after uninstall to protect database data; inspect them with `kubectl get pvc -n health-monitor` and delete them explicitly only if you intend to erase that data.

Before deploying to a shared or production cluster, replace the local password handling with an externally managed secret, configure backups/storage and resource limits, and consider a distributed scheduler before increasing the app replica count.
