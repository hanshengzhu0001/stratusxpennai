"""
Incident response pipeline with scenario profiles, baseline toggle,
evaluation agent, and case library.

Usage:
    python run.py [alerts/latest.json] [--phase plan|verify|auto]
                  [--mode stratus|baseline] [--scenario SCENARIO_ID]
                  [--list-scenarios]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from config import RETRY_RISK_LOW_MAX
from agents.baseline import choose_action as baseline_choose
from agents.candidate_actions import generate_candidate_actions
from agents.evaluator import evaluate as evaluate_verdict
from agents.incident_classifier import classify_incident
from agents.simulator import compare_prediction_to_actual, normalize_actual, normalize_prediction, simulate_action
from scenarios import list_scenarios, load_scenario
from tools.case_library import save_case
from tools.execute_action import execute_action
from tools.prometheus_client import collect_evidence
from tools.runtime_state import control_plane_urls, load_state, save_state
from tools.stratus_guardrail import rank_actions


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


def main() -> None:
    load_local_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("input_path", nargs="?", default="alerts/latest.json")
    parser.add_argument("--phase", choices=["plan", "verify", "auto"], default="auto")
    parser.add_argument("--mode", choices=["stratus", "baseline"], default="stratus")
    parser.add_argument("--scenario", default=None,
                        help="Scenario ID. Use --list-scenarios to see options.")
    parser.add_argument("--list-scenarios", action="store_true",
                        help="List available scenarios and exit.")
    args = parser.parse_args()

    if args.list_scenarios:
        for sid in list_scenarios():
            print(sid)
        return

    # Load scenario if specified
    scenario = None
    if args.scenario:
        try:
            scenario = load_scenario(args.scenario)
        except (FileNotFoundError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

    input_path = Path(args.input_path)
    payload = json.loads(input_path.read_text())
    source_type, state, evidence = load_incident_input(payload, scenario)
    scenario_id = args.scenario or payload.get("id", "alert_latest")

    # Reset runtime state to scenario's initial state
    if scenario:
        save_state(scenario["initial_state"])

    if args.phase == "plan":
        plan = build_plan_report(
            payload, scenario_id, source_type, input_path,
            state, evidence, args.mode, scenario,
        )
        write_outputs(plan, scenario_id, "plan")
        write_browser_playbook(plan, scenario_id)
        print(json.dumps(plan, indent=2))
        print(f"Wrote plan to outputs/{scenario_id}_plan.json")
        return

    if args.phase == "verify":
        plan = load_saved_plan(scenario_id)
        verify = build_verify_report(
            payload=payload,
            scenario_id=scenario_id,
            source_type=source_type,
            input_path=input_path,
            current_state=state,
            current_evidence=evidence,
            plan=plan,
            execution=None,
            mode_label="browser_verify",
            scenario=scenario,
        )
        write_outputs(verify, scenario_id, "report")
        print(json.dumps(verify, indent=2))
        print(f"Wrote report to outputs/{scenario_id}_report.json")
        return

    # auto mode
    plan = build_plan_report(
        payload, scenario_id, source_type, input_path,
        state, evidence, args.mode, scenario,
    )
    chosen = plan["best_action"]
    execution = execute_action(chosen)

    refreshed_evidence = collect_evidence(payload)
    refreshed_state = classify_incident(payload, refreshed_evidence)

    report = build_verify_report(
        payload=payload,
        scenario_id=scenario_id,
        source_type=source_type,
        input_path=input_path,
        current_state=refreshed_state,
        current_evidence=refreshed_evidence,
        plan=plan,
        execution=execution,
        mode_label="auto_apply",
        scenario=scenario,
    )

    write_outputs(plan, scenario_id, "plan")
    write_browser_playbook(plan, scenario_id)
    write_outputs(report, scenario_id, "report")
    print(json.dumps(report, indent=2))
    print(f"Wrote report to outputs/{scenario_id}_report.json")


def build_plan_report(
    payload: dict,
    scenario_id: str,
    source_type: str,
    input_path: Path,
    state: dict,
    evidence: dict,
    mode: str = "stratus",
    scenario: dict | None = None,
) -> dict:
    # Use scenario candidate actions if available
    if scenario:
        actions = scenario["candidate_actions"]
    else:
        actions = generate_candidate_actions(state)

    # Always run BOTH rankers
    ranking = rank_actions(state, actions)
    baseline_ranking = baseline_choose(state, actions)

    stratus_chosen = normalize_best_action(ranking["best_action"], actions)
    baseline_chosen = normalize_best_action(baseline_ranking["best_action"], actions)

    # Mode switch: which ranker is primary?
    if mode == "baseline":
        primary_chosen = baseline_chosen
        primary_confidence = baseline_ranking["overall_confidence"]
    else:
        primary_chosen = stratus_chosen
        primary_confidence = ranking["overall_confidence"]

    playbook_path = f"outputs/{scenario_id}_browser_playbook.json"
    return {
        "workflow": {
            "orchestrator": "openclaw",
            "entrypoint": f".venv/bin/python run.py {input_path} --phase plan --mode {mode}"
                          + (f" --scenario {scenario_id}" if scenario else ""),
            "workspace_skill": "incident_guardrail",
            "level": "level_3_browser_k8s_scaffold",
            "phase": "plan",
        },
        "source_type": source_type,
        "input_path": str(input_path),
        "scenario_id": scenario_id,
        "mode": mode,
        "incident_summary": state["summary"],
        "observed_condition": {
            "services": state.get("services", []),
            "metrics": state.get("metrics", {}),
            "logs": state.get("logs", []),
            "traces": state.get("traces", []),
            "pattern_matches": state.get("pattern_matches", []),
            "prometheus": evidence.get("prometheus", {}),
            "browser_targets": control_plane_urls(
                os.environ.get("CONTROL_PLANE_BASE_URL", "http://127.0.0.1:8010")
            ),
        },
        "candidate_actions": actions,
        "stratus_ranking": ranking["ranked_actions"],
        "best_action": primary_chosen,
        "overall_confidence": primary_confidence,
        "predicted_effects_by_action": ranking["predicted_effects_by_action"],
        # Baseline fields (always present for comparison)
        "baseline_ranking": baseline_ranking["ranked_actions"],
        "baseline_best_action": baseline_chosen,
        "baseline_confidence": baseline_ranking["overall_confidence"],
        "baseline_notes": baseline_ranking["notes"],
        "browser_workflow": browser_workflow(primary_chosen),
        "artifacts": {
            "browser_playbook": playbook_path,
            "verify_command": f".venv/bin/python run.py {input_path} --phase verify"
                              + (f" --scenario {scenario_id}" if scenario else ""),
        },
        "kubernetes_plan": kubernetes_plan(primary_chosen),
        "notes": ranking.get("notes", []),
    }


def build_verify_report(
    payload: dict,
    scenario_id: str,
    source_type: str,
    input_path: Path,
    current_state: dict,
    current_evidence: dict,
    plan: dict,
    execution: dict | None,
    mode_label: str,
    scenario: dict | None = None,
) -> dict:
    chosen = plan["best_action"]
    predicted = plan["predicted_effects_by_action"].get(chosen["id"], {})

    # When scenario is provided, use deterministic simulated outcomes
    # instead of re-collecting evidence (which returns unchanged mock data).
    if scenario:
        actual = simulate_action(
            {"metrics": plan["observed_condition"]["metrics"]},
            chosen,
            scenario,
        )
    else:
        actual = actual_outcome_from_evidence(
            before_metrics=plan["observed_condition"]["metrics"],
            after_metrics=current_evidence.get("metrics", current_evidence),
            action_id=chosen["id"],
        )
    drift = compare_prediction_to_actual(chosen["id"], predicted, actual)

    # Baseline comparison
    baseline_chosen = plan.get("baseline_best_action", {})
    baseline_vs_stratus = {
        "stratus_chose": plan.get("stratus_ranking", [{}])[0].get("id", "unknown")
            if plan.get("stratus_ranking") else "unknown",
        "baseline_chose": baseline_chosen.get("id", "unknown"),
        "same_choice": chosen["id"] == baseline_chosen.get("id"),
        "stratus_confidence": plan["overall_confidence"],
        "baseline_confidence": plan.get("baseline_confidence", 0),
    }

    report = {
        "workflow": {
            "orchestrator": "openclaw",
            "entrypoint": f".venv/bin/python run.py {input_path} --phase verify",
            "workspace_skill": "incident_guardrail",
            "level": "level_3_browser_k8s_scaffold",
            "phase": "verify",
            "mode": mode_label,
        },
        "source_type": source_type,
        "input_path": str(input_path),
        "scenario_id": scenario_id,
        "mode": plan.get("mode", "stratus"),
        "incident_summary": current_state["summary"],
        "observed_condition": {
            "services": current_state.get("services", []),
            "metrics": current_state.get("metrics", {}),
            "logs": current_state.get("logs", []),
            "traces": current_state.get("traces", []),
            "pattern_matches": current_state.get("pattern_matches", []),
            "prometheus": current_evidence.get("prometheus", {}),
            "browser_targets": control_plane_urls(
                os.environ.get("CONTROL_PLANE_BASE_URL", "http://127.0.0.1:8010")
            ),
        },
        "candidate_actions": plan["candidate_actions"],
        "stratus_ranking": plan["stratus_ranking"],
        "best_action": chosen,
        "overall_confidence": plan["overall_confidence"],
        "predicted_effects_by_action": plan["predicted_effects_by_action"],
        "browser_workflow": browser_workflow(chosen),
        "kubernetes_plan": kubernetes_plan(chosen),
        "execution": execution or {
            "executor": "browser_expected",
            "mode": "awaiting_or_completed_browser_action",
            "action_id": chosen["id"],
            "control_plane_urls": control_plane_urls(
                os.environ.get("CONTROL_PLANE_BASE_URL", "http://127.0.0.1:8010")
            ),
            "state_after_action": load_state(),
        },
        "actual_outcome": actual,
        "predicted_vs_actual": {
            "predicted": predicted,
            "actual": actual,
            "normalized_predicted": normalize_prediction(predicted),
            "normalized_actual": normalize_actual(actual),
            "drift": drift,
        },
        "baseline_vs_stratus": baseline_vs_stratus,
        "baseline_ranking": plan.get("baseline_ranking", []),
        "baseline_best_action": plan.get("baseline_best_action", {}),
        "baseline_confidence": plan.get("baseline_confidence", 0),
        "baseline_notes": plan.get("baseline_notes", []),
        "evidence_summary": current_evidence,
        "notes": plan.get("notes", []),
    }

    # --- Evaluation + Case Library (when scenario is available) ---
    if scenario:
        verdict = evaluate_verdict(plan, actual, scenario)
        case_id = save_case(verdict, plan, scenario)
        verdict["case_id"] = case_id if case_id else "unsaved"
        write_outputs(verdict, scenario_id, "verdict")

        report["verdict"] = verdict
        report["case_id"] = verdict["case_id"]

    return report


def render_markdown(report: dict) -> str:
    chosen = report["best_action"]
    predicted = report.get("predicted_vs_actual", {}).get("predicted", {})
    actual = report.get("actual_outcome", {})
    drift = report.get("predicted_vs_actual", {}).get("drift", {"score": 0.0})
    actions = "\n".join(
        f"- `{action['id']}`: {action['description']}" for action in report["candidate_actions"]
    )
    rankings = "\n".join(
        f"- Rank {item['rank']}: `{item['id']}` ({item['confidence']:.2f})"
        for item in report["stratus_ranking"]
    )
    browser_steps = "\n".join(f"- {step}" for step in report["browser_workflow"]["steps"])
    playbook = report.get("artifacts", {}).get("browser_playbook")

    # Baseline section
    baseline_section = ""
    bvs = report.get("baseline_vs_stratus", {})
    if bvs:
        baseline_section = f"""
## Baseline vs Stratus

- Stratus chose: `{bvs.get('stratus_chose', 'n/a')}`
- Baseline chose: `{bvs.get('baseline_chose', 'n/a')}`
- Same choice: `{bvs.get('same_choice', 'n/a')}`
- Stratus confidence: `{bvs.get('stratus_confidence', 'n/a')}`
- Baseline confidence: `{bvs.get('baseline_confidence', 'n/a')}`
"""

    # Verdict section (new)
    verdict_section = ""
    verdict = report.get("verdict", {})
    if verdict:
        vd = verdict.get("verdict", {})
        dc = verdict.get("decision_comparison", {})
        verdict_section = f"""
## Verdict

- Classification: `{vd.get('classification', 'n/a')}`
- Dangerous reflex rejected: `{vd.get('dangerous_reflex_rejected', 'n/a')}`
- Safe action chosen: `{vd.get('safe_action_chosen', 'n/a')}`
- Blast radius avoided: `{vd.get('blast_radius_avoided', 'n/a')}`
- Ground truth: `{dc.get('ground_truth', 'n/a')}`
- Stratus correct: `{dc.get('stratus_correct', 'n/a')}`
- Baseline correct: `{dc.get('baseline_correct', 'n/a')}`

### Narrative

{vd.get('narrative', 'No narrative generated.')}
"""

    return f"""# OpenClaw Incident Guardrail Report

## Workflow

- Level: `{report['workflow']['level']}`
- Phase: `{report['workflow']['phase']}`
- Entrypoint: `{report['workflow']['entrypoint']}`
- Mode: `{report.get('mode', 'stratus')}`

## Incident

- Summary: {report['incident_summary']}
- Services: {", ".join(report['observed_condition']['services'])}
- Pattern matches: {", ".join(report['observed_condition'].get('pattern_matches', []))}

## Candidate Actions

{actions}

## Stratus Ranking

{rankings}

## Browser Workflow

{browser_steps}

## Automation Handoff

- Browser playbook: `{playbook or "n/a"}`
- Verify command: `{report.get('artifacts', {}).get('verify_command', 'n/a')}`

## Decision

- Chosen action: `{chosen['id']}`
- Confidence: `{report['overall_confidence']:.2f}`
{baseline_section}
## Predicted vs Actual

- Predicted: `{json.dumps(predicted, sort_keys=True)}`
- Actual: `{json.dumps(actual, sort_keys=True)}`
- Drift score: `{drift['score']:.2f}`
{verdict_section}"""


def render_verdict_markdown(verdict: dict) -> str:
    """Render a verdict dict as markdown."""
    dc = verdict.get("decision_comparison", {})
    oe = verdict.get("outcome_evaluation", {})
    vd = verdict.get("verdict", {})
    cs = verdict.get("case_summary", {})
    mb = oe.get("metrics_before", {})
    ma = oe.get("metrics_after", {})

    return f"""# Incident Verdict

## Summary

- Scenario: `{verdict.get("scenario_id", "n/a")}`
- Mode: `{verdict.get("mode", "n/a")}`
- Timestamp: `{verdict.get("timestamp", "n/a")}`
- Classification: `{vd.get("classification", "n/a")}`

## Decision Comparison

| | Stratus | Baseline | Ground Truth |
|---|---|---|---|
| **Chose** | `{dc.get("stratus_chose", "n/a")}` | `{dc.get("baseline_chose", "n/a")}` | `{dc.get("ground_truth", "n/a")}` |
| **Correct** | `{dc.get("stratus_correct", "n/a")}` | `{dc.get("baseline_correct", "n/a")}` | - |
| **Confidence** | `{dc.get("stratus_confidence", "n/a")}` | `{dc.get("baseline_confidence", "n/a")}` | - |

## Outcome Evaluation

| Metric | Before | After |
|---|---|---|
| Latency P95 (ms) | {mb.get("latency_p95_ms", "-")} | {ma.get("latency_p95_ms", "-")} |
| Error Rate | {mb.get("error_rate", "-")} | {ma.get("error_rate", "-")} |
| Retry Rate | {mb.get("retry_rate", "-")} | {ma.get("retry_rate", "-")} |

- Drift score: `{oe.get("drift_score", "-")}`
- Recovery class: `{oe.get("recovery_class", "-")}`

## Verdict

- Dangerous reflex rejected: `{vd.get("dangerous_reflex_rejected", "n/a")}`
- Safe action chosen: `{vd.get("safe_action_chosen", "n/a")}`
- Blast radius avoided: `{vd.get("blast_radius_avoided", "n/a")}`
- Fairness preserved: `{vd.get("fairness_preserved", "n/a")}`

### Narrative

{vd.get("narrative", "No narrative generated.")}

## Case Summary

- Situation: {cs.get("situation", "n/a")}
- Chosen action: `{cs.get("chosen_action", "n/a")}`
- Verdict: {cs.get("narrative_verdict", "n/a")}
"""


def load_incident_input(payload: dict, scenario: dict | None = None) -> tuple[str, dict, dict]:
    """Load incident input, optionally overlaying scenario evidence and alert template."""
    if scenario:
        evidence = scenario["evidence"]
        # Use scenario's alert template so classify_incident picks up the right summary
        alert_payload = dict(payload)
        if "alert_template" in scenario:
            alert_payload.setdefault("commonAnnotations", {})
            alert_payload["commonAnnotations"].update(
                scenario["alert_template"].get("commonAnnotations", {})
            )
            alert_payload.setdefault("commonLabels", {})
            alert_payload["commonLabels"].update(
                scenario["alert_template"].get("commonLabels", {})
            )
    else:
        evidence = collect_evidence(payload)
        alert_payload = payload
    state = classify_incident(alert_payload, evidence)
    return "alertmanager_webhook", state, evidence


def normalize_best_action(best_action: dict | str, actions: list[dict]) -> dict:
    if isinstance(best_action, dict):
        return best_action
    for action in actions:
        if action["id"] == best_action:
            return action
    return {"id": str(best_action), "description": str(best_action)}


def actual_outcome_from_evidence(before_metrics: dict, after_metrics: dict, action_id: str) -> dict:
    retry_rate = float(after_metrics.get("retry_rate", 0.0))
    error_rate = float(after_metrics.get("error_rate", 0.0))
    latency = int(after_metrics.get("latency_p95_ms", 0))
    return {
        "latency_p95_ms": latency,
        "error_rate": error_rate,
        "retry_rate": retry_rate,
        "time_to_effect_seconds": 30,
        "risk_level": "low" if retry_rate <= RETRY_RISK_LOW_MAX else "medium",
        "impact": "medium_high",
        "blast_radius": "low" if retry_rate <= RETRY_RISK_LOW_MAX else "medium",
        "recovery": "strong" if latency < before_metrics.get("latency_p95_ms", latency) else "partial",
        "notes": f"Observed live metrics after {action_id} via Prometheus-backed verification.",
        "action_id": action_id,
        "latency_direction": direction(before_metrics.get("latency_p95_ms"), latency),
        "error_direction": direction(before_metrics.get("error_rate"), error_rate),
        "retry_storm_risk": retry_risk(retry_rate),
    }


def direction(before: float | int | None, after: float | int | None) -> str:
    """Reuse simulator's direction logic."""
    from agents.simulator import _direction
    return _direction(before, after)


def retry_risk(retry_rate: float) -> str:
    """Reuse simulator's retry risk logic (uses config thresholds)."""
    from agents.simulator import _retry_risk
    return _retry_risk(retry_rate)


def browser_workflow(chosen: dict) -> dict:
    button_map = {
        "rate_limit_retries": "Apply Retry Rate Limit",
        "restart_payment": "Restart Payment",
        "shift_traffic": "Shift Traffic",
        "disable_flag": "Disable Payment Flag",
    }
    selector_map = {
        "rate_limit_retries": "#action-rate_limit_retries",
        "restart_payment": "#action-restart_payment",
        "shift_traffic": "#action-shift_traffic",
        "disable_flag": "#action-disable_flag",
    }
    base_url = os.environ.get("CONTROL_PLANE_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
    return {
        "dashboard_url": base_url + "/",
        "feature_flags_url": base_url + "/feature-flags",
        "state_api_url": base_url + "/api/state",
        "button_label": button_map.get(chosen["id"], chosen["id"]),
        "button_selector": selector_map.get(chosen["id"], f"#action-{chosen['id']}"),
        "steps": [
            "Open the dashboard.",
            "Inspect the incident metrics and active flags.",
            "Open the feature-flag page.",
            f"Click '{button_map.get(chosen['id'], chosen['id'])}'.",
            "Refresh the dashboard.",
            "Summarize before/after state and then run verify phase.",
        ],
    }


def kubernetes_plan(chosen: dict) -> dict:
    manifest_map = {
        "restart_payment": "k8s/remediations/restart-payment-rollout.yaml",
        "disable_flag": "k8s/remediations/disable-payment-feature.yaml",
        "rate_limit_retries": "k8s/remediations/rate-limit-retries-configmap.yaml",
        "shift_traffic": "k8s/remediations/shift-traffic-virtualservice.yaml",
    }
    return {
        "cluster_stack": {
            "otel_demo_namespace": "otel-demo",
            "chaos_mesh_namespace": "chaos-testing",
        },
        "fault_manifests": [
            "k8s/chaos/payment-unreachable-networkchaos.yaml",
            "k8s/chaos/payment-latency-httpchaos.yaml",
        ],
        "recommended_remediation_manifest": manifest_map.get(chosen["id"]),
    }


def load_saved_plan(scenario_id: str) -> dict:
    plan_path = Path("outputs") / f"{scenario_id}_plan.json"
    if not plan_path.exists():
        print(
            f"ERROR: Plan file not found: {plan_path}\n"
            f"Run the plan phase first: python run.py alerts/latest.json --phase plan",
            file=sys.stderr,
        )
        sys.exit(1)
    return json.loads(plan_path.read_text())


def write_outputs(payload: dict, scenario_id: str, suffix: str) -> None:
    out = Path("outputs") / f"{scenario_id}_{suffix}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    # Verdict dicts have a different shape — use verdict-specific markdown
    if suffix == "verdict":
        md_out = Path("outputs") / f"{scenario_id}_{suffix}.md"
        md_out.write_text(render_verdict_markdown(payload))
    else:
        md_out = Path("outputs") / f"{scenario_id}_{suffix}.md"
        md_out.write_text(render_markdown(payload))


def build_browser_playbook(plan: dict) -> dict:
    browser = plan["browser_workflow"]
    chosen = plan["best_action"]
    return {
        "workflow": {
            "orchestrator": "openclaw",
            "level": plan["workflow"]["level"],
            "phase": "browser_playbook",
        },
        "scenario_id": plan["scenario_id"],
        "goal": "Open the dashboard, apply the selected remediation in the feature-flag UI, refresh the dashboard, and then run verify automatically.",
        "chosen_action": {
            "id": chosen["id"],
            "label": browser["button_label"],
            "selector": browser["button_selector"],
        },
        "urls": {
            "dashboard": browser["dashboard_url"],
            "feature_flags": browser["feature_flags_url"],
            "state_api": browser["state_api_url"],
        },
        "steps": [
            {"kind": "open", "target": browser["dashboard_url"], "purpose": "capture_before_dashboard"},
            {"kind": "inspect", "target": browser["dashboard_url"], "purpose": "read_before_metrics_and_flags"},
            {"kind": "open", "target": browser["feature_flags_url"], "purpose": "open_execution_surface"},
            {
                "kind": "click",
                "target": browser["button_selector"],
                "label": browser["button_label"],
                "purpose": "apply_chosen_remediation",
            },
            {"kind": "open", "target": browser["dashboard_url"], "purpose": "capture_after_dashboard"},
            {"kind": "inspect", "target": browser["state_api_url"], "purpose": "confirm_state_after_action"},
            {
                "kind": "exec",
                "command": plan["artifacts"]["verify_command"],
                "purpose": "run_post_action_verification",
            },
            {"kind": "read", "target": f"outputs/{plan['scenario_id']}_report.json", "purpose": "summarize_final_report"},
        ],
        "expected_before": plan["observed_condition"]["metrics"],
        "predicted_after": plan["predicted_effects_by_action"].get(chosen["id"], {}),
    }


def render_browser_playbook_markdown(playbook: dict) -> str:
    steps = "\n".join(
        f"- `{step['kind']}`: `{step.get('target', step.get('command', ''))}`"
        + (f" ({step['purpose']})" if step.get("purpose") else "")
        for step in playbook["steps"]
    )
    return f"""# OpenClaw Browser Playbook

## Goal

{playbook['goal']}

## Chosen Action

- Action: `{playbook['chosen_action']['id']}`
- Button label: `{playbook['chosen_action']['label']}`
- Button selector: `{playbook['chosen_action']['selector']}`

## URLs

- Dashboard: `{playbook['urls']['dashboard']}`
- Feature flags: `{playbook['urls']['feature_flags']}`
- State API: `{playbook['urls']['state_api']}`

## Steps

{steps}
"""


def write_browser_playbook(plan: dict, scenario_id: str) -> None:
    playbook = build_browser_playbook(plan)
    out = Path("outputs") / f"{scenario_id}_browser_playbook.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(playbook, indent=2))
    md_out = Path("outputs") / f"{scenario_id}_browser_playbook.md"
    md_out.write_text(render_browser_playbook_markdown(playbook))


if __name__ == "__main__":
    main()
