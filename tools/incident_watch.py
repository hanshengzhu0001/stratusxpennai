from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

WATCH_STATE_PATH = Path("state/incident_watch.json")
DEFAULT_WATCH_STATE = {
    "status": "idle",
    "armed": False,
    "pending_incident": None,
    "active_incident": None,
    "last_completed_incident": None,
    "last_seen_alert_mtime_ns": 0,
    "last_acknowledged_alert_mtime_ns": 0,
}


def load_watch_state() -> dict:
    if not WATCH_STATE_PATH.exists():
        save_watch_state(DEFAULT_WATCH_STATE)
    raw = json.loads(WATCH_STATE_PATH.read_text())
    merged = dict(DEFAULT_WATCH_STATE)
    merged.update(raw)
    if merged != raw:
        save_watch_state(merged)
    return merged


def save_watch_state(state: dict) -> None:
    WATCH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCH_STATE_PATH.write_text(json.dumps(state, indent=2))


def watch_for_pending_incident(
    alert_path: str | Path = "alerts/latest.json",
    timeout_seconds: float = 0.0,
    poll_interval_seconds: float = 2.0,
) -> dict:
    path = Path(alert_path)
    state = load_watch_state()
    if state.get("status") in {"idle", "completed"} and not state.get("pending_incident") and not state.get("active_incident"):
        baseline_mtime = path.stat().st_mtime_ns if path.exists() else 0
        state["last_acknowledged_alert_mtime_ns"] = max(
            int(state.get("last_acknowledged_alert_mtime_ns", 0)),
            int(baseline_mtime),
        )
    state["armed"] = True
    if state.get("status") in {"idle", "completed"}:
        state["status"] = "armed"
    save_watch_state(state)

    deadline = time.monotonic() + timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
    while True:
        current = _pending_incident_from_path(path)
        state = load_watch_state()
        if current and _is_new_incident(current, state):
            state["status"] = "pending"
            state["pending_incident"] = current
            state["last_seen_alert_mtime_ns"] = current["alert_mtime_ns"]
            save_watch_state(state)
            return state
        if deadline is not None and time.monotonic() >= deadline:
            state["status"] = "armed"
            save_watch_state(state)
            return state
        time.sleep(max(0.25, poll_interval_seconds))


def acknowledge_pending_incident(source: str = "openclaw_demo_start") -> dict:
    state = load_watch_state()
    pending = state.get("pending_incident")
    if not pending:
        return state
    active = dict(pending)
    active["status"] = "acknowledged"
    active["acknowledged_at_utc"] = _utc_now()
    active["acknowledged_by"] = source
    state["status"] = "acknowledged"
    state["active_incident"] = active
    state["pending_incident"] = None
    state["last_acknowledged_alert_mtime_ns"] = int(active.get("alert_mtime_ns", 0))
    save_watch_state(state)
    return state


def complete_active_incident(outcome: str, report_path: str | None = None) -> dict:
    state = load_watch_state()
    active = state.get("active_incident")
    if not active:
        state["status"] = "armed" if state.get("armed") else "idle"
        save_watch_state(state)
        return state
    completed = dict(active)
    completed["status"] = "completed"
    completed["completed_at_utc"] = _utc_now()
    completed["outcome"] = outcome
    if report_path:
        completed["report_path"] = report_path
    state["status"] = "armed" if state.get("armed") else "completed"
    state["last_completed_incident"] = completed
    state["active_incident"] = None
    save_watch_state(state)
    return state


def clear_watch_state() -> dict:
    save_watch_state(DEFAULT_WATCH_STATE)
    return load_watch_state()


def _pending_incident_from_path(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    firing_alerts = [
        alert
        for alert in payload.get("alerts", [])
        if str(alert.get("status", payload.get("status", "firing"))).lower() == "firing"
    ]
    if not firing_alerts and str(payload.get("status", "")).lower() != "firing":
        return None
    mtime_ns = path.stat().st_mtime_ns
    summary = _incident_summary(payload, firing_alerts)
    fingerprint = _incident_fingerprint(payload, mtime_ns)
    return {
        "fingerprint": fingerprint,
        "summary": summary,
        "detected_at_utc": _utc_now(),
        "alert_path": str(path),
        "alert_mtime_ns": mtime_ns,
        "alert_count": len(firing_alerts) or len(payload.get("alerts", [])),
        "source": "alertmanager_webhook_file",
        "services": sorted(
            {
                alert.get("labels", {}).get("service", "unknown")
                for alert in (firing_alerts or payload.get("alerts", []))
            }
        ),
        "alertnames": sorted(
            {
                alert.get("labels", {}).get("alertname", "unknown")
                for alert in (firing_alerts or payload.get("alerts", []))
            }
        ),
        "status": "pending",
    }


def _is_new_incident(pending: dict, state: dict) -> bool:
    pending_mtime = int(pending.get("alert_mtime_ns", 0))
    existing_pending = state.get("pending_incident") or {}
    if existing_pending.get("alert_mtime_ns") == pending_mtime:
        return False
    if pending_mtime <= int(state.get("last_acknowledged_alert_mtime_ns", 0)):
        return False
    return True


def _incident_summary(payload: dict, alerts: list[dict]) -> str:
    if alerts:
        for alert in alerts:
            summary = alert.get("annotations", {}).get("summary")
            if summary:
                return str(summary)
    return str(payload.get("commonAnnotations", {}).get("summary") or payload.get("status", "Firing incident"))


def _incident_fingerprint(payload: dict, mtime_ns: int) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8") + str(mtime_ns).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
