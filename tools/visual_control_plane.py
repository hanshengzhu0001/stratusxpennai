from __future__ import annotations

import json
import time
from html import escape
from pathlib import Path

from agents.incident_classifier import classify_incident
from fastapi import FastAPI, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from run import (
    build_plan_report,
    build_verify_report,
    load_incident_input,
    load_json_path,
    load_local_env,
    load_saved_plan,
    resolve_scenario_context_id,
    wait_for_updated_evidence,
    write_browser_playbook,
    write_openclaw_demo_mode,
    write_outputs,
)
from tools.execute_action import execute_action
from tools.incident_cases import build_alert_payload_for_scenario
from tools.incident_watch import clear_watch_state, load_watch_state
from tools.prometheus_client import collect_evidence, save_alert_payload

from tools.runtime_state import (
    apply_action,
    control_plane_urls,
    current_constraints,
    derive_metrics,
    load_state,
    reset_state,
    set_flag,
    set_scenario,
    trigger_scenario,
)
from tools.scenario_catalog import (
    default_state_for_scenario,
    default_sequence_preview,
    derive_business_metrics,
    get_scenario,
    scenario_catalog,
)

app = FastAPI(title="Guardrail Console")

BASE_URL = "http://127.0.0.1:8010"
VIEW_ORDER = ["incident", "decision", "execution", "verdict"]
DEFAULT_ALERT_PATH = Path("alerts/latest.json")
ARTIFACT_PATHS = {
    "plan": Path("outputs/alert_latest_plan.json"),
    "playbook": Path("outputs/alert_latest_browser_playbook.json"),
    "demo": Path("outputs/alert_latest_openclaw_demo.json"),
    "report": Path("outputs/alert_latest_report.json"),
}
ACTION_LABELS = {
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
ACTION_SUMMARIES = {
    "rate_limit_retries": "Caps booking retry fan-out from the portal and scheduling workers.",
    "enable_payment_circuit_breaker": "Fails fast on degraded eligibility verification to protect the scheduling path.",
    "increase_retry_backoff": "Spaces repeated booking attempts to reduce pressure on downstream verification.",
    "shorten_slot_hold_ttl": "Releases trapped appointment inventory faster so online access stops starving real patients.",
    "route_to_callback_queue": "Moves a controlled slice of demand to staffed callback instead of repeated online contention.",
    "reserve_priority_slots": "Protects urgent or high-priority patients when fairness breaks down under slot pressure.",
    "restart_payment": "Restarts eligibility workers after pressure has already been contained.",
    "shift_traffic": "Moves a controlled share of scheduling load to a secondary region.",
    "disable_flag": "Temporarily disables online self-scheduling and routes patients to staffed fallback.",
}
ACTION_HELP = {
    "rate_limit_retries": "Reduce booking retry fan-out so access can stabilize before more slots are locked.",
    "enable_payment_circuit_breaker": "Fail fast on degraded eligibility checks to protect the scheduling path and preserve capacity.",
    "increase_retry_backoff": "Space out repeated booking attempts so the dependency can recover without additional surge pressure.",
    "shorten_slot_hold_ttl": "Shorten slot-hold lifetime so duplicate attempts stop trapping scarce inventory.",
    "route_to_callback_queue": "Move excess booking demand into staffed callback when online fairness is degrading.",
    "reserve_priority_slots": "Keep a protected slice of appointment capacity available for high-priority patients.",
    "restart_payment": "Restart eligibility workers only if retry pressure is already controlled and cold-start risk is acceptable.",
    "shift_traffic": "Shift a controlled portion of scheduling traffic to spare regional capacity when saturation is localized.",
    "disable_flag": "Pause online self-scheduling and route patients to staffed call-center or callback fallback.",
}
FLAG_LABELS = {
    "payment_service_unreachable": "eligibility_service_degraded",
    "loadgenerator_flood_homepage": "patient_portal_surge",
    "retry_rate_limit_enabled": "booking_retry_throttle_enabled",
    "payment_circuit_breaker_enabled": "eligibility_circuit_breaker_enabled",
    "retry_backoff_enabled": "booking_retry_backoff_enabled",
    "traffic_shift_enabled": "regional_traffic_shift_enabled",
    "payment_feature_disabled": "online_scheduling_disabled",
}

load_local_env()


@app.get("/", response_class=HTMLResponse)
def dashboard(view: str = Query("incident")) -> str:
    active_view = view if view in VIEW_ORDER else "incident"
    state = load_state()
    watch_state = load_watch_state()
    technical_metrics = derive_metrics(state)
    business_metrics = derive_business_metrics(state, technical_metrics)
    urls = control_plane_urls(BASE_URL)
    active_scenario = get_scenario(state.get("active_scenario"))
    scenarios = scenario_catalog()
    plan = _load_artifact("plan")
    playbook = _load_artifact("playbook")
    demo_mode = _load_artifact("demo")
    report = _load_artifact("report")

    if active_view == "incident":
        body = _render_incident_view(
            state, technical_metrics, business_metrics, active_scenario, scenarios, demo_mode
        )
    elif active_view == "decision":
        body = _render_decision_view(plan, active_scenario)
    elif active_view == "execution":
        body = _render_execution_view(state, plan, playbook, report, urls, active_scenario)
    else:
        body = _render_verdict_view(report, plan, state, technical_metrics, business_metrics)

    return _render_shell(
        active_view=active_view,
        body=body,
        state=state,
        watch_state=watch_state,
        technical_metrics=technical_metrics,
        business_metrics=business_metrics,
        active_scenario=active_scenario,
    )


@app.get("/operations", response_class=HTMLResponse)
def operations_dashboard() -> str:
    state = load_state()
    watch_state = load_watch_state()
    technical_metrics = derive_metrics(state)
    business_metrics = derive_business_metrics(state, technical_metrics)
    constraints = current_constraints(state)
    active_scenario = get_scenario(state.get("active_scenario"))
    scenarios = scenario_catalog()
    plan = _load_artifact("plan")
    report = _load_artifact("report")
    return _render_operations_dashboard(
        state=state,
        technical_metrics=technical_metrics,
        business_metrics=business_metrics,
        constraints=constraints,
        active_scenario=active_scenario,
        scenarios=scenarios,
        plan=plan,
        report=report,
        watch_state=watch_state,
    )


@app.get("/feature-flags", response_class=HTMLResponse)
def feature_flags() -> str:
    state = load_state()
    active_scenario = get_scenario(state.get("active_scenario"))
    action_buttons = [
        ("rate_limit_retries", ACTION_LABELS["rate_limit_retries"]),
        ("enable_payment_circuit_breaker", ACTION_LABELS["enable_payment_circuit_breaker"]),
        ("increase_retry_backoff", ACTION_LABELS["increase_retry_backoff"]),
        ("shorten_slot_hold_ttl", ACTION_LABELS["shorten_slot_hold_ttl"]),
        ("route_to_callback_queue", ACTION_LABELS["route_to_callback_queue"]),
        ("reserve_priority_slots", ACTION_LABELS["reserve_priority_slots"]),
        ("restart_payment", ACTION_LABELS["restart_payment"]),
        ("shift_traffic", ACTION_LABELS["shift_traffic"]),
        ("disable_flag", ACTION_LABELS["disable_flag"]),
    ]
    buttons = "".join(_action_form(action_id, label) for action_id, label in action_buttons)
    toggles = "".join(
        _toggle_form(name, enabled)
        for name, enabled in [
            ("payment_service_unreachable", state["payment_service_unreachable"]),
            ("loadgenerator_flood_homepage", state["loadgenerator_flood_homepage"]),
            ("retry_rate_limit_enabled", state["retry_rate_limit_enabled"]),
            ("payment_circuit_breaker_enabled", state["payment_circuit_breaker_enabled"]),
            ("retry_backoff_enabled", state["retry_backoff_enabled"]),
            ("traffic_shift_enabled", state["traffic_shift_enabled"]),
            ("payment_feature_disabled", state["payment_feature_disabled"]),
        ]
    )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Guardrail Console - Manual Ops Controls</title>
  <style>
    :root {{ --bg:#f4efe7; --ink:#15202b; --accent:#b84f1f; --card:#fffaf3; --line:#deceb6; --muted:#6c665d; }}
    body {{ font-family: Iowan Old Style, Georgia, serif; background: linear-gradient(180deg, #fff8ef 0%, var(--bg) 100%); color: var(--ink); margin:0; }}
    .wrap {{ max-width: 1120px; margin:0 auto; padding: 32px; }}
    .top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:20px; margin-bottom:24px; }}
    .badge {{ display:inline-block; padding:7px 12px; border-radius:999px; background:#efe1cc; margin-right:8px; margin-bottom:8px; }}
    .grid {{ display:grid; grid-template-columns: 1.15fr 0.85fr; gap:18px; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:20px; padding:20px; box-shadow:0 18px 42px rgba(21,32,43,0.08); }}
    button {{ background:#0d6959; color:white; border:none; border-radius:12px; padding:11px 16px; cursor:pointer; font:inherit; }}
    .danger button {{ background:#a53d2a; }}
    .muted {{ color:var(--muted); }}
    a {{ color:inherit; }}
    .action-card {{ border:1px solid var(--line); border-radius:16px; padding:14px; background:rgba(255,255,255,0.42); margin-bottom:10px; }}
    .action-card strong {{ display:block; margin-bottom:6px; }}
    .action-meta {{ color:var(--muted); font-size:0.94rem; line-height:1.5; margin-bottom:10px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <div class="badge">Scenario: {escape(active_scenario['label'])}</div>
        <div class="badge">Expected first action: {escape(_action_label(active_scenario['expected_first_action']))}</div>
        <h1 style="margin:14px 0 6px;">Manual Ops Controls</h1>
        <p class="muted" style="max-width:720px;">This is the staffed fallback surface for healthcare access operations. Use it only if the primary OpenClaw browser flow is unavailable.</p>
      </div>
      <div>
        <a href="/">Return to Guardrail Console</a>
      </div>
    </div>
    <div class="grid">
      <div class="card">
        <h2 style="margin-top:0;">Apply Remediation</h2>
        <p class="muted">Use the chosen action from the browser playbook. The online-scheduling shutdown stays visible but visually distinct because it routes patients to staffed fallback.</p>
        {buttons}
        <form method="post" action="/api/reset" style="margin-top:12px;">
          <input type="hidden" name="return_to" value="feature-flags">
          <button>Reset Active Scenario</button>
        </form>
        <form method="post" action="/api/disarm-watch" style="margin-top:12px;">
          <input type="hidden" name="return_to" value="feature-flags">
          <button>Disarm OpenClaw Watcher</button>
        </form>
      </div>
      <div class="card">
        <h2 style="margin-top:0;">Scenario Controls</h2>
        <p class="muted">These rehearsal controls sit behind the healthcare-access scenario selector.</p>
        {toggles}
      </div>
    </div>
  </div>
</body>
</html>"""


@app.get("/openclaw-execution", response_class=HTMLResponse)
def openclaw_execution(stage: str = Query("before")) -> str:
    state = load_state()
    technical_metrics = derive_metrics(state)
    business_metrics = derive_business_metrics(state, technical_metrics)
    plan = _load_artifact("plan")
    report = _load_artifact("report")
    return _render_openclaw_execution_surface(
        plan=plan,
        report=report,
        state=state,
        technical_metrics=technical_metrics,
        business_metrics=business_metrics,
        stage=stage,
    )


@app.get("/api/state")
def state_api() -> dict:
    state = load_state()
    watch_state = load_watch_state()
    technical_metrics = derive_metrics(state)
    plan = _load_artifact("plan")
    report = _load_artifact("report")
    return {
        "scenario": get_scenario(state.get("active_scenario")),
        "state": state,
        "watch_state": watch_state,
        "metrics": technical_metrics,
        "business_metrics": derive_business_metrics(state, technical_metrics),
        "constraints": current_constraints(state),
        "plan": plan,
        "report": report,
        "updated_at": time.time(),
        "artifacts": {
            name: path.exists()
            for name, path in ARTIFACT_PATHS.items()
        },
    }


@app.post("/api/actions/{action_id}")
def api_action(action_id: str) -> dict:
    state = apply_action(action_id)
    _clear_report_artifacts()
    technical_metrics = derive_metrics(state)
    return {
        "status": "ok",
        "scenario": get_scenario(state.get("active_scenario")),
        "state": state,
        "metrics": technical_metrics,
        "business_metrics": derive_business_metrics(state, technical_metrics),
    }


@app.post("/api/plan")
def api_plan(
    input_path: str = Form(str(DEFAULT_ALERT_PATH)),
    return_to: str = Form("decision"),
) -> RedirectResponse:
    payload, resolved_path = _load_alert_payload(input_path)
    source_type, state, evidence = _load_incident_snapshot(payload)
    artifact_id = payload.get("id", "alert_latest")
    scenario_id = resolve_scenario_context_id(payload, state, artifact_id)
    plan = build_plan_report(payload, scenario_id, artifact_id, source_type, resolved_path, state, evidence)
    write_outputs(plan, artifact_id, "plan")
    write_browser_playbook(plan, artifact_id)
    write_openclaw_demo_mode(plan, artifact_id)
    _clear_report_artifacts()
    return RedirectResponse(_return_path(return_to), status_code=303)


@app.post("/api/execute-planned")
def api_execute_planned(return_to: str = Form("execution")) -> RedirectResponse:
    plan = _load_artifact("plan")
    if plan:
        chosen = plan.get("best_action", {}).get("id")
        if chosen:
            apply_action(chosen)
            _clear_report_artifacts()
    return RedirectResponse(_return_path(return_to), status_code=303)


@app.post("/api/verify")
def api_verify(
    input_path: str = Form(str(DEFAULT_ALERT_PATH)),
    return_to: str = Form("verdict"),
) -> RedirectResponse:
    payload, resolved_path = _load_alert_payload(input_path)
    artifact_id = payload.get("id", "alert_latest")
    plan = load_saved_plan(artifact_id)
    scenario_id = resolve_scenario_context_id(payload, plan, artifact_id)
    current_evidence = wait_for_updated_evidence(
        payload,
        before_evidence={
            "metrics": plan.get("observed_condition", {}).get("metrics", {}),
            "prometheus": plan.get("observed_condition", {}).get("prometheus", {}),
        },
        expected_metrics=derive_metrics(load_state()),
    )
    source_type = plan.get("source_type", "alertmanager_webhook")
    current_state = derive_state_for_verify(payload, current_evidence)
    report = build_verify_report(
        payload=payload,
        scenario_id=scenario_id,
        source_type=source_type,
        input_path=resolved_path,
        current_state=current_state,
        current_evidence=current_evidence,
        plan=plan,
        execution=None,
        mode="browser_verify",
    )
    write_outputs(report, artifact_id, "report")
    return RedirectResponse(_return_path(return_to), status_code=303)


@app.post("/api/autorun")
def api_autorun(
    input_path: str = Form(str(DEFAULT_ALERT_PATH)),
    return_to: str = Form("verdict"),
) -> RedirectResponse:
    payload, resolved_path = _load_alert_payload(input_path)
    source_type, state, evidence = _load_incident_snapshot(payload)
    artifact_id = payload.get("id", "alert_latest")
    scenario_id = resolve_scenario_context_id(payload, state, artifact_id)
    plan = build_plan_report(payload, scenario_id, artifact_id, source_type, resolved_path, state, evidence)
    write_outputs(plan, artifact_id, "plan")
    write_browser_playbook(plan, artifact_id)
    write_openclaw_demo_mode(plan, artifact_id)
    execution = execute_action(plan["best_action"])
    current_evidence = wait_for_updated_evidence(
        payload,
        before_evidence={
            "metrics": plan.get("observed_condition", {}).get("metrics", {}),
            "prometheus": plan.get("observed_condition", {}).get("prometheus", {}),
        },
        expected_metrics=derive_metrics(execution["state_after_action"]),
    )
    current_state = derive_state_for_verify(payload, current_evidence)
    report = build_verify_report(
        payload=payload,
        scenario_id=scenario_id,
        source_type=source_type,
        input_path=resolved_path,
        current_state=current_state,
        current_evidence=current_evidence,
        plan=plan,
        execution=execution,
        mode="console_autorun",
    )
    write_outputs(report, artifact_id, "report")
    return RedirectResponse(_return_path(return_to), status_code=303)


@app.post("/api/scenario")
def api_scenario(scenario_id: str = Form(...), return_to: str = Form("incident")) -> RedirectResponse:
    set_scenario(scenario_id)
    _clear_console_artifacts()
    return RedirectResponse(f"/?view={return_to}", status_code=303)


@app.post("/api/toggle")
def api_toggle(
    flag_name: str = Form(...),
    enabled: str = Form(...),
    return_to: str = Form("feature-flags"),
) -> RedirectResponse:
    set_flag(flag_name, enabled.lower() == "true")
    _clear_report_artifacts()
    return RedirectResponse(_return_path(return_to), status_code=303)


@app.post("/api/execute")
def execute_from_form(
    action_id: str = Form(...),
    return_to: str = Form("feature-flags"),
) -> RedirectResponse:
    apply_action(action_id)
    _clear_report_artifacts()
    return RedirectResponse(_return_path(return_to), status_code=303)


@app.post("/api/trigger-incident")
def trigger_incident_from_form(
    scenario_id: str = Form(...),
    return_to: str = Form("operations"),
) -> RedirectResponse:
    trigger_scenario(scenario_id)
    payload = build_alert_payload_for_scenario(scenario_id)
    save_alert_payload(payload)
    _clear_console_artifacts()
    return RedirectResponse(_return_path(return_to), status_code=303)


@app.post("/api/reset")
def reset_from_form(
    return_to: str = Form("incident"),
    scenario_id: str | None = Form(None),
) -> RedirectResponse:
    if scenario_id:
        set_scenario(scenario_id)
    else:
        reset_state()
    _clear_console_artifacts()
    return RedirectResponse(_return_path(return_to), status_code=303)


@app.post("/api/disarm-watch")
def disarm_watch_from_form(return_to: str = Form("incident")) -> RedirectResponse:
    clear_watch_state()
    return RedirectResponse(_return_path(return_to), status_code=303)


def _render_shell(
    active_view: str,
    body: str,
    state: dict,
    watch_state: dict,
    technical_metrics: dict,
    business_metrics: dict,
    active_scenario: dict,
) -> str:
    last_action = _action_label(state["last_action"])
    nav = "".join(
        f'<a class="tab{" active" if view == active_view else ""}" href="/?view={view}">{view.title()}</a>'
        for view in VIEW_ORDER
    )
    pills = "".join(
        [
            f'<span class="pill" id="pill-scenario">Scenario: {escape(active_scenario["label"])}</span>',
            f'<span class="pill" id="pill-watch">OpenClaw: {escape(_watch_label(watch_state))}</span>',
            f'<span class="pill" id="pill-last-action">Last action: {escape(last_action)}</span>',
            f'<span class="pill" id="pill-retry-factor">Retry factor: {business_metrics["retry_amplification_factor"]}x</span>',
            f'<span class="pill" id="pill-booking-completion">Booking completion: {business_metrics["payment_success_rate"]:.2f}</span>',
        ]
    )
    action_labels_json = json.dumps(ACTION_LABELS)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Guardrail Console</title>
  <style>
    :root {{
      --bg:#f2ede4;
      --panel:#fff9f1;
      --ink:#16212b;
      --muted:#696055;
      --accent:#bb5524;
      --accent-soft:#f0dfc8;
      --line:#ddccb0;
      --ok:#215e4f;
      --warn:#9c6029;
      --bad:#983729;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0;
      font-family: Iowan Old Style, Georgia, serif;
      background:
        radial-gradient(circle at top left, #fff7eb 0%, transparent 30%),
        linear-gradient(180deg, #fff8ef 0%, var(--bg) 100%);
      color:var(--ink);
    }}
    .shell {{ max-width: 1240px; margin:0 auto; padding: 26px 26px 48px; }}
    .masthead {{ display:grid; gap:14px; margin-bottom:20px; }}
    .eyebrow {{ letter-spacing:0.08em; text-transform:uppercase; font-size:0.82rem; color:var(--muted); }}
    .hero {{ display:flex; justify-content:space-between; gap:20px; align-items:flex-start; }}
    .hero h1 {{ margin:0; font-size:2.7rem; line-height:1; }}
    .hero p {{ max-width:760px; margin:10px 0 0; color:var(--muted); font-size:1.02rem; }}
    .pill {{ display:inline-block; margin-right:8px; margin-bottom:8px; padding:7px 12px; border-radius:999px; background:var(--accent-soft); }}
    .nav {{ display:flex; gap:10px; flex-wrap:wrap; }}
    .secondary-nav {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:8px; }}
    .tab {{
      text-decoration:none;
      color:var(--ink);
      padding:11px 15px;
      border:1px solid var(--line);
      border-radius:14px;
      background:rgba(255,255,255,0.55);
    }}
    .tab.active {{ background:var(--accent); color:white; border-color:var(--accent); }}
    .link-chip {{
      text-decoration:none;
      color:var(--ink);
      padding:8px 12px;
      border:1px solid var(--line);
      border-radius:999px;
      background:rgba(255,255,255,0.45);
      font-size:0.92rem;
    }}
    .grid {{ display:grid; gap:18px; }}
    .grid.two {{ grid-template-columns: 1.08fr 0.92fr; }}
    .grid.three {{ grid-template-columns: repeat(3, 1fr); }}
    .card {{
      background:var(--panel);
      border:1px solid var(--line);
      border-radius:22px;
      padding:20px;
      box-shadow:0 18px 44px rgba(22,33,43,0.08);
    }}
    .card h2, .card h3 {{ margin-top:0; }}
    .muted {{ color:var(--muted); }}
    .kpi {{ font-size:2rem; margin:6px 0 0; }}
    .list {{ margin:0; padding-left:18px; line-height:1.65; }}
    .table {{ width:100%; border-collapse:collapse; }}
    .table th, .table td {{ padding:10px 8px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
    .table th {{ color:var(--muted); font-weight:normal; }}
    .status {{
      display:inline-block;
      padding:5px 10px;
      border-radius:999px;
      font-size:0.9rem;
    }}
    .status.done {{ background:#dcede5; color:var(--ok); }}
    .status.pending {{ background:#f2e8d7; color:var(--warn); }}
    .status.blocked {{ background:#f3ded8; color:var(--bad); }}
    .timeline {{ display:grid; gap:12px; }}
    .timeline-item {{ display:flex; gap:12px; align-items:flex-start; }}
    .step {{
      width:30px; height:30px; border-radius:999px; display:flex; align-items:center; justify-content:center;
      background:#efe2cd; color:var(--ink); flex:0 0 auto; margin-top:2px;
    }}
    .step.done {{ background:#dcede5; color:var(--ok); }}
    .step.active {{ background:var(--accent); color:white; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .action-grid {{ display:grid; grid-template-columns: repeat(2, 1fr); gap:12px; }}
    .mini {{ border:1px solid var(--line); border-radius:16px; padding:14px; background:rgba(255,255,255,0.35); }}
    form.inline {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
    select, button {{
      font:inherit;
      border-radius:12px;
      border:1px solid var(--line);
      padding:10px 12px;
      background:white;
    }}
    button.primary {{ background:var(--accent); color:white; border-color:var(--accent); cursor:pointer; }}
    a.button {{ text-decoration:none; color:white; background:var(--accent); padding:11px 14px; border-radius:12px; display:inline-block; }}
    .callout {{ border-left:4px solid var(--accent); padding-left:12px; }}
    .loading-overlay {{
      position:fixed; inset:0; background:rgba(20,28,35,0.72); display:none; align-items:center; justify-content:center;
      z-index:9999; padding:24px;
    }}
    .loading-card {{
      width:min(520px, 100%); background:var(--panel); border-radius:22px; border:1px solid var(--line);
      padding:24px; box-shadow:0 24px 60px rgba(0,0,0,0.22);
    }}
    .loading-title {{ margin:0 0 8px; font-size:1.5rem; }}
    .loading-subtitle {{ margin:0 0 16px; color:var(--muted); }}
    .progress-track {{ height:14px; border-radius:999px; background:#eadbc6; overflow:hidden; }}
    .progress-bar {{ height:100%; width:8%; border-radius:999px; background:linear-gradient(90deg, var(--accent), #e38e45); transition:width 0.9s ease; }}
    .loading-steps {{ margin:14px 0 0; padding-left:18px; color:var(--muted); line-height:1.6; }}
    @media (max-width: 940px) {{
      .hero {{ display:grid; }}
      .grid.two, .grid.three, .action-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="masthead">
      <div class="eyebrow">OpenClaw + Stratus Counterfactual Guardrail</div>
      <div class="hero">
        <div>
          <h1>Guardrail Console</h1>
          <p>One operator-facing shell for healthcare access incidents, decisioning, browser execution, and final verdict. This is Hansen's operator surface for the Week 2 demo.</p>
          <div class="secondary-nav">
            <a class="link-chip" href="/operations">Open Telehealth Ops Board</a>
            <a class="link-chip" href="/openclaw-execution">OpenClaw Execution Surface</a>
            <a class="link-chip" href="/feature-flags">Manual Ops Controls</a>
          </div>
        </div>
        <div class="nav">{nav}</div>
      </div>
      <div>{pills}</div>
    </div>
    {body}
  </div>
  <div class="loading-overlay" id="loading-overlay" aria-hidden="true">
    <div class="loading-card">
      <h2 class="loading-title" id="loading-title">Working...</h2>
      <p class="loading-subtitle" id="loading-subtitle">The Guardrail Console is processing the next step.</p>
      <div class="progress-track"><div class="progress-bar" id="loading-bar"></div></div>
      <ul class="loading-steps" id="loading-steps">
        <li>Gathering incident state</li>
        <li>Waiting for Stratus / Prometheus</li>
        <li>Writing the next artifact</li>
      </ul>
    </div>
  </div>
  <script>
    (() => {{
      const overlay = document.getElementById('loading-overlay');
      const title = document.getElementById('loading-title');
      const subtitle = document.getElementById('loading-subtitle');
      const bar = document.getElementById('loading-bar');
      const steps = document.getElementById('loading-steps');
      const progressFrames = {{
        plan: [12, 34, 61, 84],
        verify: [16, 42, 68, 88],
        autorun: [10, 28, 49, 66, 83],
        execute: [20, 55, 82],
      }};
      const subtitles = {{
        plan: 'Querying evidence, selecting the shortlist, and waiting for Stratus to finish planning.',
        verify: 'Waiting for the soak window, scraping Prometheus, and writing the verdict.',
        autorun: 'Running plan, execution, and verification in one automated rehearsal.',
        execute: 'Applying the chosen remediation and updating the shared control-plane state.',
      }};
      function showLoading(form) {{
        if (!overlay || !title || !subtitle || !bar || !steps) return;
        const phase = form.dataset.loadingPhase || 'plan';
        title.textContent = form.dataset.loadingLabel || 'Working...';
        subtitle.textContent = subtitles[phase] || 'The Guardrail Console is processing the next step.';
        const phaseSteps = {{
          plan: ['Collecting evidence', 'Shortlisting actions', 'Waiting for Stratus', 'Rendering decision view'],
          verify: ['Reading plan', 'Waiting for scrape refresh', 'Comparing predicted vs actual', 'Writing verdict'],
          autorun: ['Planning', 'Applying action', 'Waiting for verification window', 'Writing final report'],
          execute: ['Reading chosen action', 'Applying control-plane change', 'Refreshing execution state'],
        }}[phase] || ['Working'];
        steps.innerHTML = phaseSteps.map((item) => `<li>${{item}}</li>`).join('');
        overlay.style.display = 'flex';
        overlay.setAttribute('aria-hidden', 'false');
        const frames = progressFrames[phase] || [18, 45, 72, 90];
        bar.style.width = '8%';
        frames.forEach((value, index) => {{
          window.setTimeout(() => {{ bar.style.width = `${{value}}%`; }}, 700 * (index + 1));
        }});
      }}
      document.querySelectorAll('form[data-loading-label]').forEach((form) => {{
        form.addEventListener('submit', () => showLoading(form));
      }});

      const actionLabels = {action_labels_json};
      function actionLabel(actionId) {{
        if (!actionId) return 'none';
        return actionLabels[actionId] || actionId;
      }}
      function watchLabel(watchState) {{
        if (!watchState) return 'Idle';
        const status = watchState.status || 'idle';
        if (status === 'pending' && watchState.pending_incident) return 'Pending incident latched';
        if (status === 'acknowledged' && watchState.active_incident) return 'Investigating active incident';
        if (watchState.armed) return 'Armed and waiting';
        return 'Idle';
      }}
      function setText(id, value) {{
        const node = document.getElementById(id);
        if (node && value !== undefined && value !== null) node.textContent = value;
      }}
      async function refreshShell() {{
        try {{
          const response = await fetch('/api/state', {{ cache: 'no-store' }});
          if (!response.ok) return;
          const data = await response.json();
          const metrics = data.metrics || {{}};
          const business = data.business_metrics || {{}};
          const state = data.state || {{}};
          const scenario = data.scenario || {{}};
          setText('pill-scenario', `Scenario: ${{scenario.label || 'Unknown'}}`);
          setText('pill-watch', `OpenClaw: ${{watchLabel(data.watch_state)}}`);
          setText('pill-last-action', `Last action: ${{actionLabel(state.last_action)}}`);
          if (business.retry_amplification_factor !== undefined) {{
            setText('pill-retry-factor', `Retry factor: ${{business.retry_amplification_factor}}x`);
          }}
          if (business.payment_success_rate !== undefined) {{
            setText('pill-booking-completion', `Booking completion: ${{Number(business.payment_success_rate).toFixed(2)}}`);
          }}
          if (metrics.latency_p95_ms !== undefined) {{
            setText('incident-latency', `${{metrics.latency_p95_ms}} ms`);
          }}
          if (metrics.error_rate !== undefined) {{
            setText('incident-error', Number(metrics.error_rate).toFixed(2));
          }}
          if (metrics.retry_rate !== undefined) {{
            setText('incident-retry', Number(metrics.retry_rate).toFixed(2));
          }}
          if (business.queue_abandonment_rate !== undefined) {{
            setText('incident-abandonment', Number(business.queue_abandonment_rate).toFixed(2));
          }}
          if (business.payment_success_rate !== undefined) {{
            setText('incident-completion', Number(business.payment_success_rate).toFixed(2));
          }}
          if (business.seat_hold_utilization !== undefined) {{
            setText('incident-slot-utilization', Number(business.seat_hold_utilization).toFixed(2));
          }}
          setText('execution-current-action', actionLabel((data.plan || {{}})?.best_action?.id));
          setText('execution-last-action', actionLabel(state.last_action));
          if (business.retry_amplification_factor !== undefined) {{
            setText('execution-retry-factor', `${{business.retry_amplification_factor}}x`);
          }}
          if (business.payment_success_rate !== undefined) {{
            setText('execution-booking-completion', Number(business.payment_success_rate).toFixed(2));
          }}
        }} catch (_error) {{
          // Keep the current UI visible even if a poll fails.
        }}
      }}
      refreshShell();
      window.setInterval(refreshShell, 2000);
    }})();
  </script>
</body>
</html>"""


def _render_incident_view(
    state: dict,
    technical_metrics: dict,
    business_metrics: dict,
    active_scenario: dict,
    scenarios: list[dict],
    demo_mode: dict | None,
) -> str:
    technical = "".join(
        [
            f"""
            <div class="card">
              <div class="muted">Scheduling Latency P95</div>
              <div class="kpi" id="incident-latency">{technical_metrics['latency_p95_ms']} ms</div>
            </div>
            """,
            f"""
            <div class="card">
              <div class="muted">Scheduling Error Rate</div>
              <div class="kpi" id="incident-error">{technical_metrics['error_rate']:.2f}</div>
            </div>
            """,
            f"""
            <div class="card">
              <div class="muted">Booking Retry Rate</div>
              <div class="kpi" id="incident-retry">{technical_metrics['retry_rate']:.2f}</div>
            </div>
            """,
        ]
    )
    business = "".join(
        [
            f"""
            <div class="card">
              <div class="muted">Scheduling Abandonment</div>
              <div class="kpi" id="incident-abandonment">{business_metrics['queue_abandonment_rate']:.2f}</div>
            </div>
            """,
            f"""
            <div class="card">
              <div class="muted">Booking Completion</div>
              <div class="kpi" id="incident-completion">{business_metrics['payment_success_rate']:.2f}</div>
            </div>
            """,
            f"""
            <div class="card">
              <div class="muted">Slot Hold Utilization</div>
              <div class="kpi" id="incident-slot-utilization">{business_metrics['seat_hold_utilization']:.2f}</div>
            </div>
            """,
        ]
    )
    scenario_options = "".join(
        f'<option value="{escape(item["id"])}"{" selected" if item["id"] == active_scenario["id"] else ""}>{escape(item["label"])}</option>'
        for item in scenarios
    )
    focus = "".join(f"<li>{escape(item)}</li>" for item in active_scenario["sentinel_focus"])
    flags = "".join(
        f'<span class="pill">{escape(label)}</span>'
        for label in [
            f"{_flag_label('payment_service_unreachable')}={str(state['payment_service_unreachable']).lower()}",
            f"{_flag_label('loadgenerator_flood_homepage')}={str(state['loadgenerator_flood_homepage']).lower()}",
            f"{_flag_label('retry_rate_limit_enabled')}={str(state['retry_rate_limit_enabled']).lower()}",
            f"{_flag_label('payment_circuit_breaker_enabled')}={str(state['payment_circuit_breaker_enabled']).lower()}",
            f"{_flag_label('retry_backoff_enabled')}={str(state['retry_backoff_enabled']).lower()}",
        ]
    )
    demo_prompt = escape(
        (demo_mode or {}).get("starter_prompt")
        or "Use the incident_guardrail skill on the latest alert in OpenClaw Demo Mode."
    )
    demo_status = (
        "Demo artifact is ready for OpenClaw to consume."
        if demo_mode
        else "Generate a plan or run demo mode once to produce the OpenClaw handoff artifact."
    )
    return f"""
    <div class="grid two">
      <div class="card">
        <h2 style="margin-bottom:8px;">Incident View</h2>
        <p class="callout"><strong>{escape(active_scenario['label'])}</strong><br>{escape(active_scenario['headline'])}</p>
        <p class="muted">{escape(active_scenario['story'])}</p>
        <p class="muted"><strong>Reset behavior:</strong> {escape(_scenario_reset_copy(active_scenario))}</p>
        <form class="inline" method="post" action="/api/scenario" style="margin-top:16px;">
          <input type="hidden" name="return_to" value="incident">
          <label for="scenario_id"><strong>Scenario</strong></label>
          <select id="scenario_id" name="scenario_id">{scenario_options}</select>
          <button class="primary">Apply Scenario</button>
          <button formaction="/api/reset" name="return_to" value="incident">Reset Current Scenario Baseline</button>
          <button formaction="/api/disarm-watch" name="return_to" value="incident">Disarm OpenClaw Watcher</button>
        </form>
        <form class="inline" method="post" action="/api/reset" style="margin-top:12px;">
          <input type="hidden" name="scenario_id" value="retry_death_spiral">
          <input type="hidden" name="return_to" value="incident">
          <button>Reset to Healthy Baseline</button>
        </form>
        <form class="inline" method="post" action="/api/trigger-incident" style="margin-top:12px;">
          <input type="hidden" name="scenario_id" value="{escape(active_scenario['id'])}">
          <input type="hidden" name="return_to" value="incident">
          <button class="primary">{escape(active_scenario.get('trigger_label', 'Trigger Current Incident'))}</button>
        </form>
        <form class="inline" method="post" action="/api/plan" style="margin-top:12px;" data-loading-label="Planning with Stratus..." data-loading-phase="plan">
          <input type="hidden" name="input_path" value="{escape(str(DEFAULT_ALERT_PATH))}">
          <input type="hidden" name="return_to" value="decision">
          <button class="primary">Generate Guardrail Plan</button>
          <a class="button" href="/?view=decision">Go to Decision View</a>
        </form>
        <form class="inline" method="post" action="/api/autorun" style="margin-top:12px;" data-loading-label="Running the full workflow..." data-loading-phase="autorun">
          <input type="hidden" name="input_path" value="{escape(str(DEFAULT_ALERT_PATH))}">
          <input type="hidden" name="return_to" value="verdict">
          <button>Console Rehearsal: Run Full Flow</button>
        </form>
        <p class="muted" style="margin-top:10px;">Use console rehearsal only as a local backup. The primary demo path should start from OpenClaw chat.</p>
        <div style="margin-top:16px;">{flags}</div>
      </div>
      <div class="card">
        <h2 style="margin-bottom:8px;">OpenClaw Demo Mode</h2>
        <p class="muted">{escape(demo_status)}</p>
        <p class="callout"><strong>Start from OpenClaw chat</strong><br>No manual console clicks are required in the intended final demo path.</p>
        <pre style="white-space:pre-wrap; background:#f8f0e1; border:1px solid var(--line); border-radius:14px; padding:14px;">{demo_prompt}</pre>
        <ul class="list" style="margin-top:14px;">
          <li>Primary artifact: <code>outputs/alert_latest_openclaw_demo.json</code></li>
          <li>Browser handoff: <code>outputs/alert_latest_browser_playbook.json</code></li>
          <li>Verification stays in the local repo workflow.</li>
        </ul>
      </div>
    </div>
    <div class="grid two" style="margin-top:18px;">
      <div class="card">
        <h2 style="margin-bottom:8px;">Sentinel Focus</h2>
        <p class="muted">These are the cues the sentinel agents should prioritize for this scenario.</p>
        <ul class="list">{focus}</ul>
      </div>
    </div>
    <div class="grid three" style="margin-top:18px;">
      {technical}
    </div>
    <div class="grid three" style="margin-top:18px;">
      {business}
    </div>
    """


def _render_decision_view(plan: dict | None, active_scenario: dict) -> str:
    if not plan:
        return f"""
        <div class="card">
          <h2>Decision View</h2>
          <p class="muted">No plan artifact is available yet.</p>
          <p>Generate the plan here to populate the shortlist, ranking, and browser playbook without leaving the console.</p>
          <form class="inline" method="post" action="/api/plan" style="margin-top:12px;" data-loading-label="Planning with Stratus..." data-loading-phase="plan">
            <input type="hidden" name="input_path" value="{escape(str(DEFAULT_ALERT_PATH))}">
            <input type="hidden" name="return_to" value="decision">
            <button class="primary">Generate Guardrail Plan</button>
            <a class="button" href="/?view=incident">Back to Incident</a>
          </form>
        </div>
        """

    shortlist = "".join(
        f"<tr><td><strong>{escape(_action_label(action['id']))}</strong></td><td><code>{escape(action['id'])}</code></td><td>{escape(action['category'])}</td><td>{escape(action['description'])}</td></tr>"
        for action in plan.get("candidate_actions", [])
    )
    rankings = "".join(
        f"<tr><td>{item['rank']}</td><td><strong>{escape(_action_label(item['id']))}</strong></td><td><code>{escape(item['id'])}</code></td><td>{item['confidence']:.2f}</td><td>{escape(item.get('reasoning') or item.get('rationale') or '')}</td></tr>"
        for item in plan.get("stratus_ranking", [])
    )
    rationale = "".join(
        f"<li>{escape(line)}</li>" for line in plan.get("planner", {}).get("selection_rationale", [])
    )
    chosen = plan["best_action"]["id"]
    notes = "".join(f"<li>{escape(note)}</li>" for note in plan.get("notes", []))
    return f"""
    <div class="grid two">
      <div class="card">
        <h2>Decision View</h2>
        <p class="muted">Scenario-aware shortlist generation followed by Stratus guardrail ranking.</p>
        <div class="action-grid" style="margin-top:16px;">
            <div class="mini">
              <div class="muted">Expected first action</div>
            <div class="kpi" style="font-size:1.4rem;">{escape(_action_label(active_scenario['expected_first_action']))}</div>
          </div>
          <div class="mini">
            <div class="muted">Guardrail choice</div>
            <div class="kpi" style="font-size:1.4rem;">{escape(_action_label(chosen))}</div>
          </div>
        </div>
        <div style="margin-top:18px;">
          <strong>Shortlist rationale</strong>
          <ul class="list">{rationale}</ul>
        </div>
        <form class="inline" method="post" action="/api/plan" style="margin-top:18px;" data-loading-label="Refreshing Stratus plan..." data-loading-phase="plan">
          <input type="hidden" name="input_path" value="{escape(str(DEFAULT_ALERT_PATH))}">
          <input type="hidden" name="return_to" value="decision">
          <button class="primary">Regenerate Plan</button>
          <a class="button" href="/?view=execution">Move to Execution</a>
        </form>
      </div>
      <div class="card">
        <h2>Guardrail Notes</h2>
        <p class="muted">This is the Stratus touchpoint rendered into the operator-facing product.</p>
        <ul class="list">{notes or '<li>No guardrail notes were recorded.</li>'}</ul>
      </div>
    </div>
    <div class="grid two" style="margin-top:18px;">
      <div class="card">
        <h3>Scenario Shortlist</h3>
        <table class="table">
          <thead><tr><th>Action</th><th>ID</th><th>Category</th><th>Description</th></tr></thead>
          <tbody>{shortlist}</tbody>
        </table>
      </div>
      <div class="card">
        <h3>Stratus Ranking</h3>
        <table class="table">
          <thead><tr><th>Rank</th><th>Action</th><th>ID</th><th>Conf.</th><th>Why</th></tr></thead>
          <tbody>{rankings}</tbody>
        </table>
      </div>
    </div>
    """


def _render_execution_view(
    state: dict,
    plan: dict | None,
    playbook: dict | None,
    report: dict | None,
    urls: dict,
    active_scenario: dict,
) -> str:
    technical_metrics = derive_metrics(state)
    business_metrics = derive_business_metrics(state, technical_metrics)
    statuses = _workflow_statuses(state, plan, playbook, report)
    timeline = "".join(
        f"""
        <div class="timeline-item">
          <div class="step {'done' if item['status'] == 'done' else 'active' if item['status'] == 'active' else ''}">{item['step']}</div>
          <div>
            <strong>{escape(item['label'])}</strong><br>
            <span class="muted">{escape(item['detail'])}</span>
          </div>
        </div>
        """
        for item in statuses
    )
    chosen = plan.get("best_action", {}).get("id") if plan else None
    chosen_label = playbook.get("chosen_action", {}).get("label") if playbook else None
    sequence = plan.get("action_sequence") if plan else None
    if sequence:
        preview_items = [
            {"step": index + 1, "label": str(item.get("id", item))}
            for index, item in enumerate(sequence[:3])
        ]
    else:
        preview_items = default_sequence_preview(state.get("active_scenario"), chosen)
    sequence_preview = "".join(
        f"<tr><td>{item['step']}</td><td>{escape(item['label'])}</td></tr>"
        for item in preview_items
    )
    playbook_steps = ""
    if playbook:
        playbook_steps = "".join(
            f"<tr><td>{escape(step['kind'])}</td><td><code>{escape(step.get('target', step.get('command', '')))}</code></td><td>{escape(step.get('purpose', ''))}</td></tr>"
            for step in playbook.get("steps", [])
        )
    return f"""
    <div class="grid two">
      <div class="card">
        <h2>Execution View</h2>
        <p class="muted">This is Hansen's shell for connecting planning, browser execution, verification, and final verdict.</p>
        <div class="timeline">{timeline}</div>
        <div style="margin-top:18px;">
          <form class="inline" method="post" action="/api/plan" data-loading-label="Planning with Stratus..." data-loading-phase="plan">
            <input type="hidden" name="input_path" value="{escape(str(DEFAULT_ALERT_PATH))}">
            <input type="hidden" name="return_to" value="execution">
            <button class="primary">Refresh Plan from Latest Alert</button>
            <a class="button" href="/openclaw-execution">Open OpenClaw Execution Surface</a>
          </form>
        </div>
        <div style="margin-top:14px;">
          <form class="inline" method="post" action="/api/execute-planned" data-loading-label="Applying planned remediation..." data-loading-phase="execute">
            <input type="hidden" name="return_to" value="execution">
            <button class="primary">Apply Planned Remediation Here</button>
          </form>
        </div>
        <div style="margin-top:10px;">
          <form class="inline" method="post" action="/api/verify" data-loading-label="Verifying outcome..." data-loading-phase="verify">
            <input type="hidden" name="input_path" value="{escape(str(DEFAULT_ALERT_PATH))}">
            <input type="hidden" name="return_to" value="verdict">
            <button>Verify After Soak</button>
          </form>
        </div>
        <div style="margin-top:10px;">
          <form class="inline" method="post" action="/api/autorun" data-loading-label="Running the full workflow..." data-loading-phase="autorun">
            <input type="hidden" name="input_path" value="{escape(str(DEFAULT_ALERT_PATH))}">
            <input type="hidden" name="return_to" value="verdict">
            <button>Console Rehearsal: Run Full Flow</button>
          </form>
        </div>
      </div>
      <div class="card">
        <h2>Sequence Preview</h2>
        <p class="muted">Present the one-action workflow cleanly now while leaving room for optional multi-step sequencing later.</p>
        <table class="table">
          <thead><tr><th>Step</th><th>Planned sequence</th></tr></thead>
          <tbody>{sequence_preview}</tbody>
        </table>
      </div>
    </div>
    <div class="grid two" style="margin-top:18px;">
      <div class="card">
        <h3>Browser Playbook</h3>
        <p class="muted">OpenClaw should follow the generated handoff artifact exactly.</p>
        <table class="table">
          <thead><tr><th>Kind</th><th>Target</th><th>Purpose</th></tr></thead>
          <tbody>{playbook_steps or '<tr><td colspan="3">Run planning to generate the browser playbook.</td></tr>'}</tbody>
        </table>
      </div>
      <div class="card">
        <h3>Operator Actions</h3>
        <ul class="list">
          <li>Chosen remediation: <strong id="execution-current-action">{escape(_action_label(chosen))}</strong>{f" <code>{escape(str(chosen))}</code>" if chosen else ""}</li>
          <li>Last action in shared state: <strong id="execution-last-action">{escape(_action_label(state.get('last_action')))}</strong></li>
          <li>Retry factor: <code id="execution-retry-factor">{business_metrics['retry_amplification_factor']}x</code></li>
          <li>Booking completion: <code id="execution-booking-completion">{business_metrics['payment_success_rate']:.2f}</code></li>
          <li>Plan from the console or by command line.</li>
          <li>OpenClaw execution: <a href="/openclaw-execution">/openclaw-execution</a></li>
          <li>Manual ops controls: <a href="{urls['feature_flags']}">{urls['feature_flags']}</a></li>
          <li>Verify waits one scrape interval before writing the verdict.</li>
          <li>Current scenario: <strong>{escape(active_scenario['label'])}</strong></li>
        </ul>
      </div>
    </div>
    """


def _render_verdict_view(
    report: dict | None,
    plan: dict | None,
    state: dict,
    technical_metrics: dict,
    business_metrics: dict,
) -> str:
    if not report:
        return f"""
        <div class="card">
          <h2>Verdict View</h2>
          <p class="muted">No verification report is available yet.</p>
          <p>Run verify here after the browser action to populate the final verdict.</p>
          <form class="inline" method="post" action="/api/verify" style="margin-top:12px;" data-loading-label="Verifying outcome..." data-loading-phase="verify">
            <input type="hidden" name="input_path" value="{escape(str(DEFAULT_ALERT_PATH))}">
            <input type="hidden" name="return_to" value="verdict">
            <button class="primary">Run Verify</button>
            <a class="button" href="/?view=execution">Back to Execution</a>
          </form>
        </div>
        """

    actual = report.get("actual_outcome", {})
    predicted = report.get("predicted_vs_actual", {}).get("predicted", {})
    drift = report.get("predicted_vs_actual", {}).get("drift", {"score": 0.0})
    notes = "".join(f"<li>{escape(note)}</li>" for note in report.get("notes", []))
    planned_action = report.get("best_action", {}).get("id", "n/a")
    executed_action = report.get("executed_action", {}).get("id", planned_action)
    action_alignment = report.get("action_alignment", {})
    rejected_action = _dangerous_reflex_from_plan(plan, planned_action)
    chosen_rationale = _chosen_rationale(report)
    alignment_note = ""
    if action_alignment and not action_alignment.get("matches_plan", True):
        alignment_note = (
            f'<p class="callout"><strong>Execution mismatch</strong><br>'
            f'The browser executed <code>{escape(executed_action)}</code> while the saved plan chose '
            f'<code>{escape(planned_action)}</code>. The verdict below is anchored to the executed action.</p>'
        )
    verdict_cards = _metric_cards(
        [
            ("Drift Score", f"{drift['score']:.2f}"),
            ("Retry Risk", escape(str(actual.get("retry_storm_risk", "n/a")))),
            ("Blast Radius", escape(str(actual.get("blast_radius", "n/a")))),
        ]
    )
    before_metrics = plan.get("observed_condition", {}).get("metrics", {}) if plan else {}
    return f"""
    <div class="grid three">
      {verdict_cards}
    </div>
    <div class="grid two" style="margin-top:18px;">
      <div class="card">
        <h2>Verdict View</h2>
        <p class="muted">This view turns Stratus output and post-action evidence into the judge-facing story.</p>
        {alignment_note}
        <ul class="list">
          <li>Guardrail choice: <strong>{escape(_action_label(planned_action))}</strong> <code>{escape(str(planned_action))}</code></li>
          <li>Executed action: <strong>{escape(_action_label(executed_action))}</strong> <code>{escape(str(executed_action))}</code></li>
          <li>Rejected dangerous reflex: <strong>{escape(_action_label(rejected_action))}</strong> <code>{escape(rejected_action)}</code></li>
          <li>Why the safer action won: {escape(chosen_rationale)}</li>
          <li>Before latency: <code>{escape(str(before_metrics.get('latency_p95_ms', 'n/a')))}</code></li>
          <li>After latency: <code>{escape(str(actual.get('latency_p95_ms', 'n/a')))}</code></li>
          <li>Scheduling abandonment now: <code>{business_metrics['queue_abandonment_rate']:.2f}</code></li>
          <li>Case writeback status: <code>planned for Evaluation Agent</code></li>
        </ul>
        <form class="inline" method="post" action="/api/verify" style="margin-top:18px;" data-loading-label="Refreshing verdict..." data-loading-phase="verify">
          <input type="hidden" name="input_path" value="{escape(str(DEFAULT_ALERT_PATH))}">
          <input type="hidden" name="return_to" value="verdict">
          <button class="primary">Refresh Verdict</button>
          <a class="button" href="/?view=incident">Start Fresh</a>
        </form>
      </div>
      <div class="card">
        <h2>Predicted vs Actual</h2>
        <table class="table">
          <thead><tr><th>Field</th><th>Predicted</th><th>Actual</th></tr></thead>
          <tbody>
            <tr><td>Latency</td><td><code>{escape(str(predicted.get('latency_p95_ms', predicted.get('latency_direction', 'n/a'))))}</code></td><td><code>{escape(str(actual.get('latency_p95_ms', actual.get('latency_direction', 'n/a'))))}</code></td></tr>
            <tr><td>Error rate</td><td><code>{escape(str(predicted.get('error_rate', predicted.get('error_direction', 'n/a'))))}</code></td><td><code>{escape(str(actual.get('error_rate', actual.get('error_direction', 'n/a'))))}</code></td></tr>
            <tr><td>Retry</td><td><code>{escape(str(predicted.get('retry_rate', predicted.get('retry_storm_risk', 'n/a'))))}</code></td><td><code>{escape(str(actual.get('retry_rate', actual.get('retry_storm_risk', 'n/a'))))}</code></td></tr>
          </tbody>
        </table>
        <div style="margin-top:16px;">
          <strong>Guardrail verdict</strong>
          <ul class="list">
            <li>Dangerous local reflex was rejected before execution.</li>
            <li>OpenClaw executed the safer single action from the browser playbook.</li>
            <li>Prometheus-backed verification wrote the final verdict after the soak period.</li>
          </ul>
        </div>
        <div style="margin-top:16px;">
          <strong>Guardrail notes</strong>
          <ul class="list">{notes or '<li>No additional notes were recorded.</li>'}</ul>
        </div>
      </div>
    </div>
    """


def _render_operations_dashboard(
    state: dict,
    technical_metrics: dict,
    business_metrics: dict,
    constraints: dict,
    active_scenario: dict,
    scenarios: list[dict],
    plan: dict | None,
    report: dict | None,
    watch_state: dict,
) -> str:
    action_labels_json = json.dumps(ACTION_LABELS)
    action_summaries_json = json.dumps(ACTION_SUMMARIES)
    care_lines_json = json.dumps(
        [
            ["Primary Care Virtual", "08:30", "10:00", "11:30", "14:00", "16:00"],
            ["Behavioral Health", "09:00", "10:30", "13:00", "15:30", "18:00"],
            ["Cardiology Follow-up", "08:45", "11:15", "12:45", "15:15", "17:30"],
            ["Urgent Telehealth", "Now", "09:30", "12:00", "14:30", "17:00"],
        ]
    )
    scenario_options = "".join(
        f'<option value="{escape(item["id"])}"{" selected" if item["id"] == active_scenario["id"] else ""}>{escape(item["label"])}</option>'
        for item in scenarios
    )
    chosen_action = (plan or {}).get("best_action", {}).get("id")
    executed_action = (report or {}).get("executed_action", {}).get("id") or state.get("last_action")
    system_state = _ops_system_state(technical_metrics, business_metrics)
    demand_index = min(
        100,
        int(45 + business_metrics["retry_amplification_factor"] * 10 + business_metrics["queue_abandonment_rate"] * 90),
    )
    backlog_index = min(
        100,
        int(business_metrics["queue_abandonment_rate"] * 120 + business_metrics["seat_hold_utilization"] * 48),
    )
    provider_grid = _render_provider_grid(
        business_metrics["seat_hold_utilization"],
        business_metrics["seat_hold_expiration_rate"],
        state.get("payment_feature_disabled", False),
    )
    status_cards = "".join(
        [
            _ops_metric_card(
                "Portal demand",
                f"{demand_index} / 100",
                _ops_tone(demand_index / 100, 0.55, 0.78),
                "Color-coded from surge pressure and retry amplification.",
                card_id="ops-card-portal-demand",
                value_id="ops-portal-demand-value",
            ),
            _ops_metric_card(
                "Eligibility health",
                _eligibility_status(state),
                "critical" if state["payment_service_unreachable"] and not state["payment_circuit_breaker_enabled"] else "watch" if state["payment_circuit_breaker_enabled"] else "stable",
                "Shows whether verification is degraded, protected, or recovered.",
                card_id="ops-card-eligibility-health",
                value_id="ops-eligibility-health-value",
            ),
            _ops_metric_card(
                "Booking completion",
                f"{business_metrics['payment_success_rate']:.0%}",
                _ops_tone(1 - business_metrics["payment_success_rate"], 0.18, 0.28, inverse=True),
                "Tracks how many patient booking attempts successfully complete.",
                card_id="ops-card-booking-completion",
                value_id="ops-booking-completion-value",
            ),
            _ops_metric_card(
                "Scheduling abandonment",
                f"{business_metrics['queue_abandonment_rate']:.0%}",
                _ops_tone(business_metrics["queue_abandonment_rate"], 0.14, 0.24),
                "Measures patient drop-off during active access pressure.",
                card_id="ops-card-scheduling-abandonment",
                value_id="ops-scheduling-abandonment-value",
            ),
            _ops_metric_card(
                "Slot hold pressure",
                f"{business_metrics['seat_hold_utilization']:.0%}",
                _ops_tone(business_metrics["seat_hold_utilization"], 0.70, 0.84),
                "High values mean inventory is getting trapped in pending holds.",
                card_id="ops-card-slot-hold-pressure",
                value_id="ops-slot-hold-pressure-value",
            ),
            _ops_metric_card(
                "Retry amplification",
                f"{business_metrics['retry_amplification_factor']}x",
                _ops_tone((business_metrics["retry_amplification_factor"] - 1.0) / 4.0, 0.35, 0.62),
                "Shows how strongly the system is self-amplifying load.",
                card_id="ops-card-retry-amplification",
                value_id="ops-retry-amplification-value",
            ),
        ]
    )
    flow_bars = "".join(
        [
            _ops_progress_bar("Patient portal surge", demand_index / 100, _ops_tone(demand_index / 100, 0.55, 0.78), row_id="ops-flow-portal-demand", bar_id="ops-flow-portal-demand-bar", value_id="ops-flow-portal-demand-value"),
            _ops_progress_bar("Eligibility delay", min(1.0, technical_metrics["latency_p95_ms"] / 2600), _ops_tone(technical_metrics["latency_p95_ms"] / 2600, 0.58, 0.82), row_id="ops-flow-eligibility-delay", bar_id="ops-flow-eligibility-delay-bar", value_id="ops-flow-eligibility-delay-value"),
            _ops_progress_bar("Backlog pressure", backlog_index / 100, _ops_tone(backlog_index / 100, 0.48, 0.72), row_id="ops-flow-backlog-pressure", bar_id="ops-flow-backlog-pressure-bar", value_id="ops-flow-backlog-pressure-value"),
            _ops_progress_bar("Booking recovery", business_metrics["payment_success_rate"], _ops_tone(1 - business_metrics["payment_success_rate"], 0.18, 0.28, inverse=True), row_id="ops-flow-booking-recovery", bar_id="ops-flow-booking-recovery-bar", value_id="ops-flow-booking-recovery-value"),
        ]
    )
    simulation_cards = "".join(
        [
            _ops_metric_card(
                "External arrivals",
                f"{constraints['lambda_external']:.2f}/s",
                _ops_tone(min(1.0, constraints["lambda_external"] / 8.5), 0.55, 0.82),
                "Base demand entering the portal before retries or gating.",
                card_id="ops-card-external-arrivals",
                value_id="ops-external-arrivals-value",
            ),
            _ops_metric_card(
                "Retry arrivals",
                f"{constraints['lambda_retry']:.2f}/s",
                _ops_tone(min(1.0, constraints["lambda_retry"] / 4.5), 0.42, 0.72),
                "Self-amplified load generated by repeated booking attempts.",
                card_id="ops-card-retry-arrivals",
                value_id="ops-retry-arrivals-value",
            ),
            _ops_metric_card(
                "Queue depth",
                f"{constraints['queue_depth']:.0f}",
                _ops_tone(min(1.0, constraints["queue_depth"] / 45.0), 0.48, 0.75),
                "Backlog proxy inside the scheduling and eligibility path.",
                card_id="ops-card-queue-depth",
                value_id="ops-queue-depth-value",
            ),
            _ops_metric_card(
                "Service rate",
                f"{constraints['effective_service_rate']:.2f}/s",
                _ops_tone(1 - min(1.0, constraints["effective_service_rate"] / 6.0), 0.36, 0.58, inverse=True),
                "Effective throughput after degradation, overload, and action effects.",
                card_id="ops-card-service-rate",
                value_id="ops-service-rate-value",
            ),
            _ops_metric_card(
                "Secondary headroom",
                f"{constraints['regional_headroom_secondary']:.2f}",
                _ops_tone(1 - constraints["regional_headroom_secondary"], 0.55, 0.75, inverse=True),
                "This is the threshold-sensitive term that can make traffic shift safe or dangerous.",
                card_id="ops-card-secondary-headroom",
                value_id="ops-secondary-headroom-value",
            ),
            _ops_metric_card(
                "Fairness pressure",
                f"{constraints['fairness_pressure']:.2f}",
                _ops_tone(min(1.0, constraints["fairness_pressure"] / 0.30), 0.45, 0.72),
                "Tracks when online access starts starving patients unevenly.",
                card_id="ops-card-fairness-pressure",
                value_id="ops-fairness-pressure-value",
            ),
        ]
    )
    incidents = _ops_incident_feed(state, technical_metrics, business_metrics, active_scenario)
    recommendation = ACTION_SUMMARIES.get(chosen_action or "", "Generate a guardrail plan to surface the recommended remediation.")
    verdict_summary = (
        f"Last verified action: {_action_label(executed_action)}"
        if executed_action
        else "No remediation has been executed yet."
    )
    case_cards = "".join(
        f"""
        <div class="case-card">
          <strong>{escape(item['label'])}</strong>
          <div class="muted" style="margin-bottom:10px;">{escape(item['headline'])}</div>
          <form method="post" action="/api/trigger-incident">
            <input type="hidden" name="scenario_id" value="{escape(item['id'])}">
            <input type="hidden" name="return_to" value="operations">
            <button class="primary">{escape(item.get('trigger_label', 'Trigger This Case'))}</button>
          </form>
        </div>
        """
        for item in scenarios
    )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Access Operations Board</title>
  <style>
    :root {{
      --bg:#f3f7f8;
      --ink:#10222d;
      --muted:#5f6e75;
      --line:#d9e5e8;
      --panel:#ffffff;
      --mint:#d8f0ea;
      --mint-ink:#0c6c57;
      --amber:#f6e3c2;
      --amber-ink:#9a5a14;
      --coral:#f7d8d6;
      --coral-ink:#a23f35;
      --slate:#e8eff2;
      --teal:#0f7a78;
      --blue:#1f5e96;
      --navy:#13354a;
      --shadow:0 22px 56px rgba(16,34,45,0.08);
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0;
      color:var(--ink);
      font-family:"Avenir Next","Helvetica Neue",sans-serif;
      background:
        radial-gradient(circle at top right, rgba(31,94,150,0.08), transparent 28%),
        linear-gradient(180deg, #fbfdff 0%, var(--bg) 100%);
    }}
    .wrap {{ max-width:1340px; margin:0 auto; padding:28px 28px 48px; }}
    .hero {{
      background:linear-gradient(135deg, rgba(19,53,74,0.97), rgba(15,122,120,0.94));
      color:#f5fbfc;
      border-radius:28px;
      padding:26px 28px;
      box-shadow:var(--shadow);
      position:relative;
      overflow:hidden;
    }}
    .hero:after {{
      content:"";
      position:absolute;
      inset:auto -100px -120px auto;
      width:280px;
      height:280px;
      border-radius:999px;
      background:radial-gradient(circle, rgba(255,255,255,0.12), transparent 62%);
    }}
    .eyebrow {{ letter-spacing:0.14em; text-transform:uppercase; font-size:0.78rem; opacity:0.82; }}
    .hero h1 {{ margin:8px 0 8px; font-size:2.5rem; line-height:1.02; font-weight:700; }}
    .hero p {{ margin:0; max-width:840px; color:rgba(245,251,252,0.84); font-size:1.02rem; }}
    .hero-top {{ display:flex; justify-content:space-between; gap:22px; align-items:flex-start; }}
    .hero-links {{ display:flex; gap:10px; flex-wrap:wrap; }}
    .hero-links a {{
      text-decoration:none; color:#f5fbfc; border:1px solid rgba(255,255,255,0.18);
      background:rgba(255,255,255,0.08); padding:10px 14px; border-radius:999px;
    }}
    .hero-pills {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:18px; }}
    .pill {{
      display:inline-flex; align-items:center; gap:8px; padding:10px 14px; border-radius:999px;
      background:rgba(255,255,255,0.10); color:#f6fbfc; font-size:0.94rem;
    }}
    .layout {{ display:grid; grid-template-columns:1.48fr 0.92fr; gap:18px; margin-top:18px; }}
    .stack {{ display:grid; gap:18px; }}
    .card {{
      background:var(--panel);
      border:1px solid var(--line);
      border-radius:24px;
      padding:22px;
      box-shadow:var(--shadow);
    }}
    .card h2, .card h3 {{ margin-top:0; }}
    .muted {{ color:var(--muted); }}
    .kpi-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }}
    .kpi {{
      border:1px solid var(--line); border-radius:20px; padding:16px;
      background:linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
    }}
    .kpi .label {{ color:var(--muted); font-size:0.92rem; }}
    .kpi .value {{ margin-top:8px; font-size:1.8rem; font-weight:700; }}
    .kpi .note {{ margin-top:8px; color:var(--muted); font-size:0.88rem; line-height:1.45; }}
    .tone-stable {{ background:linear-gradient(180deg, #ffffff 0%, #f3fbf8 100%); }}
    .tone-watch {{ background:linear-gradient(180deg, #ffffff 0%, #fff8ef 100%); }}
    .tone-critical {{ background:linear-gradient(180deg, #ffffff 0%, #fff3f2 100%); }}
    .badge {{
      display:inline-flex; align-items:center; gap:8px; border-radius:999px; padding:8px 12px; font-size:0.88rem;
      font-weight:600;
    }}
    .badge.stable {{ background:var(--mint); color:var(--mint-ink); }}
    .badge.watch {{ background:var(--amber); color:var(--amber-ink); }}
    .badge.critical {{ background:var(--coral); color:var(--coral-ink); }}
    .section-head {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:14px; }}
    .flow-grid {{ display:grid; gap:12px; }}
    .flow-row {{ display:grid; grid-template-columns:180px 1fr 70px; gap:12px; align-items:center; }}
    .track {{ height:12px; border-radius:999px; background:#e8eff2; overflow:hidden; }}
    .bar {{ height:100%; border-radius:999px; }}
    .bar.stable {{ background:linear-gradient(90deg, #21a189, #5dc7ac); }}
    .bar.watch {{ background:linear-gradient(90deg, #d48f30, #f0bf69); }}
    .bar.critical {{ background:linear-gradient(90deg, #ca5a55, #ef9a8c); }}
    .ops-grid {{ display:grid; grid-template-columns:1.1fr 0.9fr; gap:16px; }}
    .schedule-grid {{ display:grid; gap:10px; }}
    .schedule-header, .schedule-row {{ display:grid; grid-template-columns:190px repeat(5, 1fr); gap:10px; align-items:center; }}
    .schedule-header div {{ color:var(--muted); font-size:0.9rem; }}
    .schedule-service {{ font-weight:600; color:var(--navy); }}
    .slot {{
      min-height:52px; border-radius:16px; padding:10px 10px; border:1px solid var(--line);
      display:flex; flex-direction:column; justify-content:center; gap:4px;
    }}
    .slot strong {{ font-size:0.9rem; }}
    .slot span {{ font-size:0.82rem; color:var(--muted); }}
    .slot.available {{ background:#f1faf8; }}
    .slot.at-risk {{ background:#fff7ec; }}
    .slot.held {{ background:#fff2ef; }}
    .slot.manual {{ background:#eef3f7; }}
    .feed {{ display:grid; gap:10px; }}
    .feed-item {{
      border:1px solid var(--line); border-radius:18px; padding:14px 16px; background:linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
    }}
    .feed-item strong {{ display:block; margin-bottom:4px; }}
    .case-grid {{ display:grid; gap:12px; }}
    .case-card {{
      border:1px solid var(--line); border-radius:18px; padding:14px 16px; background:linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
    }}
    .case-card strong {{ display:block; margin-bottom:4px; }}
    .side-stack {{ display:grid; gap:14px; }}
    .recommend {{
      background:linear-gradient(180deg, rgba(15,122,120,0.07) 0%, rgba(31,94,150,0.04) 100%);
      border:1px solid #cfe1e7;
      border-radius:20px;
      padding:18px;
    }}
    form.inline {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
    select, button {{
      font:inherit; border-radius:12px; padding:10px 12px; border:1px solid var(--line); background:white;
    }}
    button.primary {{ background:var(--teal); color:white; border-color:var(--teal); cursor:pointer; }}
    .link-row {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:14px; }}
    .link-row a {{
      text-decoration:none; color:var(--blue); padding:10px 12px; border-radius:12px; background:#edf5fb; border:1px solid #d7e8f7;
    }}
    .summary-list {{ margin:0; padding-left:18px; line-height:1.7; }}
    @media (max-width: 1080px) {{
      .layout, .ops-grid, .kpi-grid {{ grid-template-columns:1fr; }}
      .hero-top {{ display:grid; }}
      .schedule-header, .schedule-row {{ grid-template-columns:1fr; }}
      .flow-row {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="hero-top">
        <div>
          <div class="eyebrow">Regional Virtual Care Access Operations</div>
          <h1>Telehealth Scheduling Stability Board</h1>
          <p>A live operational view of patient-portal demand, scheduling throughput, slot inventory, and eligibility verification health. Use this page to make the incident feel like a real access system under stress; use the Guardrail Console separately for OpenClaw and decision execution.</p>
        </div>
        <div class="hero-links">
          <a href="/">Guardrail Console</a>
          <a href="/openclaw-execution">OpenClaw Execution</a>
          <a href="/feature-flags">Manual Ops Controls</a>
        </div>
      </div>
      <div class="hero-pills">
        <span class="pill" id="ops-pill-scenario">Scenario: {escape(active_scenario['label'])}</span>
        <span class="pill" id="ops-pill-state">System state: {escape(system_state.title())}</span>
        <span class="pill" id="ops-pill-watch">OpenClaw: {escape(_watch_label(watch_state))}</span>
        <span class="pill" id="ops-pill-recommended">Recommended remediation: {escape(_action_label(chosen_action)) if chosen_action else 'Generate guardrail plan'}</span>
        <span class="pill" id="ops-pill-executed">Last executed: {escape(_action_label(executed_action))}</span>
      </div>
    </section>

    <div class="layout">
      <div class="stack">
        <section class="card">
          <div class="section-head">
            <div>
              <h2>Access Health Overview</h2>
              <p class="muted">Color-coded operational indicators for demand, scheduling quality, and downstream verification stability.</p>
            </div>
            <span class="badge {escape(system_state)}" id="ops-system-badge">{escape(system_state.title())} access posture</span>
          </div>
          <div class="kpi-grid">
            {status_cards}
          </div>
        </section>

        <section class="card">
          <div class="section-head">
            <div>
              <h2>Flow Pressure</h2>
              <p class="muted">Live-looking pressure bars to make surge, backlog, and recovery visually obvious during the demo.</p>
            </div>
          </div>
          <div class="flow-grid">
            {flow_bars}
          </div>
        </section>

        <section class="card">
          <div class="section-head">
            <div>
              <h2>Simulation Diagnostics</h2>
              <p class="muted">Underlying queue-and-constraint terms driving the telehealth incident. This is the causal state the guardrail is reasoning over, not just the final KPIs.</p>
            </div>
            <span class="badge {escape(_ops_tone(min(1.0, constraints['time_sec'] / 180), 0.4, 0.75))}" id="ops-time-badge">t = {constraints['time_sec']}s</span>
          </div>
          <div class="kpi-grid">
            {simulation_cards}
          </div>
        </section>

        <section class="card">
          <div class="section-head">
            <div>
              <h2>Scheduling Board</h2>
              <p class="muted">A realistic scheduling-grid view of virtual-care inventory. Warm colors indicate rising hold pressure and patient-access risk.</p>
            </div>
            <div class="badge {escape(_ops_tone(business_metrics['seat_hold_utilization'], 0.70, 0.84))}" id="ops-slot-pressure-badge">Slot pressure {business_metrics['seat_hold_utilization']:.0%}</div>
          </div>
          <div id="ops-provider-grid">{provider_grid}</div>
        </section>
      </div>

      <div class="side-stack">
        <section class="card">
          <div class="section-head">
            <div>
              <h2>Scenario Control</h2>
              <p class="muted">Switch the active healthcare-access incident without changing the underlying workflow mechanics.</p>
            </div>
          </div>
          <form class="inline" method="post" action="/api/scenario">
            <input type="hidden" name="return_to" value="operations">
            <select id="scenario_id" name="scenario_id">{scenario_options}</select>
            <button class="primary">Apply Scenario</button>
            <button formaction="/api/reset" name="return_to" value="operations">Reset Current Scenario Baseline</button>
            <button formaction="/api/disarm-watch" name="return_to" value="operations">Disarm Watcher</button>
          </form>
          <p class="muted" style="margin:12px 0 0;">{escape(_scenario_reset_copy(active_scenario))}</p>
          <form class="inline" method="post" action="/api/reset" style="margin-top:12px;">
            <input type="hidden" name="scenario_id" value="retry_death_spiral">
            <input type="hidden" name="return_to" value="operations">
            <button>Reset to Healthy Baseline</button>
          </form>
          <form class="inline" method="post" action="/api/trigger-incident" style="margin-top:12px;">
            <input type="hidden" name="scenario_id" value="{escape(active_scenario['id'])}">
            <input type="hidden" name="return_to" value="operations">
            <button class="primary">{escape(active_scenario.get('trigger_label', 'Trigger Current Incident'))}</button>
          </form>
          <div class="link-row">
            <a href="/">Open Guardrail Console</a>
            <a href="/openclaw-execution">Open Execution Surface</a>
          </div>
          <ul class="summary-list" style="margin-top:14px;">
            <li>Watcher status: <strong>{escape(_watch_label(watch_state))}</strong></li>
            <li>Arm command: <code>.venv/bin/python run.py alerts/latest.json --phase await-demo</code></li>
            <li>Reset Scenario keeps the watcher armed. Use <strong>Disarm Watcher</strong> only when you want OpenClaw to stop waiting.</li>
          </ul>
        </section>

        <section class="recommend">
          <div class="section-head">
            <div>
              <h2 style="margin-bottom:6px;">Guardrail Recommendation</h2>
              <p class="muted" style="margin:0;">This keeps the business board and the OpenClaw control plane tied together.</p>
            </div>
            <span class="badge {escape(_ops_tone((technical_metrics['latency_p95_ms'] - 900) / 1400, 0.45, 0.68))}">Stratus-backed</span>
          </div>
          <div style="font-size:1.5rem; font-weight:700; color:var(--navy);" id="ops-recommendation-action">{escape(_action_label(chosen_action)) if chosen_action else 'Plan not generated yet'}</div>
          <p class="muted" style="margin:10px 0 0;" id="ops-recommendation-summary">{escape(recommendation)}</p>
          <ul class="summary-list" style="margin-top:14px;">
            <li>Dangerous reflex: <strong id="ops-dangerous-reflex">{escape(_action_label('restart_payment'))}</strong></li>
            <li id="ops-verdict-summary">{escape(verdict_summary)}</li>
            <li>Primary operator surface remains the Guardrail Console.</li>
          </ul>
        </section>

        <section class="card">
          <div class="section-head">
            <div>
              <h2>Incident Feed</h2>
              <p class="muted">A professional incident narrative for judges and operators.</p>
            </div>
          </div>
          <div class="feed">
            {incidents}
          </div>
        </section>

        <section class="card">
          <div class="section-head">
            <div>
              <h2>Prepared Incident Cases</h2>
              <p class="muted">Use these buttons instead of `curl` to trigger different webhook incidents during the demo.</p>
            </div>
          </div>
          <div class="case-grid">
            {case_cards}
          </div>
        </section>
      </div>
    </div>
  </div>
  <script>
    (() => {{
      const actionLabels = {action_labels_json};
      const actionSummaries = {action_summaries_json};
      const careLines = {care_lines_json};
      function actionLabel(actionId) {{
        if (!actionId) return 'none';
        return actionLabels[actionId] || actionId;
      }}
      function watchLabel(watchState) {{
        if (!watchState) return 'Idle';
        const status = watchState.status || 'idle';
        if (status === 'pending' && watchState.pending_incident) return 'Pending incident latched';
        if (status === 'acknowledged' && watchState.active_incident) return 'Investigating active incident';
        if (watchState.armed) return 'Armed and waiting';
        return 'Idle';
      }}
      function opsTone(value, watch, critical, inverse = false) {{
        const score = inverse ? 1 - value : value;
        if (score >= critical) return 'critical';
        if (score >= watch) return 'watch';
        return 'stable';
      }}
      function opsSystemState(metrics, business) {{
        const riskScore = Math.max(
          (Number(metrics.latency_p95_ms || 0) - 900) / 1400,
          Number(business.queue_abandonment_rate || 0) / 0.30,
          (Number(business.retry_amplification_factor || 1) - 1.0) / 2.5
        );
        return opsTone(riskScore, 0.55, 0.82);
      }}
      function eligibilityStatus(state) {{
        if (state.payment_feature_disabled) return 'Online scheduling disabled';
        if (state.payment_service_unreachable && state.payment_circuit_breaker_enabled) return 'Protected by circuit breaker';
        if (state.payment_service_unreachable) return 'Degraded';
        return 'Healthy';
      }}
      function setText(id, value) {{
        const node = document.getElementById(id);
        if (node && value !== undefined && value !== null) node.textContent = value;
      }}
      function setTone(cardId, tone) {{
        const node = document.getElementById(cardId);
        if (!node) return;
        node.classList.remove('tone-stable', 'tone-watch', 'tone-critical');
        node.classList.add(`tone-${{tone}}`);
      }}
      function setBadge(id, tone, label) {{
        const node = document.getElementById(id);
        if (!node) return;
        node.classList.remove('stable', 'watch', 'critical');
        node.classList.add(tone);
        node.textContent = label;
      }}
      function setBar(barId, valueId, pct, tone) {{
        const bar = document.getElementById(barId);
        const value = document.getElementById(valueId);
        if (bar) {{
          bar.classList.remove('stable', 'watch', 'critical');
          bar.classList.add(tone);
          bar.style.width = `${{pct}}%`;
        }}
        if (value) value.textContent = `${{pct}}%`;
      }}
      function renderProviderGrid(slotHoldUtilization, slotHoldExpiration, onlineDisabled) {{
        const pressure = slotHoldUtilization + slotHoldExpiration * 0.35;
        const header = `
          <div class="schedule-header">
            <div>Care line</div>
            <div>Window 1</div>
            <div>Window 2</div>
            <div>Window 3</div>
            <div>Window 4</div>
            <div>Window 5</div>
          </div>
        `;
        const rows = careLines.map((row, idx) => {{
          const [name, ...times] = row;
          const cells = times.map((timeLabel, tIndex) => {{
            const phase = (pressure * 10 + idx + tIndex) % 5;
            let cls = 'available';
            let title = 'Bookable';
            let sub = 'Normal access window';
            if (onlineDisabled) {{
              cls = 'manual';
              title = 'Manual fallback';
              sub = 'Routed to staff';
            }} else if (phase >= 4.1 || pressure > 1.0) {{
              cls = 'held';
              title = 'At-risk hold';
              sub = 'Awaiting verification';
            }} else if (phase >= 2.4 || pressure > 0.82) {{
              cls = 'at-risk';
              title = 'Limited capacity';
              sub = 'Longer confirmation path';
            }}
            return `<div class="slot ${{cls}}"><strong>${{title}}</strong><span>${{timeLabel}} • ${{sub}}</span></div>`;
          }}).join('');
          return `<div class="schedule-row"><div class="schedule-service">${{name}}</div>${{cells}}</div>`;
        }}).join('');
        return `<div class="schedule-grid">${{header}}${{rows}}</div>`;
      }}
      async function refreshOperations() {{
        try {{
          const response = await fetch('/api/state', {{ cache: 'no-store' }});
          if (!response.ok) return;
          const data = await response.json();
          const metrics = data.metrics || {{}};
          const business = data.business_metrics || {{}};
          const constraints = data.constraints || {{}};
          const state = data.state || {{}};
          const scenario = data.scenario || {{}};
          const plan = data.plan || {{}};
          const report = data.report || {{}};
          const chosenAction = (plan.best_action || {{}}).id;
          const executedAction = ((report.executed_action || {{}}).id) || state.last_action;
          const systemState = opsSystemState(metrics, business);
          const demandIndex = Math.min(100, Math.round(45 + Number(business.retry_amplification_factor || 0) * 10 + Number(business.queue_abandonment_rate || 0) * 90));
          const backlogIndex = Math.min(100, Math.round(Number(business.queue_abandonment_rate || 0) * 120 + Number(business.seat_hold_utilization || 0) * 48));

          setText('ops-pill-scenario', `Scenario: ${{scenario.label || 'Unknown'}}`);
          setText('ops-pill-state', `System state: ${{systemState.charAt(0).toUpperCase() + systemState.slice(1)}}`);
          setText('ops-pill-watch', `OpenClaw: ${{watchLabel(data.watch_state)}}`);
          setText('ops-pill-recommended', `Recommended remediation: ${{chosenAction ? actionLabel(chosenAction) : 'Generate guardrail plan'}}`);
          setText('ops-pill-executed', `Last executed: ${{actionLabel(executedAction)}}`);
          setBadge('ops-system-badge', systemState, `${{systemState.charAt(0).toUpperCase() + systemState.slice(1)}} access posture`);
          setBadge('ops-time-badge', opsTone(Math.min(1.0, Number(constraints.time_sec || 0) / 180), 0.4, 0.75), `t = ${{Math.round(Number(constraints.time_sec || 0))}}s`);
          setBadge('ops-slot-pressure-badge', opsTone(Number(business.seat_hold_utilization || 0), 0.70, 0.84), `Slot pressure ${{Math.round(Number(business.seat_hold_utilization || 0) * 100)}}%`);

          setText('ops-portal-demand-value', `${{demandIndex}} / 100`);
          setTone('ops-card-portal-demand', opsTone(demandIndex / 100, 0.55, 0.78));
          setText('ops-eligibility-health-value', eligibilityStatus(state));
          const eligibilityTone = state.payment_service_unreachable && !state.payment_circuit_breaker_enabled ? 'critical' : state.payment_circuit_breaker_enabled ? 'watch' : 'stable';
          setTone('ops-card-eligibility-health', eligibilityTone);
          setText('ops-booking-completion-value', `${{Math.round(Number(business.payment_success_rate || 0) * 100)}}%`);
          setTone('ops-card-booking-completion', opsTone(1 - Number(business.payment_success_rate || 0), 0.18, 0.28, true));
          setText('ops-scheduling-abandonment-value', `${{Math.round(Number(business.queue_abandonment_rate || 0) * 100)}}%`);
          setTone('ops-card-scheduling-abandonment', opsTone(Number(business.queue_abandonment_rate || 0), 0.14, 0.24));
          setText('ops-slot-hold-pressure-value', `${{Math.round(Number(business.seat_hold_utilization || 0) * 100)}}%`);
          setTone('ops-card-slot-hold-pressure', opsTone(Number(business.seat_hold_utilization || 0), 0.70, 0.84));
          setText('ops-retry-amplification-value', `${{Number(business.retry_amplification_factor || 0).toFixed(1)}}x`);
          setTone('ops-card-retry-amplification', opsTone((Number(business.retry_amplification_factor || 1) - 1.0) / 4.0, 0.35, 0.62));

          setBar('ops-flow-portal-demand-bar', 'ops-flow-portal-demand-value', Math.max(6, Math.min(100, demandIndex)), opsTone(demandIndex / 100, 0.55, 0.78));
          setBar('ops-flow-eligibility-delay-bar', 'ops-flow-eligibility-delay-value', Math.max(6, Math.min(100, Math.round(Math.min(1.0, Number(metrics.latency_p95_ms || 0) / 2600) * 100))), opsTone(Number(metrics.latency_p95_ms || 0) / 2600, 0.58, 0.82));
          setBar('ops-flow-backlog-pressure-bar', 'ops-flow-backlog-pressure-value', Math.max(6, Math.min(100, backlogIndex)), opsTone(backlogIndex / 100, 0.48, 0.72));
          setBar('ops-flow-booking-recovery-bar', 'ops-flow-booking-recovery-value', Math.max(6, Math.min(100, Math.round(Number(business.payment_success_rate || 0) * 100))), opsTone(1 - Number(business.payment_success_rate || 0), 0.18, 0.28, true));

          setText('ops-external-arrivals-value', `${{Number(constraints.lambda_external || 0).toFixed(2)}}/s`);
          setTone('ops-card-external-arrivals', opsTone(Math.min(1.0, Number(constraints.lambda_external || 0) / 8.5), 0.55, 0.82));
          setText('ops-retry-arrivals-value', `${{Number(constraints.lambda_retry || 0).toFixed(2)}}/s`);
          setTone('ops-card-retry-arrivals', opsTone(Math.min(1.0, Number(constraints.lambda_retry || 0) / 4.5), 0.42, 0.72));
          setText('ops-queue-depth-value', `${{Math.round(Number(constraints.queue_depth || 0))}}`);
          setTone('ops-card-queue-depth', opsTone(Math.min(1.0, Number(constraints.queue_depth || 0) / 45.0), 0.48, 0.75));
          setText('ops-service-rate-value', `${{Number(constraints.effective_service_rate || 0).toFixed(2)}}/s`);
          setTone('ops-card-service-rate', opsTone(1 - Math.min(1.0, Number(constraints.effective_service_rate || 0) / 6.0), 0.36, 0.58, true));
          setText('ops-secondary-headroom-value', Number(constraints.regional_headroom_secondary || 0).toFixed(2));
          setTone('ops-card-secondary-headroom', opsTone(1 - Number(constraints.regional_headroom_secondary || 0), 0.55, 0.75, true));
          setText('ops-fairness-pressure-value', Number(constraints.fairness_pressure || 0).toFixed(2));
          setTone('ops-card-fairness-pressure', opsTone(Math.min(1.0, Number(constraints.fairness_pressure || 0) / 0.30), 0.45, 0.72));

          setText('ops-recommendation-action', chosenAction ? actionLabel(chosenAction) : 'Plan not generated yet');
          setText('ops-recommendation-summary', chosenAction ? (actionSummaries[chosenAction] || 'Guardrail plan is ready.') : 'Generate a guardrail plan to surface the recommended remediation.');
          setText('ops-dangerous-reflex', actionLabel(plan.dangerous_reflex || 'restart_payment'));
          setText('ops-verdict-summary', executedAction && executedAction !== 'none' ? `Last verified action: ${{actionLabel(executedAction)}}` : 'No remediation has been executed yet.');

          const providerGrid = document.getElementById('ops-provider-grid');
          if (providerGrid) {{
            providerGrid.innerHTML = renderProviderGrid(
              Number(business.seat_hold_utilization || 0),
              Number(business.seat_hold_expiration_rate || 0),
              Boolean(state.payment_feature_disabled)
            );
          }}
        }} catch (_error) {{
          // Leave the current board visible if polling fails.
        }}
      }}
      refreshOperations();
      window.setInterval(refreshOperations, 2000);
    }})();
  </script>
</body>
</html>"""


def _action_label(action_id: str | None) -> str:
    if not action_id:
        return "none"
    return ACTION_LABELS.get(action_id, action_id)


def _flag_label(flag_name: str) -> str:
    return FLAG_LABELS.get(flag_name, flag_name)


def _metric_cards(items: list[tuple[str, str]]) -> str:
    return "".join(
        f"""
        <div class="card">
          <div class="muted">{escape(label)}</div>
          <div class="kpi">{escape(value)}</div>
        </div>
        """
        for label, value in items
    )


def _ops_tone(value: float, watch: float, critical: float, inverse: bool = False) -> str:
    score = 1 - value if inverse else value
    if score >= critical:
        return "critical"
    if score >= watch:
        return "watch"
    return "stable"


def _ops_system_state(technical_metrics: dict, business_metrics: dict) -> str:
    risk_score = max(
        (technical_metrics["latency_p95_ms"] - 900) / 1400,
        business_metrics["queue_abandonment_rate"] / 0.30,
        (business_metrics["retry_amplification_factor"] - 1.0) / 2.5,
    )
    return _ops_tone(risk_score, 0.55, 0.82)


def _eligibility_status(state: dict) -> str:
    if state.get("payment_feature_disabled"):
        return "Online scheduling disabled"
    if state.get("payment_service_unreachable") and state.get("payment_circuit_breaker_enabled"):
        return "Protected by circuit breaker"
    if state.get("payment_service_unreachable"):
        return "Degraded"
    return "Healthy"


def _ops_metric_card(
    label: str,
    value: str,
    tone: str,
    note: str,
    card_id: str = "",
    value_id: str = "",
) -> str:
    card_attr = f' id="{escape(card_id)}"' if card_id else ""
    value_attr = f' id="{escape(value_id)}"' if value_id else ""
    return f"""
    <div class="kpi tone-{tone}"{card_attr}>
      <div class="label">{escape(label)}</div>
      <div class="value"{value_attr}>{escape(value)}</div>
      <div class="note">{escape(note)}</div>
    </div>
    """


def _ops_progress_bar(
    label: str,
    value: float,
    tone: str,
    row_id: str = "",
    bar_id: str = "",
    value_id: str = "",
) -> str:
    pct = max(6, min(100, int(value * 100)))
    row_attr = f' id="{escape(row_id)}"' if row_id else ""
    bar_attr = f' id="{escape(bar_id)}"' if bar_id else ""
    value_attr = f' id="{escape(value_id)}"' if value_id else ""
    return f"""
    <div class="flow-row"{row_attr}>
      <div>{escape(label)}</div>
      <div class="track"><div class="bar {tone}" style="width:{pct}%"{bar_attr}></div></div>
      <div{value_attr}>{pct}%</div>
    </div>
    """


def _render_provider_grid(slot_hold_utilization: float, slot_hold_expiration: float, online_disabled: bool) -> str:
    care_lines = [
        ("Primary Care Virtual", "08:30", "10:00", "11:30", "14:00", "16:00"),
        ("Behavioral Health", "09:00", "10:30", "13:00", "15:30", "18:00"),
        ("Cardiology Follow-up", "08:45", "11:15", "12:45", "15:15", "17:30"),
        ("Urgent Telehealth", "Now", "09:30", "12:00", "14:30", "17:00"),
    ]
    pressure = slot_hold_utilization + slot_hold_expiration * 0.35
    header = """
    <div class="schedule-header">
      <div>Care line</div>
      <div>Window 1</div>
      <div>Window 2</div>
      <div>Window 3</div>
      <div>Window 4</div>
      <div>Window 5</div>
    </div>
    """
    rows = []
    for idx, row in enumerate(care_lines):
        name, *times = row
        cells = []
        for t_index, time_label in enumerate(times):
            phase = (pressure * 10 + idx + t_index) % 5
            if online_disabled:
                cls = "manual"
                title = "Manual fallback"
                sub = "Routed to staff"
            elif phase >= 4.1 or pressure > 1.0:
                cls = "held"
                title = "At-risk hold"
                sub = "Awaiting verification"
            elif phase >= 2.4 or pressure > 0.82:
                cls = "at-risk"
                title = "Limited capacity"
                sub = "Longer confirmation path"
            else:
                cls = "available"
                title = "Bookable"
                sub = "Normal access window"
            cells.append(f'<div class="slot {cls}"><strong>{escape(title)}</strong><span>{escape(time_label)} • {escape(sub)}</span></div>')
        rows.append(
            '<div class="schedule-row">'
            f'<div class="schedule-service">{escape(name)}</div>'
            + "".join(cells)
            + '</div>'
        )
    return f'<div class="schedule-grid">{header}{"".join(rows)}</div>'


def _ops_incident_feed(
    state: dict,
    technical_metrics: dict,
    business_metrics: dict,
    active_scenario: dict,
) -> str:
    items = [
        (
            active_scenario["headline"],
            active_scenario["story"],
        ),
        (
            "Portal and scheduling pressure",
            f"Scheduling latency is {technical_metrics['latency_p95_ms']} ms with retry amplification at {business_metrics['retry_amplification_factor']}x.",
        ),
        (
            "Patient-access impact",
            f"Booking completion is {business_metrics['payment_success_rate']:.0%} and abandonment is {business_metrics['queue_abandonment_rate']:.0%}.",
        ),
        (
            "Operational posture",
            "Online scheduling is in staffed fallback mode."
            if state.get("payment_feature_disabled")
            else "Digital scheduling remains online while the guardrail contains blast radius.",
        ),
    ]
    return "".join(
        f'<div class="feed-item"><strong>{escape(title)}</strong><span class="muted">{escape(body)}</span></div>'
        for title, body in items
    )


def _watch_label(watch_state: dict) -> str:
    status = watch_state.get("status", "idle")
    if status == "pending" and watch_state.get("pending_incident"):
        return "Pending incident latched"
    if status == "acknowledged" and watch_state.get("active_incident"):
        return "Investigating active incident"
    if watch_state.get("armed"):
        return "Armed and waiting"
    return "Idle"


def _scenario_baseline_metrics(scenario_id: str) -> dict:
    return derive_metrics(default_state_for_scenario(scenario_id))


def _scenario_reset_copy(scenario: dict) -> str:
    baseline = _scenario_baseline_metrics(scenario["id"])
    return (
        f"Resetting this scenario returns it to {baseline['latency_p95_ms']} ms latency, "
        f"{baseline['error_rate']:.2f} error rate, and {baseline['retry_rate']:.2f} retry rate."
    )


def _dangerous_reflex_from_plan(plan: dict | None, chosen_action: str) -> str:
    if not plan:
        return "restart_payment"
    explicit = plan.get("dangerous_reflex")
    if explicit:
        return str(explicit)
    candidate_ids = [action.get("id") for action in plan.get("candidate_actions", [])]
    if "restart_payment" in candidate_ids and chosen_action != "restart_payment":
        return "restart_payment"
    for action_id in candidate_ids:
        if action_id != chosen_action:
            return str(action_id)
    return "restart_payment"


def _chosen_rationale(report: dict) -> str:
    chosen_action = report.get("executed_action", {}).get("id") or report.get("best_action", {}).get("id")
    for item in report.get("stratus_ranking", []):
        if item.get("id") == chosen_action:
            rationale = str(item.get("rationale") or item.get("reasoning") or "").strip()
            if rationale:
                return rationale
    return "Stratus selected the action with the safest forecasted recovery path."


def _render_openclaw_execution_surface(
    plan: dict | None,
    report: dict | None,
    state: dict,
    technical_metrics: dict,
    business_metrics: dict,
    stage: str,
) -> str:
    active_scenario = get_scenario(state.get("active_scenario"))
    constraints = current_constraints(state)
    if not plan:
        return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>OpenClaw Execution Surface</title>
  <style>
    body {{ font-family: Georgia, serif; background:#faf4ea; color:#16212b; margin:0; }}
    .wrap {{ max-width: 980px; margin:0 auto; padding:32px; }}
    .card {{ background:white; border:1px solid #ddccb0; border-radius:20px; padding:24px; box-shadow:0 18px 44px rgba(22,33,43,0.08); }}
    a {{ color:#bb5524; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1 style="margin-top:0;">OpenClaw Execution Surface</h1>
      <p>No plan artifact is available yet. Run <code>.venv/bin/python run.py alerts/latest.json --phase demo</code> first, or generate a plan from the Guardrail Console.</p>
      <p><a href="/">Return to Guardrail Console</a></p>
    </div>
  </div>
</body>
</html>"""

    chosen_action = plan.get("best_action", {}).get("id", "n/a")
    chosen_label = plan.get("browser_workflow", {}).get("button_label", chosen_action)
    chosen_reason = _chosen_rationale(plan)
    rejected_action = _dangerous_reflex_from_plan(plan, chosen_action)
    before_metrics = plan.get("observed_condition", {}).get("metrics", {})
    report_actual = (report or {}).get("actual_outcome", {})
    baseline_metrics = _scenario_baseline_metrics(active_scenario["id"])
    notes = "".join(f"<li>{escape(note)}</li>" for note in plan.get("notes", []))
    stage = stage if stage in {"before", "after-action", "verdict"} else "before"
    verdict_ready = bool(report and report.get("executed_action"))
    verdict_title = "Live Final Verdict" if verdict_ready else "Awaiting Final Verdict"
    verdict_status = (
        "Verification complete. The execution surface is now showing the final report."
        if verdict_ready
        else "Stay on this page. Once verify or fallback finishes, the final verdict will appear here automatically."
    )
    verdict_block = f"""
    <div class="card" id="openclaw-verdict-card">
      <h2 style="margin-top:0;">{escape(verdict_title)}</h2>
      <p id="openclaw-verdict-status" style="color:var(--muted);">{escape(verdict_status)}</p>
      <ul>
        <li>Executed remediation: <strong id="openclaw-verdict-action">{escape(_action_label(report.get('executed_action', {}).get('id', chosen_action) if report else chosen_action))}</strong></li>
        <li>After latency: <code id="openclaw-verdict-latency">{escape(str(report_actual.get('latency_p95_ms', technical_metrics['latency_p95_ms'] if verdict_ready else 'pending')))}</code></li>
        <li>Retry rate: <code id="openclaw-verdict-retry">{escape(str(report_actual.get('retry_rate', technical_metrics['retry_rate'] if verdict_ready else 'pending')))}</code></li>
        <li>Risk level: <code id="openclaw-verdict-risk">{escape(str(report_actual.get('risk_level', 'pending')))}</code></li>
        <li>Blast radius: <code id="openclaw-verdict-blast">{escape(str(report_actual.get('blast_radius', 'pending')))}</code></li>
      </ul>
      <p><a href="/">Open the full Guardrail Console</a></p>
    </div>
    """

    after_block = ""
    if stage == "after-action":
        after_block = f"""
        <div class="card" id="openclaw-after-card">
          <h2 style="margin-top:0;">Shared State Updated</h2>
          <ul>
            <li>Last action: <strong id="openclaw-after-last-action">{escape(_action_label(state.get('last_action')))}</strong> <code>{escape(str(state.get('last_action') or 'none'))}</code></li>
            <li>Retry factor now: <code id="openclaw-after-retry-factor">{business_metrics['retry_amplification_factor']}x</code></li>
            <li>Booking completion now: <code id="openclaw-after-booking-completion">{business_metrics['payment_success_rate']:.2f}</code></li>
          </ul>
          <form method="post" action="/api/verify">
            <input type="hidden" name="input_path" value="{escape(str(DEFAULT_ALERT_PATH))}">
            <input type="hidden" name="return_to" value="/openclaw-execution">
            <button style="background:#bb5524; color:white; border:none; border-radius:12px; padding:11px 16px; cursor:pointer;">Run Verify</button>
          </form>
        </div>
        """

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>OpenClaw Execution Surface</title>
  <style>
    :root {{ --bg:#faf4ea; --ink:#16212b; --muted:#6b6256; --line:#ddccb0; --card:#fffaf3; --accent:#bb5524; }}
    body {{ margin:0; font-family: Iowan Old Style, Georgia, serif; background:linear-gradient(180deg, #fff8ef 0%, var(--bg) 100%); color:var(--ink); }}
    .wrap {{ max-width: 1040px; margin:0 auto; padding:28px; }}
    .hero {{ display:flex; justify-content:space-between; gap:20px; align-items:flex-start; margin-bottom:18px; }}
    .eyebrow {{ letter-spacing:0.08em; text-transform:uppercase; color:var(--muted); font-size:0.82rem; }}
    .grid {{ display:grid; grid-template-columns: 1.05fr 0.95fr; gap:18px; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:20px; padding:22px; box-shadow:0 18px 44px rgba(22,33,43,0.08); }}
    .metric-grid {{ display:grid; grid-template-columns: repeat(3, 1fr); gap:12px; margin-top:14px; }}
    .mini-grid {{ display:grid; grid-template-columns: repeat(2, 1fr); gap:12px; margin-top:14px; }}
    .metric {{ border:1px solid var(--line); border-radius:16px; padding:14px; background:rgba(255,255,255,0.5); }}
    .metric .label {{ color:var(--muted); }}
    .metric .value {{ font-size:1.55rem; margin-top:6px; }}
    .callout {{ border-left:4px solid var(--accent); padding-left:12px; }}
    button {{ background:#bb5524; color:white; border:none; border-radius:14px; padding:13px 18px; cursor:pointer; font:inherit; }}
    a {{ color:var(--accent); }}
    ul {{ line-height:1.6; }}
    @media (max-width: 920px) {{ .grid, .metric-grid, .mini-grid {{ grid-template-columns: 1fr; }} .hero {{ display:grid; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div>
        <div class="eyebrow">OpenClaw Browser Path</div>
        <h1 style="margin:10px 0 6px;">Dedicated Execution Surface</h1>
        <p style="margin:0; color:var(--muted); max-width:760px;">This page is optimized for the managed browser. Open it once, inspect the before-state, click one stable remediation button, then stay here while verify or fallback writes the live verdict below.</p>
      </div>
      <div><a href="/">Return to Guardrail Console</a></div>
    </div>
    <div class="grid">
      <div class="card">
        <h2 style="margin-top:0;">Before-Action Incident Card</h2>
        <p class="callout"><strong>{escape(active_scenario['label'])}</strong><br>{escape(active_scenario['headline'])}</p>
        <p style="color:var(--muted); margin-top:0;">Scenario baseline on reset: <code>{baseline_metrics['latency_p95_ms']}</code> ms latency, <code>{baseline_metrics['error_rate']:.2f}</code> error rate, <code>{baseline_metrics['retry_rate']:.2f}</code> retry rate.</p>
        <ul>
          <li>Guardrail choice: <strong>{escape(_action_label(chosen_action))}</strong></li>
          <li>Operator action label: <code>{escape(chosen_label)}</code></li>
          <li>Dangerous reflex: <strong>{escape(_action_label(rejected_action))}</strong></li>
          <li>Why this action won: {escape(chosen_reason)}</li>
        </ul>
        <div class="metric-grid">
          <div class="metric"><div class="label">Scheduling latency P95</div><div class="value">{escape(str(before_metrics.get('latency_p95_ms', 'n/a')))}</div></div>
          <div class="metric"><div class="label">Scheduling error rate</div><div class="value">{escape(str(before_metrics.get('error_rate', 'n/a')))}</div></div>
          <div class="metric"><div class="label">Booking retry rate</div><div class="value">{escape(str(before_metrics.get('retry_rate', 'n/a')))}</div></div>
        </div>
        <form method="post" action="/api/execute-planned" style="margin-top:18px;">
          <input type="hidden" name="return_to" value="/openclaw-execution?stage=after-action">
          <button id="openclaw-demo-run">Execute Planned Action: {escape(chosen_label)}</button>
        </form>
        <form method="post" action="/api/reset" style="margin-top:12px;">
          <input type="hidden" name="scenario_id" value="{escape(active_scenario['id'])}">
          <input type="hidden" name="return_to" value="/openclaw-execution">
          <button style="background:#405f7f;">Reset to Healthy Baseline</button>
        </form>
        <p style="margin-top:12px; color:var(--muted);">Manual ops controls: <a href="/feature-flags">/feature-flags</a></p>
      </div>
      <div class="card">
        <h2 style="margin-top:0;">Current Shared State</h2>
        <ul>
          <li>Last action: <strong id="openclaw-last-action">{escape(_action_label(state.get('last_action')))}</strong></li>
          <li>Retry factor: <code id="openclaw-retry-factor">{business_metrics['retry_amplification_factor']}x</code></li>
          <li>Booking completion: <code id="openclaw-booking-completion">{business_metrics['payment_success_rate']:.2f}</code></li>
          <li>Scheduling abandonment: <code id="openclaw-abandonment">{business_metrics['queue_abandonment_rate']:.2f}</code></li>
        </ul>
        <div class="mini-grid">
          <div class="metric"><div class="label">External arrivals</div><div class="value" id="openclaw-external-arrivals">{constraints['lambda_external']:.2f}</div></div>
          <div class="metric"><div class="label">Retry arrivals</div><div class="value" id="openclaw-retry-arrivals">{constraints['lambda_retry']:.2f}</div></div>
          <div class="metric"><div class="label">Queue depth</div><div class="value" id="openclaw-queue-depth">{constraints['queue_depth']:.0f}</div></div>
          <div class="metric"><div class="label">Secondary headroom</div><div class="value" id="openclaw-secondary-headroom">{constraints['regional_headroom_secondary']:.2f}</div></div>
        </div>
        <strong>Guardrail notes</strong>
        <ul>{notes or '<li>No notes recorded yet.</li>'}</ul>
      </div>
    </div>
    <div style="margin-top:18px;">{after_block}{verdict_block}</div>
  </div>
  <script>
    (() => {{
      const actionLabels = {json.dumps(ACTION_LABELS)};
      function actionLabel(actionId) {{
        if (!actionId) return 'none';
        return actionLabels[actionId] || actionId;
      }}
      function setText(id, value) {{
        const node = document.getElementById(id);
        if (node && value !== undefined && value !== null) node.textContent = value;
      }}
      async function refreshExecution() {{
        try {{
          const response = await fetch('/api/state', {{ cache: 'no-store' }});
          if (!response.ok) return;
          const data = await response.json();
          const state = data.state || {{}};
          const business = data.business_metrics || {{}};
          const constraints = data.constraints || {{}};
          const report = data.report || {{}};
          const actual = report.actual_outcome || {{}};
          setText('openclaw-last-action', actionLabel(state.last_action));
          setText('openclaw-retry-factor', `${{Number(business.retry_amplification_factor || 0).toFixed(1)}}x`);
          setText('openclaw-booking-completion', Number(business.payment_success_rate || 0).toFixed(2));
          setText('openclaw-abandonment', Number(business.queue_abandonment_rate || 0).toFixed(2));
          setText('openclaw-external-arrivals', Number(constraints.lambda_external || 0).toFixed(2));
          setText('openclaw-retry-arrivals', Number(constraints.lambda_retry || 0).toFixed(2));
          setText('openclaw-queue-depth', Math.round(Number(constraints.queue_depth || 0)));
          setText('openclaw-secondary-headroom', Number(constraints.regional_headroom_secondary || 0).toFixed(2));
          setText('openclaw-after-last-action', actionLabel(state.last_action));
          setText('openclaw-after-retry-factor', `${{Number(business.retry_amplification_factor || 0).toFixed(1)}}x`);
          setText('openclaw-after-booking-completion', Number(business.payment_success_rate || 0).toFixed(2));
          if (report.executed_action) {{
            setText('openclaw-verdict-status', 'Verification complete. The execution surface is now showing the final report.');
            setText('openclaw-verdict-action', actionLabel(report.executed_action.id));
            if (actual.latency_p95_ms !== undefined) setText('openclaw-verdict-latency', actual.latency_p95_ms);
            if (actual.retry_rate !== undefined) setText('openclaw-verdict-retry', actual.retry_rate);
            if (actual.risk_level !== undefined) setText('openclaw-verdict-risk', actual.risk_level);
            if (actual.blast_radius !== undefined) setText('openclaw-verdict-blast', actual.blast_radius);
          }}
        }} catch (_error) {{
          // Keep the current execution surface visible if a poll fails.
        }}
      }}
      refreshExecution();
      window.setInterval(refreshExecution, 2000);
    }})();
  </script>
</body>
</html>"""


def _workflow_statuses(
    state: dict,
    plan: dict | None,
    playbook: dict | None,
    report: dict | None,
) -> list[dict]:
    chosen_action = plan.get("best_action", {}).get("id") if plan else None
    last_action = state.get("last_action")
    return [
        {
            "step": "1",
            "label": "Plan",
            "detail": "Planning artifact written and shortlist selected." if plan else "Run planning to generate shortlist and ranking.",
            "status": "done" if plan else "active",
        },
        {
            "step": "2",
            "label": "Playbook",
            "detail": "Browser handoff is ready." if playbook else "Generate the browser playbook from the plan.",
            "status": "done" if playbook else ("active" if plan else "pending"),
        },
        {
            "step": "3",
            "label": "Execute",
            "detail": f"Chosen action {_action_label(chosen_action)} has been applied." if chosen_action and last_action == chosen_action else "Apply the chosen remediation in the browser execution surface.",
            "status": "done" if chosen_action and last_action == chosen_action else ("active" if playbook else "pending"),
        },
        {
            "step": "4",
            "label": "Verify",
            "detail": "Verification report is available." if report else "Run verify and surface the final verdict.",
            "status": "done" if report else ("active" if chosen_action and last_action == chosen_action else "pending"),
        },
    ]


def _load_artifact(name: str) -> dict | None:
    path = ARTIFACT_PATHS[name]
    if not path.exists():
        return None
    try:
        return load_json_path(path)
    except json.JSONDecodeError:
        return None


def derive_state_for_verify(payload: dict, evidence: dict) -> dict:
    return classify_incident(payload, evidence)


def _load_alert_payload(input_path: str) -> tuple[dict, Path]:
    resolved_path = Path(input_path)
    payload = load_json_path(resolved_path)
    return payload, resolved_path


def _load_incident_snapshot(payload: dict) -> tuple[str, dict, dict]:
    synced_evidence = _wait_for_prometheus_sync(payload)
    state = classify_incident(payload, synced_evidence)
    return "alertmanager_webhook", state, synced_evidence


def _wait_for_prometheus_sync(payload: dict, timeout_seconds: float = 10.0) -> dict:
    state = load_state()
    expected_metrics = derive_metrics(state)
    deadline = time.monotonic() + timeout_seconds
    latest = collect_evidence(payload)
    while True:
        if latest.get("prometheus", {}).get("source") != "live":
            return latest
        if latest.get("metrics") == expected_metrics:
            return latest
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return latest
        time.sleep(min(2.0, remaining))
        latest = collect_evidence(payload)


def _clear_console_artifacts() -> None:
    artifact_files = [
        Path("outputs/alert_latest_plan.json"),
        Path("outputs/alert_latest_plan.md"),
        Path("outputs/alert_latest_browser_playbook.json"),
        Path("outputs/alert_latest_browser_playbook.md"),
        Path("outputs/alert_latest_openclaw_demo.json"),
        Path("outputs/alert_latest_openclaw_demo.md"),
        Path("outputs/alert_latest_report.json"),
        Path("outputs/alert_latest_report.md"),
    ]
    for path in artifact_files:
        if path.exists():
            path.unlink()


def _clear_report_artifacts() -> None:
    for path in [
        Path("outputs/alert_latest_report.json"),
        Path("outputs/alert_latest_report.md"),
    ]:
        if path.exists():
            path.unlink()


def _action_form(action_id: str, label: str) -> str:
    css = ' class="danger"' if action_id == "disable_flag" else ""
    summary = ACTION_SUMMARIES.get(action_id, "")
    return f"""<form method="post" action="/api/execute" id="form-{action_id}" data-action-id="{action_id}"{css}>
      <input type="hidden" name="action_id" value="{action_id}">
      <input type="hidden" name="return_to" value="feature-flags">
      <div class="action-card">
        <strong>{escape(label)}</strong>
        <div class="action-meta">{escape(summary)}</div>
        <button id="action-{action_id}" data-action-id="{action_id}" data-action-label="{label}">Apply Remediation</button>
      </div>
    </form>"""


def _toggle_form(flag_name: str, enabled: bool) -> str:
    next_value = "false" if enabled else "true"
    label = "Disable" if enabled else "Enable"
    return f"""<form method="post" action="/api/toggle" style="margin-bottom:10px;">
      <input type="hidden" name="flag_name" value="{flag_name}">
      <input type="hidden" name="enabled" value="{next_value}">
      <input type="hidden" name="return_to" value="feature-flags">
      <button>{label} {_flag_label(flag_name)}</button>
    </form>"""


def _return_path(return_to: str) -> str:
    if return_to.startswith("/"):
        return return_to
    if return_to == "feature-flags":
        return "/feature-flags"
    if return_to == "operations":
        return "/operations"
    if return_to in VIEW_ORDER:
        return f"/?view={return_to}"
    return "/"
