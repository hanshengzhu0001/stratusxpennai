# OpenClaw Incident Guardrail Report

## Guardrail Question

Given a healthcare scheduling latency incident with retry amplification, which of these fixes is safest globally?

## Workflow

- Level: `level_3_browser_k8s_scaffold`
- Phase: `plan`
- Entrypoint: `.venv/bin/python run.py alerts/latest.json --phase demo`

## Incident

- Summary: Scheduling retry pressure above baseline
- Services: portal, scheduling, eligibility
- Pattern matches: retry_amplification, payment_degradation, high_checkout_latency, fairness_breakdown

## Action Strategy

- Planner strategy: `broader_healthcare_access_action_library -> constraint_aware_shortlist -> stratus_ranking`
- Library size: `15`
- Shortlist size: `6`

## Action Library

- `rate_limit_retries`: retry_overload_control (browser)
- `enable_payment_circuit_breaker`: retry_overload_control (browser)
- `increase_retry_backoff`: retry_overload_control (browser)
- `shorten_slot_hold_ttl`: fairness_and_inventory_control (browser)
- `route_to_callback_queue`: graceful_access_fallback (browser)
- `reserve_priority_slots`: fairness_and_inventory_control (browser)
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

- `route_to_callback_queue`: Route a controlled slice of demand to staffed callback instead of letting repeated online attempts starve access.
- `rate_limit_retries`: Throttle appointment-booking retries to break retry amplification.
- `reserve_priority_slots`: Reserve a protected slice of scarce appointment capacity for higher-priority patients and reduce hold concentration.
- `enable_payment_circuit_breaker`: Enable an eligibility fail-fast circuit breaker instead of retrying into a degraded dependency.
- `shorten_slot_hold_ttl`: Shorten slot-hold TTL so trapped appointment inventory is released back to real patients faster.
- `restart_payment`: Restart eligibility workers after backlog pressure is reduced.

## Stratus Ranking

- Rank 1: `rate_limit_retries` (0.95)
- Rank 2: `route_to_callback_queue` (0.94)
- Rank 3: `shorten_slot_hold_ttl` (0.84)
- Rank 4: `reserve_priority_slots` (0.76)
- Rank 5: `enable_payment_circuit_breaker` (0.53)
- Rank 6: `restart_payment` (0.38)

## Browser Workflow

- Open the dedicated OpenClaw execution page.
- Inspect the before-action incident card and chosen remediation.
- Click the single OpenClaw execution button to apply the chosen remediation.
- Run verify and inspect the live verdict on that same page.

## Automation Handoff

- OpenClaw demo mode: `outputs/alert_latest_openclaw_demo.json`
- Browser playbook: `outputs/alert_latest_browser_playbook.json`
- Verify command: `.venv/bin/python run.py alerts/latest.json --phase verify`

## Decision

- Guardrail choice: `rate_limit_retries`
- Executed action: `rate_limit_retries`
- Plan/execution match: `True`
- Confidence: `0.95`
- Dashboard: `http://127.0.0.1:8010/`
- Feature flags: `http://127.0.0.1:8010/feature-flags`

## Predicted vs Actual

- Predicted: `{}`
- Actual: `{}`
- Drift score: `0.00`
