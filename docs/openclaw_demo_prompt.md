# OpenClaw Demo Prompt

Primary Week 2 / final-demo prompt for the OpenClaw dashboard chat:

```text
Use the incident_guardrail skill in this workspace and stay in the same task until the workflow is complete. Start by running `.venv/bin/python run.py alerts/latest.json --phase await-demo`, let that command block until the next incident is latched and the demo artifacts are prepared, then continue immediately through browser execution, verification or fallback, summary, and return to await-demo mode. Do not stop after `await-demo` returns.
```

The workspace skill now carries the full behavior:

- run `--phase await-demo`
- wait for a new webhook incident
- auto-acknowledge it and prepare demo artifacts
- execute one guarded action through `/openclaw-execution`
- run `verify` or `fallback`
- summarize the verdict
- return to await-demo mode

Current scope note:

- this is a single-action workflow today
- OpenClaw executes one chosen remediation per incident
- multi-step or three-action sequencing is not active in the live path yet

## Explicit Long Prompt

```text
Use the incident_guardrail skill in this workspace and stay in the same task until the workflow is complete. Run `.venv/bin/python run.py alerts/latest.json --phase await-demo` and wait until the next webhook incident is latched and the demo artifacts are prepared. Then read `outputs/alert_latest_openclaw_demo.json` and `outputs/alert_latest_browser_playbook.json`, use the browser to open `/openclaw-execution`, inspect the before-action incident card, click the single remediation button chosen by the guardrail, run verify, open `/openclaw-execution?stage=verdict`, and summarize the final verdict with the dangerous reflex that was avoided. If the browser tool fails or times out, do not stop and do not replan. Instead run `.venv/bin/python run.py alerts/latest.json --phase fallback`, then open `/openclaw-execution?stage=verdict`, read `outputs/alert_latest_report.json`, and summarize the final verdict while explicitly noting that execution used the saved-plan fallback because browser control was unavailable. After summarizing, return to `.venv/bin/python run.py alerts/latest.json --phase await-demo`. Do not stop after `await-demo` returns.
```

## Shorter Rehearsal Prompt

```text
Use the incident_guardrail skill in this workspace and stay in the same task until the workflow is complete. Run `.venv/bin/python run.py alerts/latest.json --phase await-demo` and wait for the next incident to be prepared for execution. Then execute the chosen remediation in `/openclaw-execution`, run verify, summarize why the dangerous reflex was rejected, and return to await-demo mode. If browser control fails, run `.venv/bin/python run.py alerts/latest.json --phase fallback`, then open `/openclaw-execution?stage=verdict` before summarizing. Do not stop after `await-demo` returns.
```

## Resume Active Incident Prompt

```text
Use the incident_guardrail skill in this workspace. An incident is already acknowledged and the demo artifacts are prepared. Resume now by reading `outputs/alert_latest_openclaw_demo.json` and `outputs/alert_latest_browser_playbook.json`, executing the browser step in `/openclaw-execution`, then running verify or fallback. If fallback is used, still open `/openclaw-execution?stage=verdict` before summarizing the verdict and returning to await-demo mode.
```

## Operator Notes

- `--phase await-demo` is the preferred arm mode.
- `--phase idle` resets the watcher back to a clean idle state.
- If the watcher already shows `Investigating active incident`, use the `Resume Active Incident Prompt` instead of restarting services.
- If the browser bridge is healthy, OpenClaw should follow the playbook end to end after a pending incident is latched.
- If the browser bridge is down, OpenClaw should run `.venv/bin/python run.py alerts/latest.json --phase fallback` and still finish the report.
- `/openclaw-execution` is the primary browser surface for the final demo.
- `Console Rehearsal: Run Full Flow` is a local backup, not the primary final-demo launch path.
- The intended demo rhythm is:
  - arm OpenClaw once
  - trigger the incident through the webhook / Alertmanager path
  - let OpenClaw wake up and handle it
- Always keep the explanation focused on:
  - dangerous local reflex rejected
  - safer action chosen before action
  - browser execution
  - verified outcome
