# OpenClaw Demo Mode

## Goal

Arm OpenClaw once, let it wait for the next firing incident from the Alertmanager webhook, then execute the browser remediation from one dedicated execution page, run verify, summarize the final verdict, and return to watch mode.

## Starter Prompt

```text
Monitor this workspace in background mode for new webhook incidents. Follow the local workflow described in AGENTS.md and `skills/incident_guardrail/SKILL.md` in this repo. Start by running `.venv/bin/python run.py alerts/latest.json --phase await-demo` so the workflow waits for the next incident and immediately prepares demo artifacts when it arrives.
```

## Demo Contract

- Arm watcher first: `.venv/bin/python run.py alerts/latest.json --phase watch`
- Preferred blocking arm command: `.venv/bin/python run.py alerts/latest.json --phase await-demo`
- When incident is latched, run: `.venv/bin/python run.py alerts/latest.json --phase demo`
- Verify after browser action: `.venv/bin/python run.py alerts/latest.json --phase verify`
- Fallback if browser control fails: `.venv/bin/python run.py alerts/latest.json --phase fallback`
- Return to watch after summary: `True`
- Console buttons are fallback only: `True`

## Watch State

- Status: `idle`
- Armed: `False`
- Pending incident latched: `False`

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
