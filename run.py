from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agents.candidate_actions import build_scenario_shortlist
from agents.incident_classifier import classify_incident
from agents.simulator import compare_prediction_to_actual, normalize_actual, normalize_prediction
from tools.scenario_catalog import derive_business_metrics
from tools.execute_action import execute_action
from tools.incident_watch import (
    acknowledge_pending_incident,
    clear_watch_state,
    complete_active_incident,
    load_watch_state,
    watch_for_pending_incident,
)
from tools.prometheus_client import collect_evidence
from tools.runtime_state import control_plane_urls, current_constraints, derive_metrics, load_state, reset_state
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


def resolve_scenario_context_id(payload: dict, state: dict, fallback: str = "alert_latest") -> str:
    return str(
        payload.get("commonLabels", {}).get("scenario_id")
        or (payload.get("alerts") or [{}])[0].get("labels", {}).get("scenario_id")
        or state.get("scenario_id")
        or state.get("labels", {}).get("scenario_id")
        or state.get("active_scenario")
        or fallback
    )


def main() -> None:
    load_local_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("input_path", nargs="?", default="alerts/latest.json")
    parser.add_argument(
        "--phase",
        choices=["plan", "demo", "fallback", "verify", "auto", "watch", "await-demo", "idle"],
        default="auto",
    )
    parser.add_argument("--timeout-seconds", type=float, default=0.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    args = parser.parse_args()

    input_path = Path(args.input_path)
    if args.phase == "idle":
        watch_state = clear_watch_state()
        print(json.dumps(build_watch_artifact(watch_state), indent=2))
        print("Watcher reset to idle.")
        return

    if args.phase == "watch":
        watch_state = watch_for_pending_incident(
            alert_path=input_path,
            timeout_seconds=args.timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
        print(json.dumps(build_watch_artifact(watch_state), indent=2))
        if watch_state.get("pending_incident"):
            print("Latched pending incident and armed OpenClaw workflow.")
        else:
            print("Watcher remains armed; no new incident latched during this interval.")
        return

    if args.phase == "await-demo":
        watch_state = load_watch_state()
        if not watch_state.get("pending_incident"):
            watch_state = watch_for_pending_incident(
                alert_path=input_path,
                timeout_seconds=args.timeout_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
            )
        if not watch_state.get("pending_incident"):
            print(json.dumps(build_watch_artifact(watch_state), indent=2))
            print("Watcher remains armed; no new incident latched during this interval.")
            return
        payload = load_json_path(input_path)
        acknowledge_pending_incident("openclaw_await_demo")
        state, evidence = prepare_demo_state(payload)
        artifact_id = payload.get("id", "alert_latest")
        scenario_id = resolve_scenario_context_id(payload, state, artifact_id)
        plan = build_plan_report(
            payload,
            scenario_id,
            artifact_id,
            "alertmanager_webhook",
            input_path,
            state,
            evidence,
        )
        write_outputs(plan, artifact_id, "plan")
        write_browser_playbook(plan, artifact_id)
        demo_mode = build_openclaw_demo_mode(plan)
        write_openclaw_demo_mode(plan, artifact_id)
        print(json.dumps(demo_mode, indent=2))
        print(f"Wrote OpenClaw demo mode artifact to outputs/{artifact_id}_openclaw_demo.json")
        return

    payload = load_json_path(input_path)

    if args.phase == "demo":
        acknowledge_pending_incident("openclaw_demo_start")
        state, evidence = prepare_demo_state(payload)
        source_type = "alertmanager_webhook"
    else:
        source_type, state, evidence = load_incident_input(payload)

    artifact_id = payload.get("id", "alert_latest")
    scenario_id = resolve_scenario_context_id(payload, state, artifact_id)

    if args.phase == "plan":
        plan = build_plan_report(payload, scenario_id, artifact_id, source_type, input_path, state, evidence)
        write_outputs(plan, artifact_id, "plan")
        write_browser_playbook(plan, artifact_id)
        write_openclaw_demo_mode(plan, artifact_id)
        print(json.dumps(plan, indent=2))
        print(f"Wrote plan to outputs/{artifact_id}_plan.json")
        return

    if args.phase == "demo":
        plan = build_plan_report(payload, scenario_id, artifact_id, source_type, input_path, state, evidence)
        write_outputs(plan, artifact_id, "plan")
        write_browser_playbook(plan, artifact_id)
        demo_mode = build_openclaw_demo_mode(plan)
        write_openclaw_demo_mode(plan, artifact_id)
        print(json.dumps(demo_mode, indent=2))
        print(f"Wrote OpenClaw demo mode artifact to outputs/{artifact_id}_openclaw_demo.json")
        return

    if args.phase == "fallback":
        plan = load_saved_plan(artifact_id)
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
        write_outputs(report, artifact_id, "report")
        complete_active_incident("fallback_verified", f"outputs/{artifact_id}_report.json")
        print(json.dumps(report, indent=2))
        print(f"Wrote fallback report to outputs/{artifact_id}_report.json")
        return

    if args.phase == "verify":
        plan = load_saved_plan(artifact_id)
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
        write_outputs(verify, artifact_id, "report")
        complete_active_incident("verified", f"outputs/{artifact_id}_report.json")
        print(json.dumps(verify, indent=2))
        print(f"Wrote report to outputs/{artifact_id}_report.json")
        return

    plan = build_plan_report(payload, scenario_id, artifact_id, source_type, input_path, state, evidence)
    chosen = plan["best_action"]
    execution = execute_action(chosen)
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
        mode="auto_apply",
    )
    write_outputs(plan, artifact_id, "plan")
    write_browser_playbook(plan, artifact_id)
    write_openclaw_demo_mode(plan, artifact_id)
    write_outputs(report, artifact_id, "report")
    complete_active_incident("auto_verified", f"outputs/{artifact_id}_report.json")
    print(json.dumps(report, indent=2))
    print(f"Wrote report to outputs/{artifact_id}_report.json")


def build_plan_report(
    payload: dict,
    scenario_id: str,
    artifact_id: str,
    source_type: str,
    input_path: Path,
    state: dict,
    evidence: dict,
) -> dict:
    planner = build_scenario_shortlist(state)
    shortlist = planner["shortlist"]
    ranking = rank_actions(state, shortlist)
    chosen = normalize_best_action(ranking["best_action"], shortlist)
    plan_id = f"{artifact_id}-{uuid.uuid4().hex[:8]}"
    playbook_path = f"outputs/{artifact_id}_browser_playbook.json"
    demo_path = f"outputs/{artifact_id}_openclaw_demo.json"
    return {
        "plan_id": plan_id,
        "artifact_id": artifact_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "workflow": {
            "orchestrator": "openclaw",
            "entrypoint": ".venv/bin/python run.py alerts/latest.json --phase demo",
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
            "business_metrics": state.get("business_metrics", {}),
            "constraints": state.get("constraints", {}),
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
        "dangerous_reflex": dangerous_reflex_for_scenario(
            scenario_id=str(scenario_id),
            candidate_actions=shortlist,
            chosen_action_id=chosen["id"],
            constraints=state.get("constraints", {}),
        ),
        "overall_confidence": ranking["overall_confidence"],
        "predicted_effects_by_action": ranking["predicted_effects_by_action"],
        "browser_workflow": browser_workflow(chosen),
        "artifacts": {
            "browser_playbook": playbook_path,
            "openclaw_demo_mode": demo_path,
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
    planned_action = plan["best_action"]
    executed_action = resolve_executed_action(plan, execution)
    scenario_context_id = (
        current_state.get("scenario_id")
        or current_state.get("labels", {}).get("scenario_id")
        or plan.get("scenario_id")
        or scenario_id
    )
    predicted = plan["predicted_effects_by_action"].get(executed_action["id"], {})
    actual = actual_outcome_from_evidence(
        before_metrics=plan["observed_condition"]["metrics"],
        after_metrics=current_evidence["metrics"],
        before_business=plan["observed_condition"].get("business_metrics", {}),
        after_business=current_state.get("business_metrics", current_evidence.get("business_metrics", {})),
        after_constraints=current_state.get("constraints", current_evidence.get("constraints", {})),
        action_id=executed_action["id"],
    )
    drift = compare_prediction_to_actual(executed_action["id"], predicted, actual)
    alignment = {
        "planned_action_id": planned_action["id"],
        "executed_action_id": executed_action["id"],
        "matches_plan": planned_action["id"] == executed_action["id"],
    }
    return {
        "plan_id": plan.get("plan_id"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
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
        "scenario_id": scenario_context_id,
        "incident_summary": current_state["summary"],
        "planner": plan.get("planner", {}),
        "observed_condition": {
            "services": current_state.get("services", []),
            "metrics": current_state.get("metrics", {}),
            "business_metrics": current_state.get("business_metrics", {}),
            "constraints": current_state.get("constraints", {}),
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
        "dangerous_reflex": plan.get("dangerous_reflex"),
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
        "evidence_summary": current_evidence,
        "notes": plan.get("notes", []),
    }


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
    return f"""# OpenClaw Incident Guardrail Report

## Guardrail Question

Given a healthcare scheduling latency incident with retry amplification, which of these fixes is safest globally?

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

- OpenClaw demo mode: `{demo_mode or "n/a"}`
- Browser playbook: `{playbook or "n/a"}`
- Verify command: `{report.get('artifacts', {}).get('verify_command', 'n/a')}`

## Decision

- Guardrail choice: `{chosen['id']}`
- Executed action: `{executed['id']}`
- Plan/execution match: `{alignment.get('matches_plan', True)}`
- Confidence: `{report['overall_confidence']:.2f}`
- Dashboard: `{report['observed_condition']['browser_targets']['dashboard']}`
- Feature flags: `{report['observed_condition']['browser_targets']['feature_flags']}`

## Predicted vs Actual

- Predicted: `{json.dumps(predicted, sort_keys=True)}`
- Actual: `{json.dumps(actual, sort_keys=True)}`
- Drift score: `{drift['score']:.2f}`
"""


def build_watch_artifact(watch_state: dict) -> dict:
    pending = watch_state.get("pending_incident")
    active = watch_state.get("active_incident")
    return {
        "watcher": {
            "mode": "armed_background_watch",
            "status": watch_state.get("status", "idle"),
            "armed": watch_state.get("armed", False),
            "watch_command": ".venv/bin/python run.py alerts/latest.json --phase watch",
            "await_demo_command": ".venv/bin/python run.py alerts/latest.json --phase await-demo",
            "demo_command": ".venv/bin/python run.py alerts/latest.json --phase demo",
            "verify_command": ".venv/bin/python run.py alerts/latest.json --phase verify",
            "fallback_command": ".venv/bin/python run.py alerts/latest.json --phase fallback",
        },
        "pending_incident": pending,
        "active_incident": active,
        "last_completed_incident": watch_state.get("last_completed_incident"),
        "next_step": (
            ".venv/bin/python run.py alerts/latest.json --phase demo"
            if pending
            else "Stay idle and continue watching for the next firing incident."
        ),
    }


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


def load_incident_input(payload: dict) -> tuple[str, dict, dict]:
    evidence = collect_evidence(payload)
    state = classify_incident(payload, evidence)
    return "alertmanager_webhook", state, evidence


def prepare_demo_state(payload: dict, timeout_seconds: float = 6.0) -> tuple[dict, dict]:
    current_runtime = load_state()
    expected_metrics = derive_metrics(current_runtime)
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
    if latest.get("metrics") != expected_metrics:
        latest = evidence_from_expected_state(latest, expected_metrics)
    state = classify_incident(payload, latest)
    return state, latest


def evidence_from_expected_state(latest: dict, expected_metrics: dict) -> dict:
    refreshed = json.loads(json.dumps(latest))
    state = load_state()
    refreshed["metrics"] = {
        "latency_p95_ms": int(expected_metrics["latency_p95_ms"]),
        "error_rate": round(float(expected_metrics["error_rate"]), 4),
        "retry_rate": round(float(expected_metrics["retry_rate"]), 4),
    }
    refreshed["business_metrics"] = derive_business_metrics(state, refreshed["metrics"])
    refreshed["constraints"] = current_constraints(state)
    refreshed["prometheus"] = {
        "source": "expected_state_fallback",
        "queries": {
            "checkout_latency_p95_ms": refreshed["metrics"]["latency_p95_ms"],
            "checkout_error_rate": refreshed["metrics"]["error_rate"],
            "checkout_retry_rate": refreshed["metrics"]["retry_rate"],
        },
        "active_alerts": [
            alert
            for alert, enabled in (
                ("SchedulingLatencyHigh", refreshed["metrics"]["latency_p95_ms"] > 2000),
                ("SchedulingRetrySpiral", refreshed["metrics"]["retry_rate"] > 0.25),
            )
            if enabled
        ],
        "note": "Used expected-state fallback because Prometheus had not yet reflected the latest shared simulation state.",
    }
    return refreshed


def normalize_best_action(best_action: dict | str, actions: list[dict]) -> dict:
    if isinstance(best_action, dict):
        return best_action
    for action in actions:
        if action["id"] == best_action:
            return action
    return {"id": str(best_action), "description": str(best_action)}


def actual_outcome_from_evidence(
    before_metrics: dict,
    after_metrics: dict,
    action_id: str,
    before_business: dict | None = None,
    after_business: dict | None = None,
    after_constraints: dict | None = None,
) -> dict:
    before_business = before_business or {}
    after_business = after_business or {}
    after_constraints = after_constraints or {}
    before_latency = int(before_metrics.get("latency_p95_ms", after_metrics.get("latency_p95_ms", 0)))
    before_retry = float(before_metrics.get("retry_rate", 0.0))
    before_abandonment = float(before_business.get("queue_abandonment_rate", 0.0))
    retry_rate = float(after_metrics.get("retry_rate", 0.0))
    error_rate = float(after_metrics.get("error_rate", 0.0))
    latency = int(after_metrics.get("latency_p95_ms", 0))
    abandonment = float(after_business.get("queue_abandonment_rate", 0.0))
    fairness = float(after_business.get("fairness_skew", 0.0))
    hold_util = float(after_business.get("seat_hold_utilization", 0.0))
    queue_depth = float(after_constraints.get("queue_depth", 0.0))
    secondary_headroom = float(
        after_business.get("secondary_headroom", after_constraints.get("regional_headroom_secondary", 1.0))
    )
    callback_queue = float(
        after_business.get("manual_callback_queue_depth", after_constraints.get("manual_callback_queue", 0.0))
    )
    latency_improvement = (
        max(0.0, (before_latency - latency) / max(before_latency, 1))
        if before_latency
        else 0.0
    )
    retry_improvement = max(0.0, before_retry - retry_rate)
    abandonment_improvement = max(0.0, before_abandonment - abandonment)
    stabilized_state = (
        retry_rate <= 0.20
        and fairness <= 0.18
        and hold_util <= 0.25
        and queue_depth <= 20
        and secondary_headroom >= 0.14
    )
    if (
        stabilized_state
        and (latency_improvement >= 0.20 or latency <= 1600)
        and abandonment <= 0.35
    ):
        risk_level = "low"
    elif (
        retry_rate <= 0.35
        and latency_improvement >= 0.18
        and fairness <= 0.26
        and secondary_headroom >= 0.12
        and queue_depth <= 60
    ):
        risk_level = "medium"
    else:
        risk_level = "high"
    if secondary_headroom < 0.12 or callback_queue > 24 or hold_util > 0.9:
        blast_radius = "high"
    elif risk_level == "low" or stabilized_state or (
        risk_level == "medium"
        and retry_rate <= 0.20
        and fairness <= 0.16
        and hold_util <= 0.55
        and secondary_headroom >= 0.14
    ):
        blast_radius = "low"
    else:
        blast_radius = "medium"
    return {
        "latency_p95_ms": latency,
        "error_rate": error_rate,
        "retry_rate": retry_rate,
        "queue_abandonment_rate": round(abandonment, 2),
        "fairness_skew": round(fairness, 2),
        "seat_hold_utilization": round(hold_util, 2),
        "secondary_headroom": round(secondary_headroom, 2),
        "manual_callback_queue_depth": round(callback_queue, 1),
        "time_to_effect_seconds": 30,
        "risk_level": risk_level,
        "impact": "medium_high",
        "blast_radius": blast_radius,
        "recovery": (
            "strong"
            if (
                risk_level in {"low", "medium"}
                and (latency_improvement >= 0.20 or latency <= 1600)
                and retry_improvement >= 0.10
                and (
                    abandonment_improvement >= 0.04
                    or abandonment <= 0.28
                    or queue_depth <= 20
                )
            )
            else "partial"
        ),
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
    if retry_rate <= 0.20:
        return "low"
    if retry_rate <= 0.35:
        return "medium"
    return "high"


def browser_workflow(chosen: dict) -> dict:
    button_map = {
        "rate_limit_retries": "Throttle Booking Retries",
        "enable_payment_circuit_breaker": "Enable Eligibility Circuit Breaker",
        "increase_retry_backoff": "Increase Booking Retry Backoff",
        "shorten_slot_hold_ttl": "Shorten Slot Hold TTL",
        "route_to_callback_queue": "Route Overflow to Callback Queue",
        "reserve_priority_slots": "Reserve Priority Slots",
        "restart_payment": "Restart Eligibility Service",
        "shift_traffic": "Shift Scheduling Traffic",
        "disable_flag": "Disable Online Scheduling",
    }
    selector_map = {
        "rate_limit_retries": "#action-rate_limit_retries",
        "enable_payment_circuit_breaker": "#action-enable_payment_circuit_breaker",
        "increase_retry_backoff": "#action-increase_retry_backoff",
        "shorten_slot_hold_ttl": "#action-shorten_slot_hold_ttl",
        "route_to_callback_queue": "#action-route_to_callback_queue",
        "reserve_priority_slots": "#action-reserve_priority_slots",
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
            "Run verify and inspect the live verdict on that same page.",
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
    return load_json_path(plan_path)


def write_outputs(payload: dict, scenario_id: str, suffix: str) -> None:
    out = Path("outputs") / f"{scenario_id}_{suffix}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    if not out.exists():
        raise RuntimeError(f"Expected artifact write failed: {out}")
    md_out = Path("outputs") / f"{scenario_id}_{suffix}.md"
    md_out.write_text(render_markdown(payload))


def build_browser_playbook(plan: dict) -> dict:
    browser = plan["browser_workflow"]
    chosen = plan["best_action"]
    return {
        "plan_id": plan.get("plan_id"),
        "artifact_id": plan.get("artifact_id"),
        "workflow": {
            "orchestrator": "openclaw",
            "level": plan["workflow"]["level"],
            "phase": "browser_playbook",
        },
        "scenario_id": plan["scenario_id"],
        "goal": "Open the dedicated OpenClaw execution page, apply the selected remediation with one browser action, then run verify and inspect the same page for the live verdict.",
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
                "command": ".venv/bin/python run.py alerts/latest.json --phase verify",
                "purpose": "run_post_action_verification",
            },
            {
                "kind": "inspect",
                "target": browser["openclaw_execution_url"],
                "purpose": "inspect_same_page_for_live_verdict_after_verify",
            },
            {"kind": "read", "target": "outputs/alert_latest_report.json", "purpose": "summarize_final_report"},
        ],
        "expected_before": plan["observed_condition"]["metrics"],
        "predicted_after": plan["predicted_effects_by_action"].get(chosen["id"], {}),
    }


def build_openclaw_demo_mode(plan: dict) -> dict:
    chosen = plan["best_action"]
    playbook = build_browser_playbook(plan)
    execution_surface = playbook["urls"]["openclaw_execution"]
    artifact_id = str(plan.get("artifact_id", plan["scenario_id"]))
    watch_state = load_watch_state()
    return {
        "plan_id": plan.get("plan_id"),
        "workflow": {
            "orchestrator": "openclaw",
            "phase": "demo_mode",
            "workspace_skill": "incident_guardrail",
            "launch_style": "armed_background_watch",
        },
        "scenario_id": plan["scenario_id"],
        "goal": "Arm OpenClaw once, let it wait for the next firing incident from the Alertmanager webhook, then execute the browser remediation from one dedicated execution page, run verify, inspect the live verdict on that same page, summarize, and return to watch mode.",
        "starter_prompt": default_openclaw_demo_prompt(),
        "artifacts": {
            "plan": f"outputs/{artifact_id}_plan.json",
            "browser_playbook": f"outputs/{artifact_id}_browser_playbook.json",
            "report": f"outputs/{artifact_id}_report.json",
        },
        "watch_state": {
            "status": watch_state.get("status", "idle"),
            "armed": watch_state.get("armed", False),
            "pending_incident": watch_state.get("pending_incident"),
            "active_incident": watch_state.get("active_incident"),
        },
        "demo_contract": {
            "watch_command": ".venv/bin/python run.py alerts/latest.json --phase watch",
            "await_demo_command": ".venv/bin/python run.py alerts/latest.json --phase await-demo",
            "run_first_command": ".venv/bin/python run.py alerts/latest.json --phase demo",
            "verify_command": ".venv/bin/python run.py alerts/latest.json --phase verify",
            "fallback_command": ".venv/bin/python run.py alerts/latest.json --phase fallback",
            "must_start_in_chat": True,
            "must_wait_for_incident": True,
            "manual_console_buttons_are_fallback_only": True,
            "return_to_watch_after_summary": True,
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
                "kind": "inspect",
                "target": execution_surface,
                "purpose": "inspect_same_page_for_live_verdict_after_verify",
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
            "dangerous_reflex": plan.get("dangerous_reflex", "restart_payment"),
            "chosen_action": chosen["id"],
        },
        "fallback_contract": {
            "trigger": "browser tool times out, fails to load the dashboard, or loses browser control",
            "command": ".venv/bin/python run.py alerts/latest.json --phase fallback",
            "must_say": "execution used the saved-plan fallback because browser control was unavailable",
            "must_stay_on_execution_surface_after_fallback": True,
            "verdict_url": execution_surface,
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


def dangerous_reflex_for_scenario(
    scenario_id: str,
    candidate_actions: list[dict],
    chosen_action_id: str,
    constraints: dict | None = None,
) -> str:
    constraints = constraints or {}
    candidate_ids = [action.get("id") for action in candidate_actions]
    if scenario_id == "seat_hold_clog":
        for action_id in ("enable_payment_circuit_breaker", "shift_traffic", "restart_payment"):
            if action_id in candidate_ids and action_id != chosen_action_id:
                return action_id
    if scenario_id == "regional_saturation":
        secondary_headroom = float(constraints.get("regional_headroom_secondary", 1.0))
        if secondary_headroom <= 0.24 and "shift_traffic" in candidate_ids and chosen_action_id != "shift_traffic":
            return "shift_traffic"
    if "restart_payment" in candidate_ids and chosen_action_id != "restart_payment":
        return "restart_payment"
    for action_id in candidate_ids:
        if action_id != chosen_action_id:
            return str(action_id)
    return "restart_payment"


def default_openclaw_demo_prompt() -> str:
    return (
        "Use the incident_guardrail skill in this workspace and stay in the same task as a persistent responder. "
        "Start by running `.venv/bin/python run.py alerts/latest.json --phase await-demo` and remain idle while it blocks. "
        "Only after that command returns should you continue on the single `/openclaw-execution` page, verify or fallback, summarize the verdict, "
        "and then return to `.venv/bin/python run.py alerts/latest.json --phase await-demo` again. "
        "Do not resume stale artifacts before `await-demo` returns."
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

- Arm watcher first: `{demo_mode['demo_contract']['watch_command']}`
- Preferred blocking arm command: `{demo_mode['demo_contract']['await_demo_command']}`
- When incident is latched, run: `{demo_mode['demo_contract']['run_first_command']}`
- Verify after browser action: `{demo_mode['demo_contract']['verify_command']}`
- Fallback if browser control fails: `{demo_mode['demo_contract']['fallback_command']}`
- Return to watch after summary: `{demo_mode['demo_contract']['return_to_watch_after_summary']}`
- Console buttons are fallback only: `{demo_mode['demo_contract']['manual_console_buttons_are_fallback_only']}`

## Watch State

- Status: `{demo_mode['watch_state']['status']}`
- Armed: `{demo_mode['watch_state']['armed']}`
- Pending incident latched: `{bool(demo_mode['watch_state']['pending_incident'])}`

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
    if not out.exists():
        raise RuntimeError(f"Expected artifact write failed: {out}")
    md_out = Path("outputs") / f"{scenario_id}_browser_playbook.md"
    md_out.write_text(render_browser_playbook_markdown(playbook))


def write_openclaw_demo_mode(plan: dict, scenario_id: str) -> None:
    demo_mode = build_openclaw_demo_mode(plan)
    out = Path("outputs") / f"{scenario_id}_openclaw_demo.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(demo_mode, indent=2))
    if not out.exists():
        raise RuntimeError(f"Expected artifact write failed: {out}")
    md_out = Path("outputs") / f"{scenario_id}_openclaw_demo.md"
    md_out.write_text(render_openclaw_demo_markdown(demo_mode))


def wait_for_updated_evidence(
    payload: dict,
    before_evidence: dict,
    expected_metrics: dict | None = None,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float = 2.0,
) -> dict:
    def _with_expected_fallback(latest: dict) -> dict:
        if expected_metrics and latest.get("metrics", {}) != expected_metrics:
            return evidence_from_expected_state(latest, expected_metrics)
        return latest

    if before_evidence.get("prometheus", {}).get("source") != "live":
        return _with_expected_fallback(collect_evidence(payload))

    timeout = (
        float(os.environ.get("AUTO_VERIFY_SETTLE_SECONDS", "20"))
        if timeout_seconds is None
        else timeout_seconds
    )
    if timeout <= 0:
        return _with_expected_fallback(collect_evidence(payload))

    before_metrics = before_evidence.get("metrics", {})
    before_alerts = before_evidence.get("prometheus", {}).get("active_alerts", [])
    deadline = time.monotonic() + timeout
    latest = collect_evidence(payload)

    while True:
        if latest.get("prometheus", {}).get("source") != "live":
            return _with_expected_fallback(latest)
        latest_metrics = latest.get("metrics", {})
        if expected_metrics and latest_metrics == expected_metrics:
            return latest
        if expected_metrics is None and latest_metrics != before_metrics:
            return latest
        if expected_metrics is None and latest.get("prometheus", {}).get("active_alerts", []) != before_alerts:
            return latest
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _with_expected_fallback(latest)
        time.sleep(min(poll_interval_seconds, remaining))
        latest = collect_evidence(payload)


if __name__ == "__main__":
    main()
