from __future__ import annotations

import json
from pathlib import Path

from tools.scenario_catalog import default_state_for_scenario

STATE_PATH = Path("state/runtime_state.json")
DEFAULT_STATE = default_state_for_scenario("retry_death_spiral")


def load_state() -> dict:
    if not STATE_PATH.exists():
        save_state(DEFAULT_STATE)
    state = json.loads(STATE_PATH.read_text())
    merged = dict(DEFAULT_STATE)
    merged.update(state)
    if merged != state:
        save_state(merged)
    return merged


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def reset_state() -> dict:
    active_scenario = load_state().get("active_scenario", DEFAULT_STATE["active_scenario"])
    save_state(default_state_for_scenario(active_scenario))
    return load_state()


def set_scenario(scenario_id: str) -> dict:
    save_state(default_state_for_scenario(scenario_id))
    return load_state()


def apply_action(action_id: str) -> dict:
    state = load_state()
    if action_id == "rate_limit_retries":
        state["retry_rate_limit_enabled"] = True
    elif action_id == "enable_payment_circuit_breaker":
        state["payment_circuit_breaker_enabled"] = True
    elif action_id == "increase_retry_backoff":
        state["retry_backoff_enabled"] = True
    elif action_id == "restart_payment":
        state["payment_service_unreachable"] = False
    elif action_id == "disable_flag":
        state["payment_feature_disabled"] = True
    elif action_id == "shift_traffic":
        state["traffic_shift_enabled"] = True
    state["last_action"] = action_id
    save_state(state)
    return state


def set_flag(flag_name: str, enabled: bool) -> dict:
    state = load_state()
    state[flag_name] = enabled
    save_state(state)
    return state


def derive_metrics(state: dict) -> dict:
    if state["payment_feature_disabled"]:
        return {
            "latency_p95_ms": 400,
            "error_rate": 0.95,
            "retry_rate": 0.02,
        }

    latency = 900
    error_rate = 0.04
    retry_rate = 0.05

    scenario_id = state.get("active_scenario", DEFAULT_STATE["active_scenario"])
    if scenario_id == "payment_gateway_flap":
        latency += 280
        error_rate += 0.05
        retry_rate += 0.05
    elif scenario_id == "seat_hold_clog":
        latency += 320
        error_rate += 0.02
        retry_rate += 0.08
    elif scenario_id == "regional_saturation":
        latency += 250
        error_rate += 0.03
        retry_rate += 0.06

    if state["payment_service_unreachable"]:
        latency += 800
        error_rate += 0.09
        retry_rate += 0.16
    if state["loadgenerator_flood_homepage"]:
        latency += 600
        error_rate += 0.03
        retry_rate += 0.10
    if state["payment_circuit_breaker_enabled"]:
        latency -= 520
        error_rate -= 0.03
        retry_rate -= 0.20
    if state["retry_backoff_enabled"]:
        latency -= 280
        retry_rate -= 0.12
    if state["traffic_shift_enabled"]:
        latency -= 350
        error_rate -= 0.03
        retry_rate -= 0.03
    if state["retry_rate_limit_enabled"]:
        latency -= 650
        error_rate += 0.02
        retry_rate -= 0.21

    return {
        "latency_p95_ms": max(400, latency),
        "error_rate": round(max(0.0, min(1.0, error_rate)), 2),
        "retry_rate": round(max(0.0, min(1.0, retry_rate)), 2),
    }


def control_plane_urls(base_url: str) -> dict:
    base = base_url.rstrip("/")
    return {
        "dashboard": f"{base}/",
        "feature_flags": f"{base}/feature-flags",
        "state_api": f"{base}/api/state",
    }
