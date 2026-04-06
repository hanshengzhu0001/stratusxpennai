---
name: incident_guardrail
description: Run the Stratus-powered incident remediation workflow in this repository.
---

# Incident Guardrail Skill

When asked to evaluate an incident scenario in this repo, do the following:

1. Use the `exec` tool in the workspace root.
2. Run:
   `.venv/bin/python run.py alerts/latest.json --phase demo`
3. Read:
   - `outputs/alert_latest_openclaw_demo.json`
   - `outputs/alert_latest_browser_playbook.json`
4. Use the browser tool to follow the playbook exactly:
   open `/openclaw-execution`
   inspect the before-action incident card
   click the single execution button using the provided selector
   open `/openclaw-execution?stage=verdict` after verify
   inspect the state API if needed
5. If the browser tool fails or times out, do not stop and do not replan. Instead run:
   `.venv/bin/python run.py alerts/latest.json --phase fallback`
6. If the browser action succeeds, run:
   `.venv/bin/python run.py alerts/latest.json --phase verify`
7. Read the generated report from `outputs/`.
8. Summarize:
   - incident summary
   - candidate actions
   - Stratus ranking vs baseline ranking
   - chosen action (Stratus) vs baseline choice
   - predicted vs actual
   - before/after browser-visible evidence
   - whether execution used the saved-plan fallback because browser control was unavailable

Do not call Stratus directly from chat.
Always use the local repo workflow.
Prefer the OpenClaw Demo Mode artifact over any console shortcut buttons.
If `.venv/bin/python` does not exist, first explain that the workspace virtual environment has not been created yet.
If `alerts/latest.json` is missing, explain that no alert payload has been received yet and suggest posting a sample alert to `/alerts`.
