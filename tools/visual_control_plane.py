from __future__ import annotations

import json
from html import escape
from pathlib import Path

from fastapi import FastAPI, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from tools.runtime_state import (
    apply_action,
    control_plane_urls,
    derive_metrics,
    load_state,
    reset_state,
    set_flag,
    set_scenario,
)
from tools.scenario_catalog import (
    default_sequence_preview,
    derive_business_metrics,
    get_scenario,
    scenario_catalog,
)

app = FastAPI(title="Guardrail Console")

BASE_URL = "http://127.0.0.1:8010"
VIEW_ORDER = ["incident", "decision", "execution", "verdict"]
ARTIFACT_PATHS = {
    "plan": Path("outputs/alert_latest_plan.json"),
    "playbook": Path("outputs/alert_latest_browser_playbook.json"),
    "report": Path("outputs/alert_latest_report.json"),
}


@app.get("/", response_class=HTMLResponse)
def dashboard(view: str = Query("incident")) -> str:
    active_view = view if view in VIEW_ORDER else "incident"
    state = load_state()
    technical_metrics = derive_metrics(state)
    business_metrics = derive_business_metrics(state, technical_metrics)
    urls = control_plane_urls(BASE_URL)
    active_scenario = get_scenario(state.get("active_scenario"))
    scenarios = scenario_catalog()
    plan = _load_artifact("plan")
    playbook = _load_artifact("playbook")
    report = _load_artifact("report")

    if active_view == "incident":
        body = _render_incident_view(
            state, technical_metrics, business_metrics, active_scenario, scenarios
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
        technical_metrics=technical_metrics,
        business_metrics=business_metrics,
        active_scenario=active_scenario,
    )


@app.get("/feature-flags", response_class=HTMLResponse)
def feature_flags() -> str:
    state = load_state()
    active_scenario = get_scenario(state.get("active_scenario"))
    action_buttons = [
        ("rate_limit_retries", "Apply Retry Rate Limit"),
        ("enable_payment_circuit_breaker", "Enable Payment Circuit Breaker"),
        ("increase_retry_backoff", "Increase Retry Backoff"),
        ("restart_payment", "Restart Payment"),
        ("shift_traffic", "Shift Traffic"),
        ("disable_flag", "Disable Payment Flag"),
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
  <title>Guardrail Console - Execution Surface</title>
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
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <div class="badge">Scenario: {escape(active_scenario['label'])}</div>
        <div class="badge">Expected first action: {escape(active_scenario['expected_first_action'])}</div>
        <h1 style="margin:14px 0 6px;">Execution Surface</h1>
        <p class="muted" style="max-width:720px;">OpenClaw uses this page to perform the visible remediation step and capture before / after evidence.</p>
      </div>
      <div>
        <a href="/">Return to Guardrail Console</a>
      </div>
    </div>
    <div class="grid">
      <div class="card">
        <h2 style="margin-top:0;">Execute Remediation</h2>
        <p class="muted">Use the chosen action from the browser playbook. Dangerous kill switches stay visible but visually distinct.</p>
        {buttons}
        <form method="post" action="/api/reset" style="margin-top:12px;">
          <input type="hidden" name="return_to" value="feature-flags">
          <button>Reset Active Scenario</button>
        </form>
      </div>
      <div class="card">
        <h2 style="margin-top:0;">Condition Toggles</h2>
        <p class="muted">These are the rehearsal controls behind the scenario selector.</p>
        {toggles}
      </div>
    </div>
  </div>
</body>
</html>"""


@app.get("/api/state")
def state_api() -> dict:
    state = load_state()
    technical_metrics = derive_metrics(state)
    return {
        "scenario": get_scenario(state.get("active_scenario")),
        "state": state,
        "metrics": technical_metrics,
        "business_metrics": derive_business_metrics(state, technical_metrics),
        "artifacts": {
            name: path.exists()
            for name, path in ARTIFACT_PATHS.items()
        },
    }


@app.post("/api/actions/{action_id}")
def api_action(action_id: str) -> dict:
    state = apply_action(action_id)
    technical_metrics = derive_metrics(state)
    return {
        "status": "ok",
        "scenario": get_scenario(state.get("active_scenario")),
        "state": state,
        "metrics": technical_metrics,
        "business_metrics": derive_business_metrics(state, technical_metrics),
    }


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
    return RedirectResponse(_return_path(return_to), status_code=303)


@app.post("/api/execute")
def execute_from_form(
    action_id: str = Form(...),
    return_to: str = Form("feature-flags"),
) -> RedirectResponse:
    apply_action(action_id)
    return RedirectResponse(_return_path(return_to), status_code=303)


@app.post("/api/reset")
def reset_from_form(return_to: str = Form("incident")) -> RedirectResponse:
    reset_state()
    _clear_console_artifacts()
    return RedirectResponse(_return_path(return_to), status_code=303)


def _render_shell(
    active_view: str,
    body: str,
    state: dict,
    technical_metrics: dict,
    business_metrics: dict,
    active_scenario: dict,
) -> str:
    nav = "".join(
        f'<a class="tab{" active" if view == active_view else ""}" href="/?view={view}">{view.title()}</a>'
        for view in VIEW_ORDER
    )
    pills = "".join(
        f'<span class="pill">{escape(label)}</span>'
        for label in [
            f"Scenario: {active_scenario['label']}",
            f"Last action: {state['last_action'] or 'none'}",
            f"Retry factor: {business_metrics['retry_amplification_factor']}x",
            f"Payment success: {business_metrics['payment_success_rate']:.2f}",
        ]
    )
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
    .tab {{
      text-decoration:none;
      color:var(--ink);
      padding:11px 15px;
      border:1px solid var(--line);
      border-radius:14px;
      background:rgba(255,255,255,0.55);
    }}
    .tab.active {{ background:var(--accent); color:white; border-color:var(--accent); }}
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
          <p>One operator-facing shell for incident context, decisioning, browser execution, and final verdict. This is Hansen's execution surface for the Week 2 demo.</p>
        </div>
        <div class="nav">{nav}</div>
      </div>
      <div>{pills}</div>
    </div>
    {body}
  </div>
</body>
</html>"""


def _render_incident_view(
    state: dict,
    technical_metrics: dict,
    business_metrics: dict,
    active_scenario: dict,
    scenarios: list[dict],
) -> str:
    technical = _metric_cards(
        [
            ("Latency P95", f"{technical_metrics['latency_p95_ms']} ms"),
            ("Error Rate", f"{technical_metrics['error_rate']:.2f}"),
            ("Retry Rate", f"{technical_metrics['retry_rate']:.2f}"),
        ]
    )
    business = _metric_cards(
        [
            ("Queue Abandonment", f"{business_metrics['queue_abandonment_rate']:.2f}"),
            ("Payment Success", f"{business_metrics['payment_success_rate']:.2f}"),
            ("Seat Hold Utilization", f"{business_metrics['seat_hold_utilization']:.2f}"),
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
            f"payment_service_unreachable={str(state['payment_service_unreachable']).lower()}",
            f"loadgenerator_flood_homepage={str(state['loadgenerator_flood_homepage']).lower()}",
            f"retry_rate_limit_enabled={str(state['retry_rate_limit_enabled']).lower()}",
            f"payment_circuit_breaker_enabled={str(state['payment_circuit_breaker_enabled']).lower()}",
            f"retry_backoff_enabled={str(state['retry_backoff_enabled']).lower()}",
        ]
    )
    return f"""
    <div class="grid two">
      <div class="card">
        <h2 style="margin-bottom:8px;">Incident View</h2>
        <p class="callout"><strong>{escape(active_scenario['label'])}</strong><br>{escape(active_scenario['headline'])}</p>
        <p class="muted">{escape(active_scenario['story'])}</p>
        <form class="inline" method="post" action="/api/scenario" style="margin-top:16px;">
          <input type="hidden" name="return_to" value="incident">
          <label for="scenario_id"><strong>Scenario</strong></label>
          <select id="scenario_id" name="scenario_id">{scenario_options}</select>
          <button class="primary">Apply Scenario</button>
          <button formaction="/api/reset" name="return_to" value="incident">Reset Active Scenario</button>
        </form>
        <div style="margin-top:16px;">{flags}</div>
      </div>
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
        return """
        <div class="card">
          <h2>Decision View</h2>
          <p class="muted">No plan artifact is available yet.</p>
          <p>Run <code>.venv/bin/python run.py alerts/latest.json --phase plan</code> to populate the shortlist, ranking, and browser playbook.</p>
        </div>
        """

    shortlist = "".join(
        f"<tr><td><code>{escape(action['id'])}</code></td><td>{escape(action['category'])}</td><td>{escape(action['description'])}</td></tr>"
        for action in plan.get("candidate_actions", [])
    )
    rankings = "".join(
        f"<tr><td>{item['rank']}</td><td><code>{escape(item['id'])}</code></td><td>{item['confidence']:.2f}</td><td>{escape(item.get('reasoning') or item.get('rationale') or '')}</td></tr>"
        for item in plan.get("stratus_ranking", [])
    )
    rationale = "".join(
        f"<li>{escape(line)}</li>" for line in plan.get("planner", {}).get("selection_rationale", [])
    )
    chosen = plan["best_action"]["id"]
    return f"""
    <div class="grid two">
      <div class="card">
        <h2>Decision View</h2>
        <p class="muted">Scenario-aware shortlist generation followed by Stratus guardrail ranking.</p>
        <div class="action-grid" style="margin-top:16px;">
          <div class="mini">
            <div class="muted">Expected first action</div>
            <div class="kpi" style="font-size:1.4rem;">{escape(active_scenario['expected_first_action'])}</div>
          </div>
          <div class="mini">
            <div class="muted">Guardrail choice</div>
            <div class="kpi" style="font-size:1.4rem;">{escape(chosen)}</div>
          </div>
        </div>
        <div style="margin-top:18px;">
          <strong>Shortlist rationale</strong>
          <ul class="list">{rationale}</ul>
        </div>
      </div>
      <div class="card">
        <h2>Baseline Toggle</h2>
        <p class="muted">Week 2 keeps baseline as a demo mode, not the main product path.</p>
        <ul class="list">
          <li>Same incident.</li>
          <li>Same shortlist.</li>
          <li>Different chooser.</li>
          <li>Expected naive reflex: <code>restart_payment</code>.</li>
        </ul>
      </div>
    </div>
    <div class="grid two" style="margin-top:18px;">
      <div class="card">
        <h3>Scenario Shortlist</h3>
        <table class="table">
          <thead><tr><th>Action</th><th>Category</th><th>Description</th></tr></thead>
          <tbody>{shortlist}</tbody>
        </table>
      </div>
      <div class="card">
        <h3>Stratus Ranking</h3>
        <table class="table">
          <thead><tr><th>Rank</th><th>Action</th><th>Conf.</th><th>Why</th></tr></thead>
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
        <p class="muted">This is Hansen's shell for connecting plan, browser execution, verify, and verdict.</p>
        <div class="timeline">{timeline}</div>
        <div style="margin-top:18px;">
          <a class="button" href="{urls['feature_flags']}">Open Execution Surface</a>
        </div>
      </div>
      <div class="card">
        <h2>Sequence Preview</h2>
        <p class="muted">Present the one-action workflow cleanly now; accept future multi-step sequences without redesigning the shell.</p>
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
          <li>Plan command: <code>.venv/bin/python run.py alerts/latest.json --phase plan</code></li>
          <li>Feature flags: <a href="{urls['feature_flags']}">{urls['feature_flags']}</a></li>
          <li>Verify command: <code>.venv/bin/python run.py alerts/latest.json --phase verify</code></li>
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
        return """
        <div class="card">
          <h2>Verdict View</h2>
          <p class="muted">No verification report is available yet.</p>
          <p>Run <code>.venv/bin/python run.py alerts/latest.json --phase verify</code> after the browser action to populate the final verdict.</p>
        </div>
        """

    actual = report.get("actual_outcome", {})
    predicted = report.get("predicted_vs_actual", {}).get("predicted", {})
    drift = report.get("predicted_vs_actual", {}).get("drift", {"score": 0.0})
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
        <ul class="list">
          <li>Chosen action: <code>{escape(str(report.get('best_action', {}).get('id', 'n/a')))}</code></li>
          <li>Before latency: <code>{escape(str(before_metrics.get('latency_p95_ms', 'n/a')))}</code></li>
          <li>After latency: <code>{escape(str(actual.get('latency_p95_ms', 'n/a')))}</code></li>
          <li>Queue abandonment now: <code>{business_metrics['queue_abandonment_rate']:.2f}</code></li>
          <li>Case writeback status: <code>planned for Evaluation Agent</code></li>
        </ul>
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
      </div>
    </div>
    """


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
            "detail": f"Chosen action {chosen_action} has been applied." if chosen_action and last_action == chosen_action else "Apply the chosen remediation in the feature-flag UI.",
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
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _clear_console_artifacts() -> None:
    artifact_files = [
        Path("outputs/alert_latest_plan.json"),
        Path("outputs/alert_latest_plan.md"),
        Path("outputs/alert_latest_browser_playbook.json"),
        Path("outputs/alert_latest_browser_playbook.md"),
        Path("outputs/alert_latest_report.json"),
        Path("outputs/alert_latest_report.md"),
    ]
    for path in artifact_files:
        if path.exists():
            path.unlink()


def _action_form(action_id: str, label: str) -> str:
    css = ' class="danger"' if action_id == "disable_flag" else ""
    return f"""<form method="post" action="/api/execute" id="form-{action_id}" data-action-id="{action_id}"{css} style="margin-bottom:10px;">
      <input type="hidden" name="action_id" value="{action_id}">
      <input type="hidden" name="return_to" value="feature-flags">
      <button id="action-{action_id}" data-action-id="{action_id}" data-action-label="{label}">{label}</button>
    </form>"""


def _toggle_form(flag_name: str, enabled: bool) -> str:
    next_value = "false" if enabled else "true"
    label = "Disable" if enabled else "Enable"
    return f"""<form method="post" action="/api/toggle" style="margin-bottom:10px;">
      <input type="hidden" name="flag_name" value="{flag_name}">
      <input type="hidden" name="enabled" value="{next_value}">
      <input type="hidden" name="return_to" value="feature-flags">
      <button>{label} {flag_name}</button>
    </form>"""


def _return_path(return_to: str) -> str:
    if return_to == "feature-flags":
        return "/feature-flags"
    if return_to in VIEW_ORDER:
        return f"/?view={return_to}"
    return "/"
