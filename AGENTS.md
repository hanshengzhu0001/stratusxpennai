# OpenClaw Workspace Agents

This repository is intended to be used as an OpenClaw workspace.

Primary agent:
- `stratus-incidents`: incident remediation demo agent for the hackathon

Expected workflow:
1. Alertmanager posts a firing incident payload to `tools/alert_receiver.py` at `/alerts`.
2. OpenClaw browser opens the local dashboard and feature-flag UI.
3. OpenClaw uses the `incident_guardrail` workspace skill.
4. The agent runs `.venv/bin/python run.py alerts/latest.json --phase plan` through `exec`.
5. The agent reads `outputs/alert_latest_browser_playbook.json`.
6. OpenClaw browser follows the playbook, clicks the chosen remediation in the feature-flag UI, and refreshes the dashboard.
7. The agent runs `.venv/bin/python run.py alerts/latest.json --phase verify`.
8. The report is read from `outputs/` and summarized back to the user.
