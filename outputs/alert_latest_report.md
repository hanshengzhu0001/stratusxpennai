# OpenClaw Incident Guardrail Report

## Guardrail Question

Given a payment-related latency incident with retry amplification, which of these fixes is safest globally?

## Workflow

- Level: `level_3_browser_k8s_scaffold`
- Phase: `verify`
- Entrypoint: `.venv/bin/python run.py alerts/latest.json --phase verify`

## Incident

- Summary: Retry pressure above baseline
- Services: frontend, checkout, payment
- Pattern matches: payment_degradation

## Candidate Actions

- `restart_payment`: Restart payment pods
- `disable_flag`: Disable the payment feature flag
- `rate_limit_retries`: Rate-limit checkout retries
- `shift_traffic`: Shift traffic to the secondary region

## Stratus Ranking

- Rank 1: `rate_limit_retries` (0.87)
- Rank 2: `restart_payment` (0.72)
- Rank 3: `disable_flag` (0.58)
- Rank 4: `shift_traffic` (0.45)

## Browser Workflow

- Open the dashboard.
- Inspect the incident metrics and active flags.
- Open the feature-flag page.
- Click 'Apply Retry Rate Limit'.
- Refresh the dashboard.
- Summarize before/after state and then run verify phase.

## Automation Handoff

- Browser playbook: `n/a`
- Verify command: `n/a`

## Decision

- Chosen action: `rate_limit_retries`
- Confidence: `0.87`
- Dashboard: `http://127.0.0.1:8010/`
- Feature flags: `http://127.0.0.1:8010/feature-flags`

## Predicted vs Actual

- Predicted: `{"blast_radius": "checkout service only", "error_rate": 0.08, "latency_p95_ms": 1200, "retry_rate": 0.05, "time_to_effect_seconds": 30}`
- Actual: `{"action_id": "rate_limit_retries", "blast_radius": "low", "error_direction": "up", "error_rate": 0.18, "impact": "medium_high", "latency_direction": "down", "latency_p95_ms": 1650, "notes": "Observed live metrics after rate_limit_retries via Prometheus-backed verification.", "recovery": "strong", "retry_rate": 0.1, "retry_storm_risk": "low", "risk_level": "low", "time_to_effect_seconds": 30}`
- Drift score: `0.50`
