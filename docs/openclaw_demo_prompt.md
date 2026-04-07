# OpenClaw Demo Prompt

Primary Week 2 / final-demo prompt for the OpenClaw dashboard chat:

```text
Arm the incident_guardrail skill in background mode for this workspace.
```

The workspace skill now carries the full behavior:

- run `--phase watch`
- wait for a new webhook incident
- run `--phase demo`
- execute through `/openclaw-execution`
- run `verify` or `fallback`
- summarize the verdict
- return to watch mode

## Explicit Long Prompt

```text
Arm the incident_guardrail skill in background mode for this workspace. Run `.venv/bin/python run.py alerts/latest.json --phase watch` and wait until a new pending incident is latched from the Alertmanager webhook. When one appears, run `.venv/bin/python run.py alerts/latest.json --phase demo`, read `outputs/alert_latest_openclaw_demo.json` and `outputs/alert_latest_browser_playbook.json`, then use the browser to open `/openclaw-execution`, inspect the before-action incident card, click the single remediation button chosen by the guardrail, run verify, open `/openclaw-execution?stage=verdict`, and summarize the final verdict with the dangerous reflex that was avoided. If the browser tool fails or times out, do not stop and do not replan. Instead run `.venv/bin/python run.py alerts/latest.json --phase fallback`, read `outputs/alert_latest_report.json`, and summarize the final verdict while explicitly noting that execution used the saved-plan fallback because browser control was unavailable. After summarizing, return to `.venv/bin/python run.py alerts/latest.json --phase watch`.
```

## Shorter Rehearsal Prompt

```text
Arm the incident_guardrail skill and wait for the next webhook incident. When one is latched, run demo mode, execute the chosen remediation in `/openclaw-execution`, run verify, summarize why the dangerous reflex was rejected, then return to watch mode. If browser control fails, run `.venv/bin/python run.py alerts/latest.json --phase fallback` instead of stopping.
```

## Operator Notes

- `--phase watch` is the new arm mode.
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
