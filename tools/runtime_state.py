from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from tools.scenario_catalog import default_state_for_scenario
from tools.simulation import (
    advance_simulation_state,
    apply_action_to_simulation,
    compute_metrics,
    current_constraints_snapshot,
    start_incident_simulation,
)

STATE_PATH = Path("state/runtime_state.json")
DEFAULT_STATE = default_state_for_scenario("retry_death_spiral")


def load_state() -> dict:
    if not STATE_PATH.exists():
        save_state(DEFAULT_STATE)
    state = json.loads(STATE_PATH.read_text())
    merged = dict(DEFAULT_STATE)
    merged.update(state)
    if "simulation" not in merged:
        merged["simulation"] = DEFAULT_STATE["simulation"]
    else:
        simulation_defaults = DEFAULT_STATE["simulation"]
        sim = dict(simulation_defaults)
        sim.update(merged["simulation"])
        merged["simulation"] = sim
    merged = advance_simulation_state(merged)
    if merged != state:
        save_state(merged)
    return merged


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(state, indent=2)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=STATE_PATH.parent,
        prefix=f".{STATE_PATH.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
        tmp_path = Path(handle.name)
    tmp_path.replace(STATE_PATH)


def reset_state() -> dict:
    active_scenario = load_state().get("active_scenario", DEFAULT_STATE["active_scenario"])
    save_state(default_state_for_scenario(active_scenario))
    return load_state()


def set_scenario(scenario_id: str) -> dict:
    save_state(default_state_for_scenario(scenario_id))
    return load_state()


def trigger_scenario(scenario_id: str, seed: int = 0) -> dict:
    state = default_state_for_scenario(scenario_id)
    state = start_incident_simulation(state, scenario_id, seed=seed)
    save_state(state)
    return load_state()


def ensure_alert_scenario_active(alert_payload: dict) -> dict:
    scenario_id = (
        alert_payload.get("commonLabels", {}).get("scenario_id")
        or (alert_payload.get("alerts") or [{}])[0].get("labels", {}).get("scenario_id")
    )
    status = str(alert_payload.get("status", "firing")).lower()
    state = load_state()
    if not scenario_id or status != "firing":
        return state
    simulation = state.get("simulation", {})
    if state.get("active_scenario") == scenario_id and simulation.get("active"):
        return state
    return trigger_scenario(str(scenario_id))


def apply_action(action_id: str) -> dict:
    state = load_state()
    state = apply_action_to_simulation(state, action_id)
    state["last_action"] = action_id
    save_state(state)
    return state


def set_flag(flag_name: str, enabled: bool) -> dict:
    state = load_state()
    state[flag_name] = enabled
    simulation = state.get("simulation", {})
    controls = simulation.get("controls", {})
    if flag_name == "payment_service_unreachable":
        controls["force_dependency_degraded"] = enabled
        if enabled:
            simulation["active"] = True
            if not simulation.get("started_at_utc"):
                simulation["started_at_utc"] = datetime.now(timezone.utc).isoformat()
    elif flag_name == "loadgenerator_flood_homepage":
        controls["force_portal_surge"] = enabled
        if enabled:
            simulation["active"] = True
            if not simulation.get("started_at_utc"):
                simulation["started_at_utc"] = datetime.now(timezone.utc).isoformat()
    elif flag_name == "retry_rate_limit_enabled":
        controls["retry_throttle_factor"] = 0.34 if enabled else 1.0
    elif flag_name == "payment_circuit_breaker_enabled":
        controls["circuit_breaker_enabled"] = enabled
    elif flag_name == "retry_backoff_enabled":
        controls["retry_backoff_factor"] = 0.55 if enabled else 1.0
    elif flag_name == "traffic_shift_enabled":
        controls["traffic_shift_fraction"] = 0.15 if enabled else 0.0
    elif flag_name == "payment_feature_disabled":
        controls["online_scheduling_enabled"] = not enabled
    simulation["controls"] = controls
    state["simulation"] = simulation
    state = advance_simulation_state(state)
    save_state(state)
    return state


def derive_metrics(state: dict) -> dict:
    return compute_metrics(state)


def current_constraints(state: dict | None = None) -> dict:
    return current_constraints_snapshot(state or load_state())


def control_plane_urls(base_url: str) -> dict:
    base = base_url.rstrip("/")
    return {
        "dashboard": f"{base}/",
        "feature_flags": f"{base}/feature-flags",
        "state_api": f"{base}/api/state",
    }
