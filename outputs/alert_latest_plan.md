# OpenClaw Incident Guardrail Report

## Guardrail Question

Given a healthcare scheduling latency incident with retry amplification, which of these fixes is safest globally?

## Workflow

- Level: `level_3_browser_k8s_scaffold`
- Phase: `plan`
- Entrypoint: `.venv/bin/python run.py alerts/latest.json --phase demo`

## Incident

- Summary: Retry pressure above baseline
- Services: portal, scheduling, eligibility
- Pattern matches: 

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

- `enable_payment_circuit_breaker`: Enable an eligibility fail-fast circuit breaker instead of retrying into a degraded dependency.
- `route_to_callback_queue`: Route a controlled slice of demand to staffed callback instead of letting repeated online attempts starve access.
- `shift_traffic`: Shift a slice of patient-portal scheduling traffic to the secondary region.
- `rate_limit_retries`: Throttle appointment-booking retries to break retry amplification.
- `increase_retry_backoff`: Increase booking retry backoff so the portal stops hammering eligibility during partial degradation.
- `shorten_slot_hold_ttl`: Shorten slot-hold TTL so trapped appointment inventory is released back to real patients faster.

## Stratus Ranking

- Rank 1: `route_to_callback_queue` (0.55)
- Rank 2: `shorten_slot_hold_ttl` (0.55)
- Rank 3: `enable_payment_circuit_breaker` (0.55)
- Rank 4: `shift_traffic` (0.55)
- Rank 5: `increase_retry_backoff` (0.55)
- Rank 6: `rate_limit_retries` (0.55)

## Browser Workflow

- Open the dedicated OpenClaw execution page.
- Inspect the before-action incident card and chosen remediation.
- Click the single OpenClaw execution button to apply the chosen remediation.
- Run verify and inspect the live verdict on that same page.
- Do not use web_fetch or url-fetch on localhost URLs; use the browser tool only.

## Automation Handoff

- OpenClaw demo mode: `outputs/alert_latest_openclaw_demo.json`
- Browser playbook: `outputs/alert_latest_browser_playbook.json`
- Verify command: `.venv/bin/python run.py alerts/latest.json --phase verify`

## Decision

- Guardrail choice: `route_to_callback_queue`
- Executed action: `route_to_callback_queue`
- Plan/execution match: `True`
- Confidence: `0.55`
- Dashboard: `http://127.0.0.1:8010/`
- Feature flags: `http://127.0.0.1:8010/feature-flags`

## Predicted vs Actual

- Predicted: `{}`
- Actual: `{}`
- Drift score: `0.00`
