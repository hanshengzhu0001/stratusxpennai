# Final Demo Script

## Goal

Show that OpenClaw + Stratus does not just automate remediation. It rejects the dangerous reflex, executes the safer move, and verifies the outcome in a live-looking ticket-drop incident.

## 90-Second Version

1. Open the Guardrail Console at `http://127.0.0.1:8010/`.
2. Set the scenario to `Retry Death Spiral`.
3. Explain:
   - major concert ticket drop
   - payment wobbles
   - checkout retries amplify the issue
   - fairness and seat access are now at risk
4. Run planning:
   - `.venv/bin/python run.py alerts/latest.json --phase plan`
5. In the console's `Decision View`, show:
   - shortlist
   - Stratus ranking
   - expected baseline reflex: `restart_payment`
   - guardrail choice: `rate_limit_retries`
6. Move to `Execution View`.
7. Let OpenClaw browser or the operator open `/feature-flags` and apply the chosen action.
8. Run verify:
   - `.venv/bin/python run.py alerts/latest.json --phase verify`
9. In the `Verdict View`, show:
   - before vs after metrics
   - dangerous reflex rejected
   - blast radius stayed contained
   - case is now ready to be written back into the case library

## Longer Spoken Track

### Opening

We are showing a counterfactual remediation guardrail for high-stakes digital drops. In this case, a concert ticket release is under pressure. The obvious fix is to restart payment, but that is exactly the kind of local reflex that can make the whole system worse.

### Observation

The Guardrail Console merges four observation lanes: payment health, queue and fairness, inventory pressure, and browser-visible state. That gives us one operator-facing incident view instead of scattered dashboards.

### Decision

Next, the planner narrows a broad action library into a scenario-specific shortlist. Stratus then ranks only that shortlist. The key point is not that it picks an action. The key point is that it explains why the obvious reflex is dangerous before we act.

### Execution

Now OpenClaw takes over the browser step. It reads the playbook, opens the remediation surface, applies the chosen action, and then we verify the system state again.

### Verdict

Finally, we compare predicted vs actual. If the guardrail was right, retry pressure and latency improve while blast radius stays low. That turns the product into a closed loop, not just a recommendation engine.
