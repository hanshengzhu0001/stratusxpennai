"""
Baseline evaluation pipeline.

Runs Eason's baseline alongside the teammate's Stratus pipeline and compares results.
Uses the same alert input, evidence, classification, and candidate actions.

Usage:
    python run_baseline.py [alerts/latest.json]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def load_local_env(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_local_env()

from agents.baseline import choose_action as baseline_choose
from agents.candidate_actions import generate_candidate_actions
from agents.incident_classifier import classify_incident
from agents.simulator import simulate_action
from config import BASELINE_REPORT_PATH, DEFAULT_EVIDENCE, GROUND_TRUTH_ACTION_ID


def main() -> None:
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("alerts/latest.json")
    if not input_path.exists():
        print(f"Alert file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    payload = json.loads(input_path.read_text())
    evidence = DEFAULT_EVIDENCE
    state = classify_incident(payload, evidence)
    actions = generate_candidate_actions(state)

    print("=" * 60)
    print("BASELINE EVALUATION PIPELINE")
    print("=" * 60)

    # --- Step 1: Baseline chooses an action ---
    print(f"\nIncident: {state['summary']}")
    print(f"Services: {', '.join(state['services'])}")
    print(f"Patterns: {', '.join(state['pattern_matches'])}")
    print(f"Candidate actions: {[a['id'] for a in actions]}")

    baseline_result = baseline_choose(state, actions)
    baseline_action = baseline_result["best_action"]
    print(f"\nBaseline chose: {baseline_action['id']}")
    print(f"  Notes: {baseline_result['notes']}")

    # --- Step 2: Simulate outcome for baseline's choice ---
    baseline_outcome = simulate_action(state, baseline_action)
    print(f"\nSimulated outcome for '{baseline_action['id']}':")
    print(f"  Recovery: {baseline_outcome['recovery']}")
    print(f"  Blast radius: {baseline_outcome['blast_radius']}")
    print(f"  Risk level: {baseline_outcome['risk_level']}")
    print(f"  Latency: {baseline_outcome['latency_p95_ms']}ms (direction: {baseline_outcome['latency_direction']})")
    print(f"  Retry storm risk: {baseline_outcome['retry_storm_risk']}")
    print(f"  Notes: {baseline_outcome['notes']}")

    # --- Step 3: Compare with ground truth (rate_limit_retries) ---
    ground_truth_action = next(a for a in actions if a["id"] == GROUND_TRUTH_ACTION_ID)
    gt_outcome = simulate_action(state, ground_truth_action)

    baseline_is_correct = baseline_action["id"] == GROUND_TRUTH_ACTION_ID

    print(f"\n{'=' * 60}")
    print("COMPARISON: BASELINE vs GROUND TRUTH")
    print(f"{'=' * 60}")
    print(f"  Baseline chose:    {baseline_action['id']} (correct: {baseline_is_correct})")
    print(f"  Ground truth:      rate_limit_retries")
    print(f"  Baseline blast:    {baseline_outcome['blast_radius']}")
    print(f"  GT blast:          {gt_outcome['blast_radius']}")
    print(f"  Baseline recovery: {baseline_outcome['recovery']}")
    print(f"  GT recovery:       {gt_outcome['recovery']}")
    print(f"  Baseline risk:     {baseline_outcome['risk_level']}")
    print(f"  GT risk:           {gt_outcome['risk_level']}")

    # --- Write output ---
    report = {
        "scenario_id": payload.get("id", "alert_latest"),
        "incident_summary": state["summary"],
        "baseline": {
            "chosen_action": baseline_action,
            "outcome": baseline_outcome,
            "chose_correct": baseline_is_correct,
            "notes": baseline_result["notes"],
        },
        "ground_truth": {
            "action": ground_truth_action,
            "outcome": gt_outcome,
        },
    }

    out_path = Path(BASELINE_REPORT_PATH)
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nWrote report to {out_path}")


if __name__ == "__main__":
    main()
