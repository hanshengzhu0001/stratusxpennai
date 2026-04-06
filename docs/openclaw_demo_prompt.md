# OpenClaw Demo Prompt

Primary Week 2 / final-demo prompt for the OpenClaw dashboard chat:

```text
Use the incident_guardrail skill on the latest alert in OpenClaw Demo Mode. Run `.venv/bin/python run.py alerts/latest.json --phase demo`, read `outputs/alert_latest_openclaw_demo.json` and `outputs/alert_latest_browser_playbook.json`, then use the browser to open `/openclaw-execution`, inspect the before-action incident card, click the single remediation button chosen by the guardrail, run verify, open `/openclaw-execution?stage=verdict`, and summarize the final verdict with the dangerous reflex that was avoided. If the browser tool fails or times out, do not stop and do not replan. Instead run `.venv/bin/python run.py alerts/latest.json --phase fallback`, read `outputs/alert_latest_report.json`, and summarize the final verdict while explicitly noting that execution used the saved-plan fallback because browser control was unavailable.
```

## Shorter Rehearsal Prompt

```text
Use the incident_guardrail skill on the latest alert in OpenClaw Demo Mode. Follow outputs/alert_latest_openclaw_demo.json and outputs/alert_latest_browser_playbook.json exactly, open `/openclaw-execution`, click the single remediation button for the chosen action, run verify, and summarize why the dangerous reflex was rejected. If browser control fails, run `.venv/bin/python run.py alerts/latest.json --phase fallback` instead of stopping.
```

## Operator Notes

- If the browser bridge is healthy, OpenClaw should follow the playbook end to end.
- If the browser bridge is down, OpenClaw should run `.venv/bin/python run.py alerts/latest.json --phase fallback` and still finish the report.
- `/openclaw-execution` is the primary browser surface for the final demo.
- `Console Rehearsal: Run Full Flow` is a local backup, not the primary final-demo launch path.
- Always keep the explanation focused on:
  - dangerous local reflex rejected
  - safer action chosen before action
  - browser execution
  - verified outcome
