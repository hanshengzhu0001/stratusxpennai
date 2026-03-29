---
name: incident_guardrail
description: Run the Stratus-powered incident remediation workflow in this repository.
---

# Incident Guardrail Skill

When asked to evaluate an incident scenario in this repo, do the following:

1. Use the `exec` tool in the workspace root.
2. Run:
   `.venv/bin/python run.py alerts/latest.json --phase plan`
3. Read `outputs/alert_latest_browser_playbook.json`.
4. Use the browser tool to follow the playbook exactly:
   open the dashboard
   inspect the incident
   open feature flags
   click the chosen remediation button using the provided selector
   refresh the dashboard
   inspect the state API if needed
5. After the browser action, run:
   `.venv/bin/python run.py alerts/latest.json --phase verify`
6. Read the generated report from `outputs/`.
7. Summarize:
   - incident summary
   - candidate actions
   - Stratus ranking
   - chosen action
   - predicted vs actual
   - before/after browser-visible evidence

Do not call Stratus directly from chat.
Always use the local repo workflow.
If `.venv/bin/python` does not exist, first explain that the workspace virtual environment has not been created yet.
If `alerts/latest.json` is missing, explain that no alert payload has been received yet and suggest posting a sample alert to `/alerts`.
