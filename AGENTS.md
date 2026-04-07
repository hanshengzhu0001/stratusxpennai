# OpenClaw Workspace Agents

This repository is intended to be used as an OpenClaw workspace.

Primary agent:
- `stratus-incidents`: incident remediation demo agent for the hackathon

Expected workflow:
1. OpenClaw is armed once with the `incident_guardrail` workspace skill.
2. The agent runs `.venv/bin/python run.py alerts/latest.json --phase watch` and waits for a latched pending incident.
3. Alertmanager posts a firing incident payload to `tools/alert_receiver.py` at `/alerts`.
4. When a new incident is latched, the agent runs `.venv/bin/python run.py alerts/latest.json --phase demo` through `exec`.
5. The agent reads:
   - `outputs/alert_latest_openclaw_demo.json`
   - `outputs/alert_latest_browser_playbook.json`
6. OpenClaw browser follows the playbook, opens `/openclaw-execution`, clicks the chosen remediation there, and inspects the shared state and verdict surface.
7. If browser control fails, the agent runs `.venv/bin/python run.py alerts/latest.json --phase fallback` instead of stopping or replanning.
8. If browser control succeeds, the agent runs `.venv/bin/python run.py alerts/latest.json --phase verify`.
9. The report is read from `outputs/` and summarized back to the user.
10. The agent returns to `.venv/bin/python run.py alerts/latest.json --phase watch` to wait for the next incident.
