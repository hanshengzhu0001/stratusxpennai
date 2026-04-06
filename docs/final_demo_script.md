# Final Demo Script

## Goal

Show that OpenClaw + Stratus does not just automate remediation. It rejects a dangerous local reflex, executes a safer move, and verifies the outcome in a live-looking healthcare access incident.

## 90-Second Version

1. Start in the OpenClaw dashboard chat, not the console.
2. Paste the prompt from `docs/openclaw_demo_prompt.md`.
3. Explain:
   - a high-demand telehealth or specialist-slot release is underway
   - eligibility verification degrades but is not fully down
   - scheduling retries amplify the issue
   - online access and fairness are now at risk
4. Let OpenClaw run:
   - `.venv/bin/python run.py alerts/latest.json --phase demo`
5. In the Guardrail Console, show:
   - the shortlisted remediations
   - Stratus ranking
   - the dangerous reflex: `restart_payment` shown as `Restart Eligibility Service`
   - the guardrail choice
6. Let OpenClaw browser execute the remediation handoff end to end.
7. If browser control fails, let OpenClaw run the saved-plan fallback instead:
   - `.venv/bin/python run.py alerts/latest.json --phase fallback`
8. If browser control succeeds, let OpenClaw run verify:
   - `.venv/bin/python run.py alerts/latest.json --phase verify`
9. In the `Verdict View`, show:
   - before vs after scheduling metrics
   - dangerous reflex rejected
   - blast radius stayed contained
   - the case is ready to be written back into the case library

## Longer Spoken Track

### Opening

We are showing a counterfactual remediation guardrail for healthcare access operations. In this case, a hospital system opens a high-demand telehealth or specialist scheduling window. Eligibility verification becomes slow and flaky. The obvious human reflex is to restart eligibility, but that is exactly the kind of local fix that can make the whole access system worse.

### Observation

The Guardrail Console merges four observation lanes: eligibility health, scheduling retries and abandonment, slot inventory pressure, and browser-visible state. That gives us one operator-facing incident view instead of scattered dashboards.

### Decision

Next, the planner narrows a broad healthcare-access action library into a scenario-specific shortlist. Stratus then ranks only that shortlist. The point is not just that it chooses an action. The point is that it explains why the dangerous reflex is unsafe before we act.

### Execution

Now OpenClaw takes over the browser step. It starts from the generated demo-mode artifact, reads the playbook, opens the dedicated `/openclaw-execution` surface, clicks one stable remediation button, and then we verify the system state again. If browser control flakes out, OpenClaw still completes the saved plan through the fallback command instead of abandoning the demo.

### Verdict

Finally, we compare predicted vs actual. If the guardrail was right, retry pressure and scheduling latency improve while blast radius stays low and patient access stabilizes. That turns the product into a closed loop, not just a recommendation engine.
