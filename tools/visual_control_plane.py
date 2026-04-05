from __future__ import annotations

import json
from html import escape
from pathlib import Path

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from tools.runtime_state import (
    apply_action,
    control_plane_urls,
    derive_metrics,
    load_state,
    reset_state,
    set_flag,
)

app = FastAPI(title="Incident Visual Control Plane")


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    state = load_state()
    metrics = derive_metrics(state)
    urls = control_plane_urls("http://127.0.0.1:8010")
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Incident Dashboard</title>
  <style>
    :root {{ --bg:#f6f1e8; --ink:#18222f; --accent:#c65d2e; --card:#fffaf1; --line:#d9c8ac; }}
    body {{ font-family: Georgia, serif; margin: 0; background: radial-gradient(circle at top, #fff6e7, var(--bg)); color: var(--ink); }}
    .wrap {{ max-width: 980px; margin: 0 auto; padding: 32px; }}
    .hero {{ display:grid; gap:16px; margin-bottom: 28px; }}
    .cards {{ display:grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }}
    .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 18px; padding: 18px; box-shadow: 0 18px 40px rgba(24,34,47,0.08); }}
    .kpi {{ font-size: 2rem; margin: 6px 0; }}
    .pill {{ display:inline-block; padding:6px 10px; border-radius:999px; background:#efe3c9; margin-right:8px; }}
    a.button {{ display:inline-block; padding:12px 16px; border-radius:12px; background:var(--accent); color:white; text-decoration:none; }}
    ul {{ line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>Checkout Incident Dashboard</h1>
      <p>Live Level 2 visual surface for OpenClaw browser inspection and action verification.</p>
      <div>
        <span class="pill">payment_service_unreachable={str(state["payment_service_unreachable"]).lower()}</span>
        <span class="pill">loadgenerator_flood_homepage={str(state["loadgenerator_flood_homepage"]).lower()}</span>
        <span class="pill">retry_rate_limit_enabled={str(state["retry_rate_limit_enabled"]).lower()}</span>
        <span class="pill">last_action={escape(str(state["last_action"]))}</span>
      </div>
      <div>
        <a class="button" href="{urls['feature_flags']}">Open Feature Flags</a>
      </div>
    </div>
    <div class="cards">
      <div class="card"><strong>Latency P95</strong><div class="kpi">{metrics['latency_p95_ms']} ms</div></div>
      <div class="card"><strong>Error Rate</strong><div class="kpi">{metrics['error_rate']:.2f}</div></div>
      <div class="card"><strong>Retry Rate</strong><div class="kpi">{metrics['retry_rate']:.2f}</div></div>
    </div>
    <div class="card" style="margin-top:16px;">
      <strong>Recommended Browser Evidence</strong>
      <ul>
        <li>Capture this dashboard before and after action.</li>
        <li>Open the feature-flag page to execute a mitigation.</li>
        <li>Open Prometheus alerts at <code>http://127.0.0.1:9091/alerts</code>.</li>
      </ul>
    </div>
  </div>
</body>
</html>"""


@app.get("/feature-flags", response_class=HTMLResponse)
def feature_flags() -> str:
    state = load_state()
    buttons = _action_form("rate_limit_retries", "Apply Retry Rate Limit")
    buttons += _action_form("restart_payment", "Restart Payment")
    buttons += _action_form("shift_traffic", "Shift Traffic")
    buttons += _action_form("disable_flag", "Disable Payment Flag")
    toggles = "".join(
        _toggle_form(name, enabled)
        for name, enabled in [
            ("payment_service_unreachable", state["payment_service_unreachable"]),
            ("loadgenerator_flood_homepage", state["loadgenerator_flood_homepage"]),
            ("retry_rate_limit_enabled", state["retry_rate_limit_enabled"]),
            ("traffic_shift_enabled", state["traffic_shift_enabled"]),
            ("payment_feature_disabled", state["payment_feature_disabled"]),
        ]
    )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Feature Flags</title>
  <style>
    body {{ font-family: Georgia, serif; background:#fbf7ef; color:#18222f; margin:0; }}
    .wrap {{ max-width: 980px; margin: 0 auto; padding: 32px; }}
    .grid {{ display:grid; grid-template-columns: 1fr 1fr; gap:16px; }}
    .card {{ background:white; border:1px solid #dbcdb6; border-radius:16px; padding:18px; box-shadow:0 18px 40px rgba(24,34,47,0.06); }}
    button {{ background:#0f6c5c; color:white; border:none; border-radius:10px; padding:10px 14px; cursor:pointer; }}
    .danger button {{ background:#b0432c; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Feature Flags & Mitigations</h1>
    <p>OpenClaw browser can use this page to perform a visible action and capture evidence.</p>
    <div class="grid">
      <div class="card">
        <h2>Execute Remediation</h2>
        {buttons}
        <form method="post" action="/api/reset" style="margin-top:12px;"><button>Reset Incident State</button></form>
      </div>
      <div class="card">
        <h2>Toggle Conditions</h2>
        {toggles}
      </div>
    </div>
  </div>
</body>
</html>"""


@app.get("/api/state")
def state_api() -> dict:
    state = load_state()
    return {"state": state, "metrics": derive_metrics(state)}


@app.post("/api/actions/{action_id}")
def api_action(action_id: str) -> dict:
    state = apply_action(action_id)
    return {"status": "ok", "state": state, "metrics": derive_metrics(state)}


@app.post("/api/toggle")
def api_toggle(flag_name: str = Form(...), enabled: str = Form(...)) -> RedirectResponse:
    set_flag(flag_name, enabled.lower() == "true")
    return RedirectResponse("/feature-flags", status_code=303)


@app.post("/api/execute")
def execute_from_form(action_id: str = Form(...)) -> RedirectResponse:
    apply_action(action_id)
    return RedirectResponse("/feature-flags", status_code=303)


@app.post("/api/reset")
def reset_from_form() -> RedirectResponse:
    reset_state()
    return RedirectResponse("/feature-flags", status_code=303)


def _action_form(action_id: str, label: str) -> str:
    css = ' class="danger"' if action_id == "disable_flag" else ""
    return f"""<form method="post" action="/api/execute" id="form-{action_id}" data-action-id="{action_id}"{css} style="margin-bottom:10px;">
      <input type="hidden" name="action_id" value="{action_id}">
      <button id="action-{action_id}" data-action-id="{action_id}" data-action-label="{label}">{label}</button>
    </form>"""


@app.get("/verdict", response_class=HTMLResponse)
def verdict_latest() -> str:
    """Render the latest verdict. Tries the most recently modified verdict file."""
    outputs = Path("outputs")
    verdict_files = sorted(
        outputs.glob("*_verdict.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not verdict_files:
        return _verdict_empty_page()
    try:
        v = json.loads(verdict_files[0].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _verdict_empty_page()
    return _render_verdict_html(v)


@app.get("/verdict/{scenario_id}", response_class=HTMLResponse)
def verdict_by_scenario(scenario_id: str) -> str:
    """Render the verdict for a specific scenario."""
    import re
    if not re.fullmatch(r"[a-z0-9_]+", scenario_id):
        return _verdict_empty_page(scenario_id)
    path = Path("outputs") / f"{scenario_id}_verdict.json"
    if not path.exists():
        return _verdict_empty_page(scenario_id)
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _verdict_empty_page(scenario_id)
    return _render_verdict_html(v)


def _render_verdict_html(v: dict) -> str:
    """Render a verdict dict as a polished HTML page."""
    dc = v.get("decision_comparison", {})
    oe = v.get("outcome_evaluation", {})
    vd = v.get("verdict", {})
    cs = v.get("case_summary", {})
    mb = oe.get("metrics_before", {})
    ma = oe.get("metrics_after", {})

    drift = oe.get("drift_score", 0.0)
    d_color = "#2d7a3a" if drift >= 0.75 else "#b8860b" if drift >= 0.5 else "#b0432c"

    def _bool_badge(val: bool, yes: str = "Yes", no: str = "No") -> str:
        color = "#2d7a3a" if val else "#b0432c"
        label = yes if val else no
        return f'<span style="background:{color};color:white;padding:4px 10px;border-radius:8px;">{escape(label)}</span>'

    def _metric_row(label: str, before: object, after: object) -> str:
        return f"<tr><td>{escape(label)}</td><td>{escape(str(before))}</td><td>{escape(str(after))}</td></tr>"

    metrics_table = (
        _metric_row("Latency P95 (ms)", mb.get("latency_p95_ms", "-"), ma.get("latency_p95_ms", "-"))
        + _metric_row("Error Rate", mb.get("error_rate", "-"), ma.get("error_rate", "-"))
        + _metric_row("Retry Rate", mb.get("retry_rate", "-"), ma.get("retry_rate", "-"))
    )

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Verdict - {escape(v.get("scenario_id", ""))}</title>
  <style>
    :root {{ --bg:#f6f1e8; --ink:#18222f; --accent:#c65d2e; --card:#fffaf1; --line:#d9c8ac; --green:#2d7a3a; --red:#b0432c; }}
    body {{ font-family: Georgia, serif; margin:0; background:radial-gradient(circle at top, #fff6e7, var(--bg)); color:var(--ink); }}
    .wrap {{ max-width:1020px; margin:0 auto; padding:32px; }}
    h1 {{ margin-bottom:4px; }}
    .subtitle {{ color:#666; margin-bottom:24px; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:18px; padding:20px; box-shadow:0 12px 32px rgba(24,34,47,0.06); }}
    .card h2 {{ font-size:1.1rem; margin:0 0 12px 0; color:var(--accent); }}
    .full {{ grid-column: 1 / -1; }}
    table {{ width:100%; border-collapse:collapse; margin:8px 0; }}
    th, td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); }}
    th {{ font-weight:600; }}
    .drift {{ font-size:1.4rem; font-weight:700; }}
    .narrative {{ line-height:1.6; font-style:italic; background:#f0e8d8; padding:14px; border-radius:12px; margin-top:10px; }}
    .vs {{ display:flex; gap:16px; align-items:center; margin:8px 0; }}
    .vs-item {{ flex:1; padding:12px; border-radius:12px; text-align:center; }}
    .vs-wrong {{ background:#fde8e4; border:2px solid var(--red); }}
    .vs-right {{ background:#e4f5e8; border:2px solid var(--green); }}
    .arrow {{ font-size:1.5rem; color:#999; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Incident Verdict</h1>
    <p class="subtitle">Scenario: <strong>{escape(v.get("scenario_id", ""))}</strong> &middot;
       Mode: <strong>{escape(v.get("mode", ""))}</strong> &middot;
       {escape(v.get("timestamp", "")[:19])}</p>

    <div class="grid">

      <div class="card">
        <h2>Predicted vs Actual</h2>
        <table>
          <tr><th>Metric</th><th>Before</th><th>After</th></tr>
          {metrics_table}
        </table>
        <div class="drift" style="color:{d_color}; margin-top:8px;">
          Drift Score: {drift:.2f}
        </div>
        <div>Recovery: <strong>{escape(oe.get("recovery_class", "-"))}</strong></div>
      </div>

      <div class="card">
        <h2>Dangerous Reflex Rejected</h2>
        <div>{_bool_badge(vd.get("dangerous_reflex_rejected", False), "Rejected", "Not rejected")}</div>
        <div class="vs">
          <div class="vs-item vs-wrong">
            <div style="font-size:0.8rem;color:var(--red);">Baseline chose</div>
            <div><strong>{escape(dc.get("baseline_chose", "-"))}</strong></div>
            <div style="font-size:0.8rem;">Correct: {_bool_badge(dc.get("baseline_correct", False))}</div>
          </div>
          <div class="arrow">&rarr;</div>
          <div class="vs-item vs-right">
            <div style="font-size:0.8rem;color:var(--green);">Guardrail chose</div>
            <div><strong>{escape(dc.get("stratus_chose", "-"))}</strong></div>
            <div style="font-size:0.8rem;">Correct: {_bool_badge(dc.get("stratus_correct", False))}</div>
          </div>
        </div>
        <p style="font-size:0.9rem;">{escape(vd.get("reflex_rejected_reason", ""))}</p>
      </div>

      <div class="card">
        <h2>Blast Radius Avoided</h2>
        <div style="font-size:1.6rem; text-align:center; margin:16px 0;">
          {escape(vd.get("blast_radius_avoided", "-"))}
        </div>
        <div>Fairness preserved: {_bool_badge(vd.get("fairness_preserved", False))}</div>
        <div>Safe action chosen: {_bool_badge(vd.get("safe_action_chosen", False))}</div>
      </div>

      <div class="card">
        <h2>Case Saved</h2>
        <div>Case ID: <code>{escape(str(v.get("case_id", "-")))}</code></div>
        <div style="margin-top:8px;">Classification: <strong>{escape(vd.get("classification", "-"))}</strong></div>
        <div style="margin-top:8px;">Chosen action: <strong>{escape(cs.get("chosen_action", "-"))}</strong></div>
      </div>

      <div class="card full">
        <h2>Narrative Verdict</h2>
        <div class="narrative">
          {escape(vd.get("narrative", "No narrative generated."))}
        </div>
      </div>

    </div>
  </div>
</body>
</html>"""


def _verdict_empty_page(scenario_id: str | None = None) -> str:
    """Render a page when no verdict is available."""
    msg = f"No verdict available for scenario '{escape(scenario_id)}'." if scenario_id else "No verdict available."
    return f"""<!doctype html>
<html>
<head><meta charset="utf-8"><title>No Verdict</title>
<style>body {{ font-family: Georgia, serif; text-align:center; padding:80px; background:#f6f1e8; color:#18222f; }}</style>
</head>
<body>
  <h1>No Verdict Available</h1>
  <p>{msg}</p>
  <p>Run the pipeline first:</p>
  <code>.venv/bin/python run.py alerts/latest.json --phase auto --scenario retry_death_spiral</code>
</body>
</html>"""


def _toggle_form(flag_name: str, enabled: bool) -> str:
    next_value = "false" if enabled else "true"
    label = "Disable" if enabled else "Enable"
    return f"""<form method="post" action="/api/toggle" style="margin-bottom:10px;">
      <input type="hidden" name="flag_name" value="{flag_name}">
      <input type="hidden" name="enabled" value="{next_value}">
      <button>{label} {flag_name}</button>
    </form>"""
