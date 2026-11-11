# Runbook

## Health check service stops executing checks

### Alerts covered

- `HealthCheckServiceDown`: Prometheus cannot scrape `/metrics`.
- `HealthCheckSchedulerStalled`: the scheduler has not completed a database scan for over 30 seconds.
- `HealthChecksStopped`: endpoints are registered, but no result has been persisted within twice the longest configured check interval.
- `HealthCheckSchedulerScanErrors`: the scheduler reported database scan failures.
- `HealthCheckResultPersistenceErrors`: writing check results to the database failed.
- `HealthCheckRetentionCleanupFailing`: expired result cleanup failed and old rows may be accumulating.

### Impact

Checks may be stale or missing. Endpoint current states and transition history may not reflect the targets' current health. The API may still be available even when background checks have stopped.

### Triage

The commands below assume the Helm release is named `health-check-service` and was installed in the `health-monitor` namespace. If you used another release name or namespace, substitute the names accordingly.

1. Check pod status, placement, and recent events:

   ```sh
   kubectl get pods -n health-monitor -o wide
   kubectl get events -n health-monitor --sort-by=.lastTimestamp
   kubectl describe deployment/health-check-service -n health-monitor
   ```

   Check for image pull failures, restart loops, failed probes, pending PVCs, or scheduling/resource errors. Look at the app container's logs, including its previous instance if it restarted:

   ```sh
   kubectl logs deployment/health-check-service -n health-monitor --since=15m
   kubectl logs deployment/health-check-service -n health-monitor --previous
   ```

   Look for scheduler scan errors, health-check task errors, database connection errors, and result persistence failures.

2. Check the app's health and metrics from your workstation. In a separate terminal, keep this port-forward running:

   ```sh
   kubectl port-forward deployment/health-check-service 8000:8000 -n health-monitor
   ```

   Then query the forwarded endpoints:

   ```sh
   curl -i http://localhost:8000/health
   curl -i http://localhost:8000/ready
   curl -s http://localhost:8000/metrics
   ```

   `/health` confirms the HTTP application is responding. `/ready` confirms it can query the database. In `/metrics`, inspect:

   - `health_check_scheduler_last_scan_timestamp_seconds`
   - `health_check_last_completion_timestamp_seconds`
   - `active_monitored_endpoints`
   - `health_check_scheduler_scan_errors_total`
   - `health_check_persistence_errors_total`
   - `health_check_retention_cleanup_errors_total`

3. Check PostgreSQL's StatefulSet, pod, PVC, logs, and readiness:

   ```sh
   kubectl get statefulset,pods,pvc,service -n health-monitor
   kubectl describe statefulset/health-check-service-postgresql -n health-monitor
   kubectl logs statefulset/health-check-service-postgresql -n health-monitor --since=15m
   ```

   If the database pod is running, verify PostgreSQL readiness inside its container:

   ```sh
   kubectl exec statefulset/health-check-service-postgresql -n health-monitor -- sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
   ```

   If `/ready` returns 503 or scheduler scan errors increase, restore database connectivity before restarting the app. Check the PVC status and cluster storage events for mount or capacity problems.

4. Confirm the scheduler is enabled without printing database credentials:

   ```sh
   kubectl exec deployment/health-check-service -n health-monitor -- printenv SCHEDULER_ENABLED
   ```

   The expected value is `true`. If it is false, update the Helm value `app.env.schedulerEnabled` to `"true"` and run `helm upgrade`.

5. Check endpoint status and recent result history through the API. For a known endpoint ID:

   ```sh
   curl -s http://localhost:8000/api/v1/endpoints
   curl -s http://localhost:8000/api/v1/endpoints/1/history
   ```

   Verify that the endpoint has not been deleted and that its `interval_seconds` is the expected value. The no-check alert waits for twice the largest interval, so endpoints with long intervals are not incorrectly treated as overdue.

### Recovery

1. Resolve the cause shown by logs and metrics, such as database unavailability or `SCHEDULER_ENABLED=false`.
2. If the scheduler remains stalled after PostgreSQL and the PVC are healthy, restart only the app Deployment:

   ```sh
   kubectl rollout restart deployment/health-check-service -n health-monitor
   kubectl rollout status deployment/health-check-service -n health-monitor --timeout=120s
   ```

   Do not delete the PostgreSQL StatefulSet's PVC. It contains the database data.
3. Wait for a scheduler scan and a subsequent check. Confirm the scan and completion timestamps advance in `/metrics`.
4. Confirm the endpoint's `last_checked_at` and history advance:

   ```sh
   curl -s http://localhost:8000/api/v1/endpoints
   curl -s http://localhost:8000/api/v1/endpoints/1/history
   ```
5. Verify the Prometheus alerts resolve. If checks still do not resume, preserve pod descriptions, events, app/database logs, and metric output for diagnosis rather than repeatedly restarting.

### Escalation

Escalate with the alert name, time of last successful scheduler scan and persisted check, app/database logs around the failure, and whether `/ready` succeeds. Do not include `.env` contents or database credentials.

## Alert thresholds and assumptions

Rules are in `monitoring/health-check-alerts.yml`. The `HealthChecksStopped` alert compares the last persisted completion with twice the largest configured endpoint interval; this avoids paging just because a long-interval endpoint is not due yet. Scheduler stalling is detected independently of endpoint count. Prometheus must scrape the service using the job name `health-check-service` and load the rule file. `monitoring/prometheus.yml` is a starter scrape/rule configuration; configure your Prometheus/Alertmanager notification receivers separately.

## Result retention

Detailed health-check result rows are retained for `CHECK_RESULT_RETENTION_DAYS` (30 days by default). The scheduler performs cleanup approximately once per day, beginning 24 hours after startup. Cleanup only deletes rows from `health_check_results`; monitored endpoint records and state-transition history are retained. The deletion total and cleanup failures are exposed as `health_check_retention_deleted_results_total` and `health_check_retention_cleanup_errors_total`.
