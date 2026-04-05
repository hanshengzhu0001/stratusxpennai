# OpenClaw Incident Guardrail Report

## Guardrail Question

Given a payment-related latency incident with retry amplification, which of these fixes is safest globally?

## Workflow

- Level: `level_3_browser_k8s_scaffold`
- Phase: `plan`
- Entrypoint: `.venv/bin/python run.py alerts/latest.json --phase demo`

## Incident

- Summary: Retry pressure above baseline
- Services: frontend, checkout, payment
- Pattern matches: retry_amplification, payment_degradation, high_checkout_latency

## Action Strategy

- Planner strategy: `broader_retail_action_library -> scenario_shortlist -> stratus_ranking`
- Library size: `12`
- Shortlist size: `6`

## Action Library

- `rate_limit_retries`: retry_overload_control (browser)
- `enable_payment_circuit_breaker`: retry_overload_control (browser)
- `increase_retry_backoff`: retry_overload_control (browser)
- `restart_payment`: service_recovery (browser)
- `shift_traffic`: traffic_management (browser)
- `disable_flag`: graceful_degradation (browser)
- `rollback_payment_deploy`: deployment_recovery (k8s)
- `rollback_checkout_deploy`: deployment_recovery (k8s)
- `disable_nonessential_checkout_features`: graceful_degradation (future)
- `serve_stale_catalog_cache`: graceful_degradation (future)
- `failover_to_secondary_region`: traffic_management (k8s)
- `pause_noncritical_background_jobs`: capacity_management (k8s)

## Candidate Actions

- `rate_limit_retries`: Rate-limit checkout retries to break retry amplification.
- `enable_payment_circuit_breaker`: Enable a payment circuit breaker to fail fast instead of retrying into a degraded dependency.
- `increase_retry_backoff`: Increase retry backoff so checkout stops hammering payment during partial degradation.
- `restart_payment`: Restart payment pods to clear local state after backlog is reduced.
- `shift_traffic`: Shift a slice of checkout traffic to the secondary region.
- `disable_flag`: Disable the payment feature flag as a last-resort kill switch.

## Stratus Ranking

- Rank 1: `enable_payment_circuit_breaker` (0.94)
- Rank 2: `increase_retry_backoff` (0.88)
- Rank 3: `rate_limit_retries` (0.82)
- Rank 4: `restart_payment` (0.65)
- Rank 5: `shift_traffic` (0.58)
- Rank 6: `disable_flag` (0.45)

## Browser Workflow

- Open the dedicated OpenClaw execution page.
- Inspect the before-action incident card and chosen remediation.
- Click the single OpenClaw execution button to apply the chosen remediation.
- Run verify and inspect the dedicated verdict page.

## Automation Handoff

- OpenClaw demo mode: `outputs/alert_latest_openclaw_demo.json`
- Browser playbook: `outputs/alert_latest_browser_playbook.json`
- Verify command: `.venv/bin/python run.py alerts/latest.json --phase verify`

## Decision

- Guardrail choice: `enable_payment_circuit_breaker`
- Executed action: `enable_payment_circuit_breaker`
- Plan/execution match: `True`
- Confidence: `0.94`
- Dashboard: `http://127.0.0.1:8010/`
- Feature flags: `http://127.0.0.1:8010/feature-flags`

## Predicted vs Actual

- Predicted: `{}`
- Actual: `{}`
- Drift score: `0.00`
