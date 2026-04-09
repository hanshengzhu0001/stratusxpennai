# OpenClaw Workspace Agents

This repository is intended to be used as an OpenClaw workspace.

Primary agent:
- `stratus-incidents`: incident remediation demo agent for the hackathon

Current scope:
- one guarded remediation action per incident
- verify or fallback after that single action
- no live multi-step execution chain yet

Expected workflow:
1. OpenClaw is armed once with the `incident_guardrail` workspace skill.
2. If `state/incident_watch.json` already contains an acknowledged `active_incident` and no final report yet, the agent resumes immediately from the prepared artifacts instead of waiting again.
3. Otherwise, the agent runs `.venv/bin/python run.py alerts/latest.json --phase await-demo` and blocks until the next webhook incident is latched, acknowledged, and converted into demo artifacts.
4. Alertmanager posts a firing incident payload to `tools/alert_receiver.py` at `/alerts`.
5. When the blocking command returns, the agent reads the prepared demo artifacts and continues immediately.
6. The agent reads:
   - `outputs/alert_latest_openclaw_demo.json`
   - `outputs/alert_latest_browser_playbook.json`
7. OpenClaw browser follows the playbook, opens `/openclaw-execution`, clicks the chosen remediation there, and stays on that same page to inspect the shared state and final verdict.
   It must not use `web_fetch` or `url-fetch` against localhost surfaces.
8. If browser control fails, the agent runs `.venv/bin/python run.py alerts/latest.json --phase fallback` instead of stopping or replanning.
9. After fallback, the agent stays on `/openclaw-execution`; the final browser surface updates there to match the written report.
10. If browser control succeeds, the agent runs `.venv/bin/python run.py alerts/latest.json --phase verify`.
11. The report is read from `outputs/` and summarized back to the user.
12. The agent returns to `.venv/bin/python run.py alerts/latest.json --phase await-demo` to wait for the next incident.
