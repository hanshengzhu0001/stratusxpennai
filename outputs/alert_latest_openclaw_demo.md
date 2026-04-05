# OpenClaw Demo Mode

## Goal

Start from OpenClaw chat, generate the guardrail plan, execute the browser remediation from one dedicated execution page, run verify, and summarize the final verdict without using the console shortcut buttons.

## Starter Prompt

```text
Use the incident_guardrail skill on the latest alert in OpenClaw Demo Mode. Run `.venv/bin/python run.py alerts/latest.json --phase demo`, read `outputs/alert_latest_openclaw_demo.json` and `outputs/alert_latest_browser_playbook.json`, then use the browser to open the dedicated OpenClaw execution page, inspect the before-action incident card, click the single execution button for the chosen remediation, run verify, inspect the verdict page, and summarize the final verdict with the dangerous reflex that was avoided. If the browser tool fails or times out, do not stop and do not replan. Instead run `.venv/bin/python run.py alerts/latest.json --phase fallback`, then read `outputs/alert_latest_report.json` and summarize the final verdict while explicitly noting that execution used the saved-plan fallback because browser control was unavailable.
```

## Demo Contract

- Run first: `.venv/bin/python run.py alerts/latest.json --phase demo`
- Verify after browser action: `.venv/bin/python run.py alerts/latest.json --phase verify`
- Fallback if browser control fails: `.venv/bin/python run.py alerts/latest.json --phase fallback`
- Console buttons are fallback only: `True`

## Browser Sequence

- Step 1: `open` `http://127.0.0.1:8010/openclaw-execution` (open_dedicated_execution_surface)
- Step 2: `inspect` `http://127.0.0.1:8010/openclaw-execution` (read_before_metrics_and_chosen_action)
- Step 3: `click` `#openclaw-demo-run` (apply_guardrail_choice)
- Step 4: `exec` `.venv/bin/python run.py alerts/latest.json --phase verify` (write_final_verdict)
- Step 5: `open` `http://127.0.0.1:8010/openclaw-execution?stage=verdict` (inspect_final_verdict_surface)
- Step 6: `inspect` `http://127.0.0.1:8010/api/state` (confirm_shared_state_after_action)

## Fallback Contract

- Trigger: browser tool times out, fails to load the dashboard, or loses browser control
- Command: `.venv/bin/python run.py alerts/latest.json --phase fallback`
- Final summary note: execution used the saved-plan fallback because browser control was unavailable

## Final Summary Must Include

- incident summary
- dangerous local reflex rejected
- chosen safer action
- before and after browser-visible evidence
- predicted versus actual drift
