# Design Document: Baseline, Simulator & Comparison Modules for Stratus Incident Response

**Author:** Eason (Zhou Yincheng)
**Date:** 2026-03-27
**Status:** Draft

---

## 1. Problem Statement

The Stratus team is building a prototype that demonstrates Stratus's multi-agent reasoning makes better incident response decisions than naive approaches. To prove this, we need a **control group** (baseline), a **ground-truth engine** (simulator), and a **scoring system** (comparison).

Eason owns all three. The baseline must be deliberately simple and weak — it exists to make Stratus look good by contrast. The simulator must be deterministic so results are reproducible. The comparison must clearly show which approach won and why.

All modules run on **OpenClaw**, the open-source AI agent framework. The baseline leverages OpenClaw's Brain (plain LLM call). The simulator and comparison use Python helper scripts called from OpenClaw skills.

**Target users:** Hansen's pipeline (programmatic), team members during demo (visual output).

---

## 2. Goals

1. Deliver a dead-simple baseline that uses one naive LLM prompt to pick an action — no Stratus, no multi-step reasoning, intentionally weak
2. Deliver a deterministic simulator that maps (scenario + action) to a fixed outcome, same input always produces same output
3. Produce a comparison report: baseline vs Stratus vs ground-truth, with clear metrics
4. Integrate cleanly with Hansen's pipeline via the team's shared I/O format
5. First version ready by Thursday 9 PM checkpoint

**Constraints:**
- Baseline uses OpenClaw's Brain (plain LLM) but NOT Stratus
- Simulator must NOT use any LLM — pure deterministic logic
- Must accept/return JSON matching the shared schema Hansen defines
- Runs as OpenClaw skills in the team's workspace

---

## 3. Proposed Solution

### 3.1 Overview

Three OpenClaw skills, one per deliverable:

**Baseline** — A single `SKILL.md` file with no helper scripts. The markdown body contains a deliberately naive prompt: "Given this incident and these candidate actions, pick the one that seems most reasonable. Return your choice and a short reason." That's it. The OpenClaw Brain makes one LLM call and returns a result. No chain-of-thought, no multi-agent orchestration, no world model. This is the control group.

**Simulator** — A `SKILL.md` that wraps a Python script. The script loads a pre-defined `outcomes.json` file that maps every (scenario_id, action_id) pair to a deterministic outcome (recovery time, blast radius, services affected, success/failure). Same input always produces the same output. Charlie and Stan define what these outcomes are; Eason's job is the engine that looks them up.

**Comparison** — A `SKILL.md` that wraps a Python script. The script takes the baseline result, Stratus result, ground-truth best choice, and simulator outcomes for each, then computes metrics: correct action chosen (yes/no), predicted vs actual outcome delta, blast radius comparison, recovery time comparison. Outputs a structured report.

---

## 4. Technical Design

### 4.1 Tech Stack

| Criteria | OpenClaw SKILL.md (baseline) | Python scripts (simulator/comparison) | Pure Node.js scripts |
| -------- | --- | --- | --- |
| Maturity | Stable — OpenClaw's native format, 5700+ community skills | Excellent — standard language | Excellent |
| Ecosystem | Brain handles LLM calls natively | Rich JSON/data libs, team already uses Python | Would work but team prefers Python |
| Determinism | No — LLM is inherently non-deterministic | Yes — pure dict lookups, no randomness | Yes |
| Complexity | Zero code for baseline — just a prompt | Minimal scripts (~80 + ~60 lines) | Similar |
| Team fit | Everyone on the team uses OpenClaw | Team uses Python (FastAPI in NeverDrop, Python SDK) | OpenClaw is Node.js but team writes Python |

**Decision:**

- **Baseline = pure SKILL.md.** The baseline is a single LLM prompt. Writing code for it would be over-engineering. OpenClaw's Brain handles the LLM call natively — the skill body IS the prompt. This is the simplest possible implementation, which is exactly what we want for a deliberately weak baseline.

- **Simulator + Comparison = Python scripts called from SKILL.md.** The simulator must be deterministic, which rules out LLM. A Python script doing JSON key lookups is the most straightforward approach. The team already writes Python, so there's no learning curve. Node.js would also work, but Python is the team's common language.

- **Why not pure SKILL.md for simulator?** The Brain would introduce non-determinism. Even with temperature=0, LLM outputs can vary across runs. A deterministic outcome engine must be code, not a prompt. The SKILL.md wrapper exists only so Hansen's pipeline can invoke it as a standard OpenClaw skill.

### 4.2 File System Structure

```
skills/
  baseline/                       # Eason's baseline action chooser
    SKILL.md                      # Single file. Naive LLM prompt.
                                  # No scripts, no helpers. That's the point.

  simulator/                      # Eason's deterministic outcome engine
    SKILL.md                      # Skill wrapper — invokes simulate.py
    scripts/
      simulate.py                 # Deterministic lookup: (scenario_id, action_id) → outcome
                                  # ~80 lines. Reads outcomes.json, returns result.
    data/
      outcomes.json               # Pre-defined outcome mappings per scenario
                                  # Authored by Charlie/Stan, consumed by this module

  comparison/                     # Eason's scoring and reporting module
    SKILL.md                      # Skill wrapper — invokes compare.py
    scripts/
      compare.py                  # Computes metrics, formats report. ~60 lines.
```

**Total footprint:** 3 SKILL.md files, 2 Python scripts, 1 JSON data file. Six files.

### 4.3 Encapsulation Design

```
Hansen's Pipeline (main runner)
│
├──► baseline skill
│      Input:  {incident_state, candidate_actions}
│      Output: {chosen_action, reason}
│      Internal: LLM prompt (hidden in SKILL.md body)
│
├──► Tony's stratus_guardrail (parallel with baseline)
│      Input:  {incident_state, candidate_actions}
│      Output: {ranked_actions, confidence, reason, predicted_effect}
│
├──► simulator skill (called for each choice)
│      Input:  {scenario_id, chosen_action}
│      Output: {outcome, recovery_time_mins, blast_radius, services_affected, success}
│      Internal: outcomes.json lookup logic (hidden in simulate.py)
│
└──► comparison skill
       Input:  {baseline_result, stratus_result, ground_truth, simulator_outcomes}
       Output: {winner, metrics_table, summary}
       Internal: scoring weights, metric computation (hidden in compare.py)
```

**Module: baseline**
- Public API: accepts incident state + candidate actions, returns chosen action + reason
- Internal: the LLM prompt wording (deliberately naive)
- Dependencies: OpenClaw Brain (LLM provider), Hansen's shared I/O schema

**Module: simulator**
- Public API: accepts scenario_id + chosen action, returns deterministic outcome
- Internal: outcomes.json structure, lookup logic
- Dependencies: Python 3, Hansen's shared I/O schema, Charlie/Stan's scenario data

**Module: comparison**
- Public API: accepts all results + ground truth, returns metrics report
- Internal: scoring formulas, formatting logic
- Dependencies: Python 3, simulator output format, Hansen's shared I/O schema

**No circular dependencies.** Data flows one way: baseline/stratus → simulator → comparison → final report.

### 4.4 Scalability & Parallelism

- **Parallel operations:** Baseline and Stratus run in parallel (independent, same input, different outputs). Hansen's pipeline should invoke both concurrently.
- **Sequential operations:** Simulator runs after both choosers return. Comparison runs after simulator.
- **Scaling strategy:** Not applicable — hackathon prototype, one scenario at a time, single machine.
- **Async patterns:** Not needed. Each skill call is a single request-response.
- **Caching:** Not needed. Simulator is a fast JSON lookup. Comparison is pure computation.

```
      incident data
        /       \
       /         \
  baseline    stratus_guardrail     ← parallel
       \         /
        \       /
     simulator (once per choice)    ← sequential
            |
       comparison                   ← sequential
            |
       final report
```

### 4.5 Detailed Design

#### Baseline SKILL.md

```yaml
---
name: incident_baseline
description: Naive incident response action chooser. Given an incident and candidate actions, picks one using simple LLM reasoning. No Stratus. Control group for comparison.
---
```

Body (the prompt):

```markdown
# Incident Baseline Chooser

You are a simple incident response assistant. You will receive an incident description and a list of candidate remediation actions.

Pick the single action that seems most reasonable based on surface-level pattern matching. Do NOT perform deep analysis, do NOT consider downstream effects, do NOT simulate outcomes. Just pick what looks right at first glance.

Return your response as JSON:
{
  "chosen_action": "<action_id>",
  "reason": "<one sentence>"
}
```

This prompt is deliberately shallow. It explicitly tells the LLM NOT to think deeply — ensuring the baseline remains weak.

#### Simulator — simulate.py

```python
"""
Deterministic incident outcome simulator.
Maps (scenario_id, action_id) → fixed outcome.
"""
import json
import sys

def simulate(scenario_id: str, action_id: str, outcomes_path: str = "data/outcomes.json") -> dict:
    with open(outcomes_path) as f:
        outcomes = json.load(f)

    key = f"{scenario_id}:{action_id}"
    if key not in outcomes:
        return {"error": f"No outcome defined for {key}"}

    return outcomes[key]

if __name__ == "__main__":
    input_data = json.loads(sys.argv[1])
    result = simulate(input_data["scenario_id"], input_data["chosen_action"])
    print(json.dumps(result))
```

#### Simulator — outcomes.json (example structure)

```json
{
  "scenario_1:rollback_deploy": {
    "outcome": "Service restored. Previous version deployed. No data loss.",
    "recovery_time_mins": 8,
    "blast_radius": 1,
    "services_affected": ["checkout-service"],
    "success": true
  },
  "scenario_1:restart_pods": {
    "outcome": "Pods restarted but root cause persists. Service crashes again after 5 min.",
    "recovery_time_mins": 45,
    "blast_radius": 3,
    "services_affected": ["checkout-service", "payment-service", "inventory-service"],
    "success": false
  },
  "scenario_1:scale_up": {
    "outcome": "More pods added but they also crash. Resources wasted. No recovery.",
    "recovery_time_mins": 60,
    "blast_radius": 2,
    "services_affected": ["checkout-service", "payment-service"],
    "success": false
  }
}
```

Charlie defines which outcomes are correct. The key insight: one action *looks* good locally (restart_pods — obvious first instinct) but is worse globally (root cause persists). Stratus should catch this; the baseline probably won't.

#### Comparison — compare.py

```python
"""
Compare baseline vs Stratus vs ground-truth.
"""
import json
import sys

def compare(baseline_result: dict, stratus_result: dict, ground_truth: dict, simulator_outcomes: dict) -> dict:
    gt_action = ground_truth["best_action"]

    baseline_correct = baseline_result["chosen_action"] == gt_action
    stratus_correct = stratus_result["chosen_action"] == gt_action

    b_outcome = simulator_outcomes.get("baseline", {})
    s_outcome = simulator_outcomes.get("stratus", {})
    gt_outcome = simulator_outcomes.get("ground_truth", {})

    return {
        "winner": "stratus" if stratus_correct and not baseline_correct
                  else "baseline" if baseline_correct and not stratus_correct
                  else "tie",
        "metrics": {
            "baseline": {
                "chose_correct_action": baseline_correct,
                "recovery_time_mins": b_outcome.get("recovery_time_mins"),
                "blast_radius": b_outcome.get("blast_radius"),
                "success": b_outcome.get("success")
            },
            "stratus": {
                "chose_correct_action": stratus_correct,
                "recovery_time_mins": s_outcome.get("recovery_time_mins"),
                "blast_radius": s_outcome.get("blast_radius"),
                "success": s_outcome.get("success")
            },
            "ground_truth": {
                "action": gt_action,
                "recovery_time_mins": gt_outcome.get("recovery_time_mins"),
                "blast_radius": gt_outcome.get("blast_radius")
            }
        },
        "summary": _generate_summary(baseline_correct, stratus_correct, b_outcome, s_outcome)
    }

def _generate_summary(b_correct, s_correct, b_outcome, s_outcome) -> str:
    if s_correct and not b_correct:
        return (f"Stratus chose the correct action. Baseline did not. "
                f"Stratus recovery: {s_outcome.get('recovery_time_mins')}min, "
                f"Baseline recovery: {b_outcome.get('recovery_time_mins')}min.")
    elif b_correct and not s_correct:
        return "Baseline chose correctly. Stratus did not."
    elif b_correct and s_correct:
        return "Both chose the correct action."
    else:
        return "Neither chose the correct action."

if __name__ == "__main__":
    input_data = json.loads(sys.argv[1])
    result = compare(
        input_data["baseline_result"],
        input_data["stratus_result"],
        input_data["ground_truth"],
        input_data["simulator_outcomes"]
    )
    print(json.dumps(result, indent=2))
```

### 4.6 Error Handling

- **Simulator: unknown scenario/action pair** — returns `{"error": "No outcome defined for scenario_1:unknown_action"}`. Pipeline should surface this clearly, not silently fail.
- **Baseline: LLM returns malformed JSON** — Hansen's pipeline should catch parse errors. Not Eason's problem to solve inside the skill.
- **Comparison: missing fields** — `compare.py` uses `.get()` with no defaults; missing fields show as `null` in the report rather than crashing.

---

## 5. Alternatives Considered

### 5.1 All-in-one Single Skill

- **Description:** One SKILL.md containing baseline + simulator + comparison logic together.
- **Pros:** Single entry point, fewer files.
- **Cons:** Mixes an LLM-based module (baseline) with deterministic modules (simulator/comparison). Harder for teammates to call individual pieces. The simulator needs a script — can't be pure SKILL.md.

### 5.2 Three Separate Skills (Recommended — chosen above)

- **Description:** One skill per deliverable, each independently callable.
- **Pros:** Maps directly to the three deliverables in the team plan. Hansen can call each piece independently. Clean separation of LLM (baseline) vs deterministic (simulator/comparison).
- **Cons:** Three folders instead of one. Minimal overhead.

### 5.3 Python Package + Thin Skill Wrappers

- **Description:** All logic in a pip-installable Python package, with SKILL.md files that just import and call it.
- **Pros:** Easiest to unit test outside OpenClaw, portable.
- **Cons:** Over-engineered for a hackathon. The baseline doesn't even have Python code. Adds packaging complexity for zero benefit at this scale.

---

## 6. Security Considerations

- No API keys or secrets in Eason's modules. The baseline uses whatever LLM provider OpenClaw's Brain is already configured with.
- `outcomes.json` contains no sensitive data — just fictional incident scenarios.
- `simulate.py` reads from a local JSON file only. No network calls, no file writes.

---

## 7. Performance Considerations

- **Baseline:** One LLM call. Latency depends on the provider (~1-3 seconds typical).
- **Simulator:** JSON file read + dictionary lookup. Sub-millisecond.
- **Comparison:** Pure arithmetic + string formatting. Sub-millisecond.
- **Bottleneck:** The LLM call in the baseline (and Tony's Stratus call). But these run in parallel, so total wall-clock time is `max(baseline_latency, stratus_latency) + simulator + comparison`.

---

## 8. Milestones

| Milestone | Scope | Dependencies | Verification Criteria |
| --------- | ----- | ------------ | --------------------- |
| M1: Baseline skill | `baseline/SKILL.md` — working naive LLM action chooser | Hansen's shared I/O format defined, OpenClaw running | Feed sample incident + 3 actions, get back a chosen action + reason as JSON |
| M2: Simulator engine | `simulator/` — script + outcomes.json for main scenario | Charlie's scenario spec (which actions exist, which is correct), Stan's scenario data | `python simulate.py '{"scenario_id":"scenario_1","chosen_action":"rollback_deploy"}'` returns deterministic outcome. Run twice, get identical output. |
| M3: Comparison report | `comparison/` — script that scores and formats | M1 + M2 working, Tony's stratus_guardrail output format | Feed baseline result + stratus result + ground truth, get back winner + metrics table + summary |
| M4: Pipeline integration | All three skills callable from Hansen's main runner | Hansen's pipeline ready, all modules tested individually | End-to-end: one scenario runs start-to-finish, final report shows baseline vs Stratus comparison |

---

## 9. Open Questions

- [ ] What is Hansen's exact shared I/O schema? (incident_state, candidate_actions format)
- [ ] What scenario(s) has Charlie finalized? Need scenario_id and action_id list to populate outcomes.json
- [ ] What does Tony's stratus_guardrail output look like? Need to match its format in the comparison module
- [ ] Does Stan's evidence data include the scenario_id field, or does Hansen's pipeline assign it?
- [ ] Does the team want the comparison output as JSON only, or also a human-readable text summary for the demo?

---

*Generated with /design*
