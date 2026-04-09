# OpenClaw Demo Mode

## Goal

Arm OpenClaw once, let it wait for the next firing incident from the Alertmanager webhook, then execute the browser remediation from one dedicated execution page, run verify, inspect the live verdict on that same page, summarize, and return to watch mode.

## Starter Prompt

```text
Use the incident_guardrail skill in this workspace and stay in the same task as a persistent responder. Start by running `.venv/bin/python run.py alerts/latest.json --phase await-demo` and remain idle while it blocks. Only after that command returns should you continue on the single `/openclaw-execution` page, verify or fallback, summarize the verdict, and then return to `.venv/bin/python run.py alerts/latest.json --phase await-demo` again. Use the browser tool only for localhost surfaces and never use web_fetch or url-fetch on 127.0.0.1 URLs. If the browser tool fails once, run fallback immediately instead of trying any localhost fetch. Do not resume stale artifacts before `await-demo` returns.
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

- Status: `armed`
- Armed: `True`
- Pending incident latched: `False`

## Browser Sequence

- Step 1: `open` `http://127.0.0.1:8010/openclaw-execution` (open_dedicated_execution_surface)
- Step 2: `inspect` `http://127.0.0.1:8010/openclaw-execution` (read_before_metrics_and_chosen_action)
- Step 3: `click` `#openclaw-demo-run` (apply_guardrail_choice)
- Step 4: `exec` `.venv/bin/python run.py alerts/latest.json --phase verify` (write_final_verdict)
- Step 5: `inspect` `http://127.0.0.1:8010/openclaw-execution` (inspect_same_page_for_live_verdict_after_verify)

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
