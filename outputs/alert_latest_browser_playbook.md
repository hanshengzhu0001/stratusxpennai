# OpenClaw Browser Playbook

## Goal

Open the dedicated OpenClaw execution page, apply the selected remediation with one browser action, then run verify and inspect the verdict page.

## Chosen Action

- Action: `enable_payment_circuit_breaker`
- Button label: `Enable Payment Circuit Breaker`
- Button selector: `#openclaw-demo-run`
- Manual fallback selector: `#action-enable_payment_circuit_breaker`

## URLs

- Dashboard: `http://127.0.0.1:8010/`
- OpenClaw execution: `http://127.0.0.1:8010/openclaw-execution`
- OpenClaw verdict: `http://127.0.0.1:8010/openclaw-execution?stage=verdict`
- Feature flags: `http://127.0.0.1:8010/feature-flags`
- State API: `http://127.0.0.1:8010/api/state`

## Steps

- `open`: `http://127.0.0.1:8010/openclaw-execution` (open_dedicated_execution_surface)
- `inspect`: `http://127.0.0.1:8010/openclaw-execution` (read_before_metrics_and_chosen_action)
- `click`: `#openclaw-demo-run` (apply_chosen_remediation)
- `exec`: `.venv/bin/python run.py alerts/latest.json --phase verify` (run_post_action_verification)
- `open`: `http://127.0.0.1:8010/openclaw-execution?stage=verdict` (open_dedicated_verdict_surface)
- `inspect`: `http://127.0.0.1:8010/api/state` (confirm_state_after_action)
- `read`: `outputs/alert_latest_report.json` (summarize_final_report)
