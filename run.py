from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from agents.candidate_actions import build_scenario_shortlist
from agents.incident_classifier import classify_incident
from agents.simulator import compare_prediction_to_actual, normalize_actual, normalize_prediction
from tools.execute_action import execute_action
from tools.prometheus_client import collect_evidence
from tools.runtime_state import control_plane_urls, load_state
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
    args = parser.parse_args()

    input_path = Path(args.input_path)
    payload = json.loads(input_path.read_text())
    source_type, state, evidence = load_incident_input(payload)
    scenario_id = payload.get("id", "alert_latest")

    if args.phase == "plan":
        plan = build_plan_report(payload, scenario_id, source_type, input_path, state, evidence)
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
            mode="browser_verify",
        )
        write_outputs(verify, scenario_id, "report")
        print(json.dumps(verify, indent=2))
        print(f"Wrote report to outputs/{scenario_id}_report.json")
        return

    plan = build_plan_report(payload, scenario_id, source_type, input_path, state, evidence)
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
        mode="auto_apply",
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
) -> dict:
    planner = build_scenario_shortlist(state)
    shortlist = planner["shortlist"]
    ranking = rank_actions(state, shortlist)
    chosen = normalize_best_action(ranking["best_action"], shortlist)
    playbook_path = f"outputs/{scenario_id}_browser_playbook.json"
    return {
        "workflow": {
            "orchestrator": "openclaw",
            "entrypoint": ".venv/bin/python run.py alerts/latest.json --phase plan",
            "workspace_skill": "incident_guardrail",
            "level": "level_3_browser_k8s_scaffold",
            "phase": "plan",
        },
        "source_type": source_type,
        "input_path": str(input_path),
        "scenario_id": scenario_id,
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
        "best_action": chosen,
        "overall_confidence": ranking["overall_confidence"],
        "predicted_effects_by_action": ranking["predicted_effects_by_action"],
        "browser_workflow": browser_workflow(chosen),
        "artifacts": {
            "browser_playbook": playbook_path,
            "verify_command": ".venv/bin/python run.py alerts/latest.json --phase verify",
        },
        "kubernetes_plan": kubernetes_plan(chosen),
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
    mode: str,
) -> dict:
    chosen = plan["best_action"]
    predicted = plan["predicted_effects_by_action"].get(chosen["id"], {})
    actual = actual_outcome_from_evidence(
        before_metrics=plan["observed_condition"]["metrics"],
        after_metrics=current_evidence["metrics"],
        action_id=chosen["id"],
    )
    drift = compare_prediction_to_actual(chosen["id"], predicted, actual)
    return {
        "workflow": {
            "orchestrator": "openclaw",
            "entrypoint": ".venv/bin/python run.py alerts/latest.json --phase verify",
            "workspace_skill": "incident_guardrail",
            "level": "level_3_browser_k8s_scaffold",
            "phase": "verify",
            "mode": mode,
        },
        "source_type": source_type,
        "input_path": str(input_path),
        "scenario_id": scenario_id,
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
        "evidence_summary": current_evidence,
        "notes": plan.get("notes", []),
    }


def render_markdown(report: dict) -> str:
    chosen = report["best_action"]
    predicted = report.get("predicted_vs_actual", {}).get("predicted", {})
    actual = report.get("actual_outcome", {})
    drift = report.get("predicted_vs_actual", {}).get("drift", {"score": 0.0})
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
    return f"""# OpenClaw Incident Guardrail Report

## Guardrail Question

Given a payment-related latency incident with retry amplification, which of these fixes is safest globally?

## Workflow

- Level: `{report['workflow']['level']}`
- Phase: `{report['workflow']['phase']}`
- Entrypoint: `{report['workflow']['entrypoint']}`

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

- Browser playbook: `{playbook or "n/a"}`
- Verify command: `{report.get('artifacts', {}).get('verify_command', 'n/a')}`

## Decision

- Chosen action: `{chosen['id']}`
- Confidence: `{report['overall_confidence']:.2f}`
- Dashboard: `{report['observed_condition']['browser_targets']['dashboard']}`
- Feature flags: `{report['observed_condition']['browser_targets']['feature_flags']}`

## Predicted vs Actual

- Predicted: `{json.dumps(predicted, sort_keys=True)}`
- Actual: `{json.dumps(actual, sort_keys=True)}`
- Drift score: `{drift['score']:.2f}`
"""


def load_incident_input(payload: dict) -> tuple[str, dict, dict]:
    evidence = collect_evidence(payload)
    state = classify_incident(payload, evidence)
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
        "risk_level": "low" if retry_rate <= 0.12 else "medium",
        "impact": "medium_high",
        "blast_radius": "low" if retry_rate <= 0.12 else "medium",
        "recovery": "strong" if latency < before_metrics.get("latency_p95_ms", latency) else "partial",
        "notes": f"Observed live metrics after {action_id} via Prometheus-backed verification.",
        "action_id": action_id,
        "latency_direction": direction(before_metrics.get("latency_p95_ms"), latency),
        "error_direction": direction(before_metrics.get("error_rate"), error_rate),
        "retry_storm_risk": retry_risk(retry_rate),
    }


def direction(before: float | int | None, after: float | int | None) -> str:
    if before is None or after is None:
        return "mixed"
    if after < before:
        return "down"
    if after > before:
        return "up"
    return "mixed"


def retry_risk(retry_rate: float) -> str:
    if retry_rate <= 0.12:
        return "low"
    if retry_rate <= 0.25:
        return "medium"
    return "high"


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
    return json.loads(plan_path.read_text())


def write_outputs(payload: dict, scenario_id: str, suffix: str) -> None:
    out = Path("outputs") / f"{scenario_id}_{suffix}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
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
                "command": ".venv/bin/python run.py alerts/latest.json --phase verify",
                "purpose": "run_post_action_verification",
            },
            {"kind": "read", "target": "outputs/alert_latest_report.json", "purpose": "summarize_final_report"},
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
