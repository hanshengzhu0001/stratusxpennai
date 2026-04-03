# OpenClaw Demo Prompt

Use this in the OpenClaw dashboard chat for the Week 2 demo:

```text
Use the incident_guardrail skill on the latest alert. Work from the Guardrail Console flow exactly: read the plan, follow outputs/alert_latest_browser_playbook.json, open the dashboard, inspect the incident, open feature flags, apply the chosen remediation, refresh the dashboard, then run verify and summarize the final verdict.
```

## More Explicit Rehearsal Prompt

```text
Use the incident_guardrail skill on the latest alert. Follow outputs/alert_latest_browser_playbook.json exactly. Start from the Guardrail Console, inspect the active scenario, open the feature-flag execution surface, click the chosen remediation button, return to the console, run verify, and summarize the predicted-vs-actual result plus what dangerous action was avoided.
```

## Operator Notes

- If the browser bridge is healthy, OpenClaw should follow the playbook end to end.
- If the browser bridge is down, use the same flow manually in the console and still run verify.
- Always keep the explanation focused on:
  - dangerous local reflex rejected
  - safer action chosen before action
  - browser execution
  - verified outcome
