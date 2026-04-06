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
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import RETRY_RISK_LOW_MAX
from agents.baseline import choose_action as baseline_choose
from agents.candidate_actions import build_scenario_shortlist, generate_candidate_actions
from agents.evaluator import evaluate as evaluate_verdict
from agents.incident_classifier import classify_incident
from agents.simulator import compare_prediction_to_actual, normalize_actual, normalize_prediction, simulate_action
from scenarios import list_scenarios, load_scenario
from tools.case_library import save_case
from tools.execute_action import execute_action
from tools.prometheus_client import collect_evidence
from tools.runtime_state import control_plane_urls, derive_metrics, load_state, reset_state, save_state
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
    parser.add_argument("--phase", choices=["plan", "demo", "fallback", "verify", "auto"], default="auto")
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
    payload = load_json_path(input_path)

    if args.phase == "demo":
        state, evidence = prepare_demo_state(payload)
        source_type = "alertmanager_webhook"
    else:
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
        write_openclaw_demo_mode(plan, scenario_id)
        print(json.dumps(plan, indent=2))
        print(f"Wrote plan to outputs/{scenario_id}_plan.json")
        return

    if args.phase == "demo":
        plan = build_plan_report(payload, scenario_id, source_type, input_path, state, evidence)
        write_outputs(plan, scenario_id, "plan")
        write_browser_playbook(plan, scenario_id)
        demo_mode = build_openclaw_demo_mode(plan)
        write_openclaw_demo_mode(plan, scenario_id)
        print(json.dumps(demo_mode, indent=2))
        print(f"Wrote OpenClaw demo mode artifact to outputs/{scenario_id}_openclaw_demo.json")
        return

    if args.phase == "fallback":
        plan = load_saved_plan(scenario_id)
        chosen = plan["best_action"]
        execution = execute_action(chosen)
        execution["executor"] = "openclaw_saved_plan_fallback"
        execution["mode"] = "saved_plan_fallback"
        execution["notes"] = (
            "Browser control was unavailable, so OpenClaw executed the saved plan action "
            "through the local fallback path and then verified the outcome."
        )
        refreshed_evidence = wait_for_updated_evidence(
            payload,
            before_evidence={
                "metrics": plan["observed_condition"]["metrics"],
                "prometheus": plan["observed_condition"].get("prometheus", {}),
            },
            expected_metrics=derive_metrics(execution["state_after_action"]),
        )
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
            mode="openclaw_browser_fallback",
        )
        write_outputs(report, scenario_id, "report")
        print(json.dumps(report, indent=2))
        print(f"Wrote fallback report to outputs/{scenario_id}_report.json")
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

    if scenario:
        refreshed_evidence = collect_evidence(payload)
    else:
        refreshed_evidence = wait_for_updated_evidence(
            payload,
            before_evidence={
                "metrics": plan["observed_condition"]["metrics"],
                "prometheus": plan["observed_condition"].get("prometheus", {}),
            },
            expected_metrics=derive_metrics(execution["state_after_action"]),
        )
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
    write_openclaw_demo_mode(plan, scenario_id)
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
    planner = build_scenario_shortlist(state)
    # Use scenario candidate actions if available, otherwise use planner shortlist
    if scenario:
        shortlist = scenario["candidate_actions"]
    else:
        shortlist = planner["shortlist"]

    # Always run BOTH rankers
    ranking = rank_actions(state, shortlist)
    baseline_ranking = baseline_choose(state, shortlist)

    stratus_chosen = normalize_best_action(ranking["best_action"], shortlist)
    baseline_chosen = normalize_best_action(baseline_ranking["best_action"], shortlist)

    # Mode switch: which ranker is primary?
    if mode == "baseline":
        primary_chosen = baseline_chosen
        primary_confidence = baseline_ranking["overall_confidence"]
    else:
        primary_chosen = stratus_chosen
        primary_confidence = ranking["overall_confidence"]

    plan_id = f"{scenario_id}-{uuid.uuid4().hex[:8]}"
    playbook_path = f"outputs/{scenario_id}_browser_playbook.json"
    demo_path = f"outputs/{scenario_id}_openclaw_demo.json"
    return {
        "plan_id": plan_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
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
        "planner": {
            "agent": "Planner Agent",
            "selection_strategy": planner["selection_strategy"],
            "action_library_size": len(planner["action_library"]),
            "shortlist_size": len(shortlist),
            "selection_rationale": planner["selection_rationale"],
        },
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
        "action_library": planner["action_library"],
        "candidate_actions": shortlist,
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
            "openclaw_demo_mode": demo_path,
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
    planned_action = plan["best_action"]
    executed_action = resolve_executed_action(plan, execution)
    predicted = plan["predicted_effects_by_action"].get(executed_action["id"], {})

    # When scenario is provided, use deterministic simulated outcomes
    # instead of re-collecting evidence (which returns unchanged mock data).
    if scenario:
        actual = simulate_action(
            {"metrics": plan["observed_condition"]["metrics"]},
            executed_action,
            scenario,
        )
    else:
        actual = actual_outcome_from_evidence(
            before_metrics=plan["observed_condition"]["metrics"],
            after_metrics=current_evidence.get("metrics", current_evidence),
            action_id=executed_action["id"],
        )
    drift = compare_prediction_to_actual(executed_action["id"], predicted, actual)

    # Plan/execution alignment
    alignment = {
        "planned_action_id": planned_action["id"],
        "executed_action_id": executed_action["id"],
        "matches_plan": planned_action["id"] == executed_action["id"],
    }

    # Baseline comparison
    baseline_chosen = plan.get("baseline_best_action", {})
    baseline_vs_stratus = {
        "stratus_chose": plan.get("stratus_ranking", [{}])[0].get("id", "unknown")
            if plan.get("stratus_ranking") else "unknown",
        "baseline_chose": baseline_chosen.get("id", "unknown"),
        "same_choice": executed_action["id"] == baseline_chosen.get("id"),
        "stratus_confidence": plan["overall_confidence"],
        "baseline_confidence": plan.get("baseline_confidence", 0),
    }

    report = {
        "plan_id": plan.get("plan_id"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
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
        "planner": plan.get("planner", {}),
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
        "action_library": plan.get("action_library", []),
        "candidate_actions": plan["candidate_actions"],
        "stratus_ranking": plan["stratus_ranking"],
        "best_action": planned_action,
        "executed_action": executed_action,
        "action_alignment": alignment,
        "overall_confidence": plan["overall_confidence"],
        "predicted_effects_by_action": plan["predicted_effects_by_action"],
        "browser_workflow": browser_workflow(planned_action),
        "artifacts": plan.get("artifacts", {}),
        "kubernetes_plan": kubernetes_plan(planned_action),
        "execution": execution or {
            "executor": "browser_expected",
            "mode": "awaiting_or_completed_browser_action",
            "action_id": executed_action["id"],
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
    executed = report.get("executed_action", chosen)
    predicted = report.get("predicted_vs_actual", {}).get("predicted", {})
    actual = report.get("actual_outcome", {})
    drift = report.get("predicted_vs_actual", {}).get("drift", {"score": 0.0})
    alignment = report.get("action_alignment", {})
    actions = "\n".join(
        f"- `{action['id']}`: {action['description']}" for action in report["candidate_actions"]
    )
    library = "\n".join(
        f"- `{action['id']}`: {action['category']} ({action['execution_surface']})"
        for action in report.get("action_library", [])
    )
    rankings = "\n".join(
        f"- Rank {item['rank']}: `{item['id']}` ({item['confidence']:.2f})"
        for item in report["stratus_ranking"]
    )
    browser_steps = "\n".join(f"- {step}" for step in report["browser_workflow"]["steps"])
    playbook = report.get("artifacts", {}).get("browser_playbook")
    demo_mode = report.get("artifacts", {}).get("openclaw_demo_mode")

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

## Action Strategy

- Planner strategy: `{report.get('planner', {}).get('selection_strategy', 'n/a')}`
- Library size: `{report.get('planner', {}).get('action_library_size', 0)}`
- Shortlist size: `{report.get('planner', {}).get('shortlist_size', 0)}`

## Action Library

{library}

## Candidate Actions

{actions}

## Stratus Ranking

{rankings}

## Browser Workflow

{browser_steps}

## Automation Handoff

- OpenClaw demo mode: `{demo_mode or "n/a"}`
- Browser playbook: `{playbook or "n/a"}`
- Verify command: `{report.get('artifacts', {}).get('verify_command', 'n/a')}`

## Decision

- Guardrail choice: `{chosen['id']}`
- Executed action: `{executed['id']}`
- Plan/execution match: `{alignment.get('matches_plan', True)}`
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


def load_json_path(path: Path) -> dict:
    raw = path.read_text()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        try:
            payload, index = json.JSONDecoder().raw_decode(raw)
        except json.JSONDecodeError:
            raise exc
        if raw[index:].strip():
            path.write_text(json.dumps(payload, indent=2))
        return payload


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


def prepare_demo_state(payload: dict, timeout_seconds: float = 20.0) -> tuple[dict, dict]:
    reset_runtime = reset_state()
    expected_metrics = derive_metrics(reset_runtime)
    deadline = time.monotonic() + timeout_seconds
    latest = collect_evidence(payload)
    while True:
        if latest.get("prometheus", {}).get("source") != "live":
            break
        if latest.get("metrics") == expected_metrics:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(2.0, remaining))
        latest = collect_evidence(payload)
    state = classify_incident(payload, latest)
    return state, latest


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
        "enable_payment_circuit_breaker": "Enable Payment Circuit Breaker",
        "increase_retry_backoff": "Increase Retry Backoff",
        "restart_payment": "Restart Payment",
        "shift_traffic": "Shift Traffic",
        "disable_flag": "Disable Payment Flag",
    }
    selector_map = {
        "rate_limit_retries": "#action-rate_limit_retries",
        "enable_payment_circuit_breaker": "#action-enable_payment_circuit_breaker",
        "increase_retry_backoff": "#action-increase_retry_backoff",
        "restart_payment": "#action-restart_payment",
        "shift_traffic": "#action-shift_traffic",
        "disable_flag": "#action-disable_flag",
    }
    base_url = os.environ.get("CONTROL_PLANE_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
    return {
        "dashboard_url": base_url + "/",
        "feature_flags_url": base_url + "/feature-flags",
        "openclaw_execution_url": base_url + "/openclaw-execution",
        "openclaw_verdict_url": base_url + "/openclaw-execution?stage=verdict",
        "state_api_url": base_url + "/api/state",
        "button_label": button_map.get(chosen["id"], chosen["id"]),
        "button_selector": "#openclaw-demo-run",
        "fallback_button_selector": selector_map.get(chosen["id"], f"#action-{chosen['id']}"),
        "steps": [
            "Open the dedicated OpenClaw execution page.",
            "Inspect the before-action incident card and chosen remediation.",
            "Click the single OpenClaw execution button to apply the chosen remediation.",
            "Run verify and inspect the dedicated verdict page.",
        ],
    }


def kubernetes_plan(chosen: dict) -> dict:
    manifest_map = {
        "restart_payment": "k8s/remediations/restart-payment-rollout.yaml",
        "disable_flag": "k8s/remediations/disable-payment-feature.yaml",
        "rate_limit_retries": "k8s/remediations/rate-limit-retries-configmap.yaml",
        "enable_payment_circuit_breaker": "k8s/remediations/enable-payment-circuit-breaker-configmap.yaml",
        "increase_retry_backoff": "k8s/remediations/increase-retry-backoff-configmap.yaml",
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
    return load_json_path(plan_path)


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
        "plan_id": plan.get("plan_id"),
        "workflow": {
            "orchestrator": "openclaw",
            "level": plan["workflow"]["level"],
            "phase": "browser_playbook",
        },
        "scenario_id": plan["scenario_id"],
        "goal": "Open the dedicated OpenClaw execution page, apply the selected remediation with one browser action, then run verify and inspect the verdict page.",
        "chosen_action": {
            "id": chosen["id"],
            "label": browser["button_label"],
            "selector": browser["button_selector"],
            "fallback_selector": browser["fallback_button_selector"],
        },
        "urls": {
            "dashboard": browser["dashboard_url"],
            "openclaw_execution": browser["openclaw_execution_url"],
            "openclaw_verdict": browser["openclaw_verdict_url"],
            "feature_flags": browser["feature_flags_url"],
            "state_api": browser["state_api_url"],
        },
        "steps": [
            {"kind": "open", "target": browser["openclaw_execution_url"], "purpose": "open_dedicated_execution_surface"},
            {"kind": "inspect", "target": browser["openclaw_execution_url"], "purpose": "read_before_metrics_and_chosen_action"},
            {
                "kind": "click",
                "target": browser["button_selector"],
                "label": f"Execute Planned Action: {browser['button_label']}",
                "purpose": "apply_chosen_remediation",
            },
            {
                "kind": "exec",
                "command": plan["artifacts"]["verify_command"],
                "purpose": "run_post_action_verification",
            },
            {"kind": "open", "target": browser["openclaw_verdict_url"], "purpose": "open_dedicated_verdict_surface"},
            {"kind": "inspect", "target": browser["state_api_url"], "purpose": "confirm_state_after_action"},
            {"kind": "read", "target": f"outputs/{plan['scenario_id']}_report.json", "purpose": "summarize_final_report"},
        ],
        "expected_before": plan["observed_condition"]["metrics"],
        "predicted_after": plan["predicted_effects_by_action"].get(chosen["id"], {}),
    }


def build_openclaw_demo_mode(plan: dict) -> dict:
    chosen = plan["best_action"]
    playbook = build_browser_playbook(plan)
    execution_surface = playbook["urls"]["openclaw_execution"]
    verdict_surface = playbook["urls"]["openclaw_verdict"]
    state_api = playbook["urls"]["state_api"]
    return {
        "plan_id": plan.get("plan_id"),
        "workflow": {
            "orchestrator": "openclaw",
            "phase": "demo_mode",
            "workspace_skill": "incident_guardrail",
            "launch_style": "start_from_openclaw_chat",
        },
        "scenario_id": plan["scenario_id"],
        "goal": "Start from OpenClaw chat, generate the guardrail plan, execute the browser remediation from one dedicated execution page, run verify, and summarize the final verdict without using the console shortcut buttons.",
        "starter_prompt": default_openclaw_demo_prompt(),
        "artifacts": {
            "plan": f"outputs/{plan['scenario_id']}_plan.json",
            "browser_playbook": f"outputs/{plan['scenario_id']}_browser_playbook.json",
            "report": f"outputs/{plan['scenario_id']}_report.json",
        },
        "demo_contract": {
            "run_first_command": ".venv/bin/python run.py alerts/latest.json --phase demo",
            "verify_command": ".venv/bin/python run.py alerts/latest.json --phase verify",
            "fallback_command": ".venv/bin/python run.py alerts/latest.json --phase fallback",
            "must_start_in_chat": True,
            "manual_console_buttons_are_fallback_only": True,
        },
        "browser_sequence": [
            {
                "step": 1,
                "kind": "open",
                "target": execution_surface,
                "purpose": "open_dedicated_execution_surface",
            },
            {
                "step": 2,
                "kind": "inspect",
                "target": execution_surface,
                "purpose": "read_before_metrics_and_chosen_action",
            },
            {
                "step": 3,
                "kind": "click",
                "target": playbook["chosen_action"]["selector"],
                "label": f"Execute Planned Action: {playbook['chosen_action']['label']}",
                "purpose": "apply_guardrail_choice",
            },
            {
                "step": 4,
                "kind": "exec",
                "command": ".venv/bin/python run.py alerts/latest.json --phase verify",
                "purpose": "write_final_verdict",
            },
            {
                "step": 5,
                "kind": "open",
                "target": verdict_surface,
                "purpose": "inspect_final_verdict_surface",
            },
            {
                "step": 6,
                "kind": "inspect",
                "target": state_api,
                "purpose": "confirm_shared_state_after_action",
            },
        ],
        "summary_contract": {
            "must_include": [
                "incident summary",
                "dangerous local reflex rejected",
                "chosen safer action",
                "before and after browser-visible evidence",
                "predicted versus actual drift",
            ],
            "dangerous_reflex": "restart_payment",
            "chosen_action": chosen["id"],
        },
        "fallback_contract": {
            "trigger": "browser tool times out, fails to load the dashboard, or loses browser control",
            "command": ".venv/bin/python run.py alerts/latest.json --phase fallback",
            "must_say": "execution used the saved-plan fallback because browser control was unavailable",
        },
    }


def resolve_executed_action(plan: dict, execution: dict | None) -> dict:
    action_by_id = {action["id"]: action for action in plan.get("candidate_actions", [])}
    planned_action = plan["best_action"]
    execution_action_id = None
    if execution:
        execution_action_id = execution.get("action_id")
    if not execution_action_id:
        execution_action_id = load_state().get("last_action")
    if execution_action_id and execution_action_id in action_by_id:
        return action_by_id[execution_action_id]
    return planned_action


def default_openclaw_demo_prompt() -> str:
    return (
        "Use the incident_guardrail skill on the latest alert in OpenClaw Demo Mode. "
        "Run `.venv/bin/python run.py alerts/latest.json --phase demo`, read "
        "`outputs/alert_latest_openclaw_demo.json` and "
        "`outputs/alert_latest_browser_playbook.json`, then use the browser to open the "
        "dedicated OpenClaw execution page, inspect the before-action incident card, click the "
        "single execution button for the chosen remediation, run verify, inspect the verdict page, and summarize "
        "the final verdict with the dangerous reflex that was avoided. If the browser tool "
        "fails or times out, do not stop and do not replan. Instead run "
        "`.venv/bin/python run.py alerts/latest.json --phase fallback`, then read "
        "`outputs/alert_latest_report.json` and summarize the final verdict while explicitly "
        "noting that execution used the saved-plan fallback because browser control was unavailable."
    )


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
- Manual fallback selector: `{playbook['chosen_action']['fallback_selector']}`

## URLs

- Dashboard: `{playbook['urls']['dashboard']}`
- OpenClaw execution: `{playbook['urls']['openclaw_execution']}`
- OpenClaw verdict: `{playbook['urls']['openclaw_verdict']}`
- Feature flags: `{playbook['urls']['feature_flags']}`
- State API: `{playbook['urls']['state_api']}`

## Steps

{steps}
"""


def render_openclaw_demo_markdown(demo_mode: dict) -> str:
    browser_steps = "\n".join(
        f"- Step {step['step']}: `{step['kind']}` "
        + f"`{step.get('target', step.get('command', ''))}`"
        + (f" ({step['purpose']})" if step.get("purpose") else "")
        for step in demo_mode["browser_sequence"]
    )
    summary_requirements = "\n".join(
        f"- {item}" for item in demo_mode["summary_contract"]["must_include"]
    )
    return f"""# OpenClaw Demo Mode

## Goal

{demo_mode['goal']}

## Starter Prompt

```text
{demo_mode['starter_prompt']}
```

## Demo Contract

- Run first: `{demo_mode['demo_contract']['run_first_command']}`
- Verify after browser action: `{demo_mode['demo_contract']['verify_command']}`
- Fallback if browser control fails: `{demo_mode['demo_contract']['fallback_command']}`
- Console buttons are fallback only: `{demo_mode['demo_contract']['manual_console_buttons_are_fallback_only']}`

## Browser Sequence

{browser_steps}

## Fallback Contract

- Trigger: {demo_mode['fallback_contract']['trigger']}
- Command: `{demo_mode['fallback_contract']['command']}`
- Final summary note: {demo_mode['fallback_contract']['must_say']}

## Final Summary Must Include

{summary_requirements}
"""


def write_browser_playbook(plan: dict, scenario_id: str) -> None:
    playbook = build_browser_playbook(plan)
    out = Path("outputs") / f"{scenario_id}_browser_playbook.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(playbook, indent=2))
    md_out = Path("outputs") / f"{scenario_id}_browser_playbook.md"
    md_out.write_text(render_browser_playbook_markdown(playbook))


def write_openclaw_demo_mode(plan: dict, scenario_id: str) -> None:
    demo_mode = build_openclaw_demo_mode(plan)
    out = Path("outputs") / f"{scenario_id}_openclaw_demo.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(demo_mode, indent=2))
    md_out = Path("outputs") / f"{scenario_id}_openclaw_demo.md"
    md_out.write_text(render_openclaw_demo_markdown(demo_mode))


def wait_for_updated_evidence(
    payload: dict,
    before_evidence: dict,
    expected_metrics: dict | None = None,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float = 2.0,
) -> dict:
    if before_evidence.get("prometheus", {}).get("source") != "live":
        return collect_evidence(payload)

    timeout = (
        float(os.environ.get("AUTO_VERIFY_SETTLE_SECONDS", "20"))
        if timeout_seconds is None
        else timeout_seconds
    )
    if timeout <= 0:
        return collect_evidence(payload)

    before_metrics = before_evidence.get("metrics", {})
    before_alerts = before_evidence.get("prometheus", {}).get("active_alerts", [])
    deadline = time.monotonic() + timeout
    latest = collect_evidence(payload)

    while True:
        if latest.get("prometheus", {}).get("source") != "live":
            return latest
        latest_metrics = latest.get("metrics", {})
        if expected_metrics and latest_metrics == expected_metrics:
            return latest
        if expected_metrics is None and latest_metrics != before_metrics:
            return latest
        if expected_metrics is None and latest.get("prometheus", {}).get("active_alerts", []) != before_alerts:
            return latest
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return latest
        time.sleep(min(poll_interval_seconds, remaining))
        latest = collect_evidence(payload)


if __name__ == "__main__":
    main()
