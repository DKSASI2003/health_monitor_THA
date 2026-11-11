# Health Check Service Design

## 1. MVP Architecture

The service is implemented as a modular monolith with a FastAPI application, SQLAlchemy persistence layer, and an asynchronous scheduler.

This keeps the initial deployment simple while satisfying the project requirements: asynchronous health checks, bounded concurrency, persistence, state transitions, metrics, and graceful shutdown.

The MVP runs as a single deployment with PostgreSQL as its datastore. The architecture is designed to evolve into independently scalable control-plane and data-plane components without requiring a complete rewrite.

## 2. Components

### API Layer

The FastAPI application exposes CRUD endpoints for monitored endpoints and read-only access to health-check results and state transitions.

All endpoints are versioned under `/api/v1`.

### Scheduler

The scheduler polls the database for endpoints whose `last_checked_at` timestamp indicates that their next check is due.

Each endpoint is checked using a bounded `asyncio.Semaphore` to limit concurrency and prevent unbounded outbound HTTP requests.

The MVP uses an in-process scheduler. In a production deployment, scheduling responsibilities will be separated from the API and distributed across regional scheduler instances.

### Health Checking

Each check uses `httpx.AsyncClient` with configurable timeouts and redirect handling.

The health result captures:
- HTTP status code.
- Response latency.
- Health status.
- Error details, if applicable.
- Check execution timestamp.

The result is persisted, and the endpoint's current state is updated if a state transition is detected.

### Persistence

The schema includes three tables:

- `monitored_endpoints` stores endpoint metadata, check configuration, and current health state.
- `health_check_results` stores historical outcomes from every check.
- `state_transitions` records only actual health-state changes.

### Metrics and Readiness

The application exposes:
- `/metrics` for Prometheus scraping.
- `/health` for application liveness.
- `/ready` for application readiness.

## 3. Production Scaling Architecture

![Global Health Check Architecture Diagram](<Global Health Check Architecture Diagram.png>)


### 3.1 Capacity Planning

The production requirement is to monitor 50,000 endpoints at 30-second intervals across three regions.

Expected workload:

- Total endpoints: 50,000.
- Check interval: 30 seconds.
- Average global execution rate: approximately 1,667 checks/second.
- Average daily execution volume: approximately 144 million checks.

Assuming an even distribution across three regions:

| Region | Endpoints | Checks/second |
|---|---:|---:|
| Region 1 | 16,667 | 556 |
| Region 2 | 16,667 | 556 |
| Region 3 | 16,666 | 555 |
| **Total** | **50,000** | **1,667** |

These are average workloads. Actual capacity planning must account for endpoint response times, retries, network latency, and failure scenarios.

### 3.2 Global Control Plane

I propose a centralized Global Control Plane responsible for managing endpoint configurations and coordinating regional ownership.

The Control Plane is logically separated from the regional execution infrastructure.

Its responsibilities include:

- Exposing the global REST API for endpoint registration, updates, and deletion.
- Maintaining endpoint configurations and regional ownership metadata.
- Assigning endpoints to appropriate monitoring regions.
- Distributing endpoint configurations to regional data planes.
- Coordinating regional failover when a region becomes unavailable.
- Providing a unified interface for querying endpoint configurations and monitoring status.

The Control Plane will be implemented as a separate FastAPI service and deployed independently using Kubernetes.

For the initial production design, I would deploy the Control Plane in a primary region with high availability and standby/failover capability.

Existing regional data planes must continue executing checks using locally cached or replicated configurations even if the Control Plane becomes unavailable.

This prevents a Control Plane outage from interrupting ongoing monitoring.

### 3.3 Regional Data Plane

Each region contains an independent data plane responsible for executing health checks.

A regional deployment consists of:

- Regional scheduler.
- Message broker.
- Horizontally scalable worker pods.
- Result processing components.
- Regional datastore.
- Regional observability stack.

Each region normally owns a defined partition of monitored endpoints.

The regional scheduler identifies endpoints whose checks are due and publishes execution jobs to the message broker.

Workers consume these jobs, execute HTTP health checks, and persist their results.

This separation allows scheduling and execution to scale independently.

### 3.4 Distributed Scheduling and Execution

The in-process scheduler used in the MVP will be replaced by a distributed scheduling mechanism.

Proposed execution flow:

1. The regional scheduler identifies endpoints due for execution.
2. Scheduling jitter distributes executions across the 30-second interval to avoid synchronized bursts.
3. The scheduler publishes jobs to a durable message broker such as Kafka or RabbitMQ.
4. Multiple worker instances consume jobs concurrently.
5. Workers execute HTTP requests using asynchronous clients and bounded concurrency.
6. Results are persisted and endpoint state transitions are recorded.

To prevent duplicate scheduling, use partition ownership, distributed leases, or database locking.

Execution jobs should contain endpoint identifiers, execution identifiers, configuration versions, and scheduled timestamps.

Workers must support idempotent result processing because message brokers may provide at-least-once delivery.

Worker scaling will be based on queue depth, queue processing lag, execution latency, and resource utilization.

Kubernetes HPA or KEDA can be used to scale worker deployments.

### 3.5 Regional Ownership and Failover

The Global Control Plane maintains regional ownership metadata.

For example:

- Region 1 owns approximately 16,667 endpoints.
- Region 2 owns approximately 16,667 endpoints.
- Region 3 owns approximately 16,666 endpoints.

If a region becomes unavailable:

1. Detect the regional failure using regional health signals.
2. Identify the affected endpoint partitions.
3. Coordinate reassignment to healthy regions.
4. Ensure the new region has the required endpoint configurations and scheduling state.
5. Resume health checks.
6. Reconcile delayed or duplicate executions when the original region recovers.

I would maintain spare worker capacity across regions to support failover.

For critical endpoints, additional regional probes can provide independent geographical availability observations.

## 4. Data Storage and Retention

PostgreSQL will be used initially for endpoint configurations, current health states, and historical check results.

At higher volumes, the historical results table will become the primary storage concern due to the approximately 144 million daily check executions.

### Storage Strategy

- Use PostgreSQL for endpoint configurations and operational state.
- Partition historical health-check results by timestamp.
- Use connection pooling and batched inserts to improve database throughput.
- Use read replicas for historical queries where appropriate.
- Evaluate TimescaleDB or ClickHouse for higher-volume time-series and analytical workloads.

Regional databases will handle local execution data, while aggregated results can be asynchronously replicated to a global reporting layer.

### Retention Strategy

Proposed retention policy:

| Data Type | Retention |
|---|---|
| Detailed health-check results | 7 days |
| Five-minute aggregated results | 30 days |
| Hourly/daily historical aggregates | 1 year |

Older detailed records can be removed through partition lifecycle management.

Aggregated historical data can be archived in object storage such as Amazon S3.

## 5. Failure Modes and Recovery

### Scheduler Failure

- Deploy redundant scheduler instances.
- Use distributed leases or partition ownership.
- Persist scheduling metadata.
- Allow another scheduler to take over expired ownership.
- Identify and reschedule overdue checks.

### Worker Failure

- Use message acknowledgements and automatic job redelivery.
- Implement bounded retries and exponential backoff.
- Use execution identifiers to prevent duplicate result persistence.
- Route repeatedly failing jobs to a dead-letter queue.

### Database Failure

- Use database replication and automated failover.
- Buffer results in durable queues during temporary outages.
- Retry transient database failures.
- Monitor connection usage and replication lag.
- Maintain regular backups and recovery procedures.

### Message Broker Failure

- Use replicated broker infrastructure.
- Enable durable message persistence.
- Configure producer retries.
- Monitor broker availability and queue lag.
- Recover unprocessed jobs after broker restoration.

### Regional Failure

- Detect regional failures through independent health monitoring.
- Coordinate endpoint ownership reassignment through the Control Plane.
- Use healthy regions to resume affected monitoring workloads.
- Maintain sufficient spare capacity for regional failover.
- Ensure regional recovery does not result in duplicate scheduling.

### Control Plane Failure

- Deploy redundant Control Plane API instances.
- Maintain standby/failover capability for the primary Control Plane region.
- Keep regional configurations locally available.
- Allow regional schedulers and workers to continue operating independently.
- Resume configuration distribution and ownership reconciliation when the Control Plane recovers.

## 6. Deployment Topology

### MVP Deployment

The initial implementation will be deployed as a single-region application.

Components:
- FastAPI application.
- In-process scheduler.
- Asynchronous health-check execution.
- PostgreSQL datastore.
- Prometheus metrics endpoint.

Docker Compose can be used for local development, while Helm can deploy the application and datastore to a local Kubernetes cluster.

### Production Deployment

The production architecture will use a centralized Control Plane and three independent regional data planes.

**Global Control Plane:**
- Separate FastAPI deployment.
- Global endpoint configuration management.
- Regional ownership coordination.
- Configuration distribution.
- Failover coordination.
- Highly available deployment in a primary region with standby capability.

**Regional Data Planes:**

Each region will have an independent Kubernetes cluster containing:
- Regional scheduler.
- Worker deployment.
- Message broker.
- Result processors.
- Regional datastore.
- Prometheus monitoring.

The three regional clusters will operate independently, with asynchronous configuration and reporting synchronization.

### Kubernetes and Infrastructure

Use Terraform to provision:
- Regional EKS clusters.
- VPCs and networking.
- IAM roles and permissions.
- Container registry resources.
- Database infrastructure.
- Supporting messaging infrastructure.

Use Helm to deploy the Control Plane and regional data-plane components independently.

Each regional deployment should span multiple availability zones to tolerate individual availability-zone failures.

Use Kubernetes HPA/KEDA, PodDisruptionBudgets, readiness/liveness probes, resource requests and limits, and cluster autoscaling.

### CI/CD

Use GitHub Actions for:
1. Running unit and integration tests.
2. Building Docker images.
3. Performing container vulnerability scans.
4. Publishing versioned images to ECR.
5. Deploying using Helm.

For multi-region deployment, use progressive rollout: deploy to one region, validate application health and metrics, and then proceed with the remaining regions.

Database migrations should follow backward-compatible expand-and-contract practices.

## 7. Observability

Use Prometheus and Grafana for application and infrastructure monitoring.

Important metrics include:

- Total scheduled health-check jobs.
- Completed health checks per second.
- Scheduler execution lag.
- Message queue depth and processing latency.
- Active worker tasks.
- Health-check execution latency.
- HTTP failure rate.
- Database write failures.
- Missed check intervals.
- Regional monitoring coverage.
- Control Plane availability.

Configure alerts for scheduler inactivity, excessive queue backlog, worker unavailability, database failures, and regional outages.

A synthetic canary endpoint should also be continuously monitored to verify that the monitoring platform itself is functioning correctly.

## 8. Architecture Decisions and Trade-offs

The MVP uses a modular monolith and an in-process scheduler to minimize implementation complexity.

At production scale, the system evolves into a centralized Control Plane and independently scalable regional Data Planes.

This separation provides:

- Independent scaling of API, scheduling, and execution workloads.
- Regional fault isolation.
- Reduced dependency on the global Control Plane during normal check execution.
- Controlled regional failover.
- Better handling of high-volume asynchronous workloads.

The design deliberately avoids introducing distributed scheduling, multi-region database consistency, and messaging infrastructure into the initial MVP.

These components should be introduced as the monitoring workload and availability requirements grow.

The primary design goal is to maintain reliable health-check execution while ensuring that failures in individual components or regions do not interrupt the entire monitoring platform.