# OpenClaw Browser Playbook

## Goal

Open the dashboard, apply the selected remediation in the feature-flag UI, refresh the dashboard, and then run verify automatically.

## Chosen Action

- Action: `rate_limit_retries`
- Button label: `Apply Retry Rate Limit`
- Button selector: `#action-rate_limit_retries`

## URLs

- Dashboard: `http://127.0.0.1:8010/`
- Feature flags: `http://127.0.0.1:8010/feature-flags`
- State API: `http://127.0.0.1:8010/api/state`

## Steps

- `open`: `http://127.0.0.1:8010/` (capture_before_dashboard)
- `inspect`: `http://127.0.0.1:8010/` (read_before_metrics_and_flags)
- `open`: `http://127.0.0.1:8010/feature-flags` (open_execution_surface)
- `click`: `#action-rate_limit_retries` (apply_chosen_remediation)
- `open`: `http://127.0.0.1:8010/` (capture_after_dashboard)
- `inspect`: `http://127.0.0.1:8010/api/state` (confirm_state_after_action)
- `exec`: `.venv/bin/python run.py alerts\latest.json --phase verify` (run_post_action_verification)
- `read`: `outputs/alert_latest_report.json` (summarize_final_report)
