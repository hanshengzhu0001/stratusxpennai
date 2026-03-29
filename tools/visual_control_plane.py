from __future__ import annotations

from html import escape

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


def _toggle_form(flag_name: str, enabled: bool) -> str:
    next_value = "false" if enabled else "true"
    label = "Disable" if enabled else "Enable"
    return f"""<form method="post" action="/api/toggle" style="margin-bottom:10px;">
      <input type="hidden" name="flag_name" value="{flag_name}">
      <input type="hidden" name="enabled" value="{next_value}">
      <button>{label} {flag_name}</button>
    </form>"""
