# Workspace Tools

Preferred OpenClaw tool usage in this workspace:

- `exec`: run the local Python workflow entrypoint
- `browser`: follow `outputs/alert_latest_browser_playbook.json` exactly, including the chosen action selector and post-click refresh/inspection steps
- `file` tools: inspect scenarios and generated reports

Default execution command:

`.venv/bin/python run.py alerts/latest.json --phase plan`

Browser handoff artifact:

`outputs/alert_latest_browser_playbook.json`
