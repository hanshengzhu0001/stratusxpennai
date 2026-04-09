---
name: incident_guardrail
description: Run the Stratus-powered incident remediation workflow in this repository.
---

# Incident Guardrail Skill

When asked to evaluate an incident scenario in this repo, do the following:

1. Use the `exec` tool in the workspace root.
2. First check whether `state/incident_watch.json` already contains an acknowledged `active_incident` and no final report has been written yet.
3. If an acknowledged active incident already exists, resume immediately from the prepared artifacts instead of waiting again.
4. Otherwise, arm the workflow by running one blocking command:
   `.venv/bin/python run.py alerts/latest.json --phase await-demo`
5. That command should wait for the next webhook incident and return only after the pending incident is acknowledged and the demo artifacts are written.
6. Read:
   - `outputs/alert_latest_openclaw_demo.json`
   - `outputs/alert_latest_browser_playbook.json`
7. Use the browser tool to follow the playbook exactly:
   open `/openclaw-execution`
   inspect the before-action incident card
   click the single execution button using the provided selector
   stay on `/openclaw-execution` after verify or fallback
   inspect the live verdict on that same page
8. If the browser tool fails or times out, do not stop and do not replan. Instead run:
   `.venv/bin/python run.py alerts/latest.json --phase fallback`
9. After fallback completes, stay on `/openclaw-execution` and inspect the final verdict there.
10. If the browser action succeeds, run:
   `.venv/bin/python run.py alerts/latest.json --phase verify`
11. Read the generated report from `outputs/`.
12. Summarize:
   - incident summary
   - candidate actions
   - Stratus ranking
   - chosen action
   - predicted vs actual
   - before/after browser-visible evidence
   - whether execution used the saved-plan fallback because browser control was unavailable
13. Return to:
   `.venv/bin/python run.py alerts/latest.json --phase await-demo`

Do not call Stratus directly from chat.
Always use the local repo workflow.
Prefer the OpenClaw Demo Mode artifact over any console shortcut buttons.
This skill currently executes one guarded remediation action per incident, then verifies or falls back.
If `.venv/bin/python` does not exist, first explain that the workspace virtual environment has not been created yet.
If `alerts/latest.json` is missing, explain that no alert payload has been received yet and suggest posting a sample alert to `/alerts`.
