from __future__ import annotations

import math
import random
from copy import deepcopy
from datetime import datetime, timedelta, timezone


STEP_SECONDS = 5
MAX_HISTORY_POINTS = 120


SIMULATION_PROFILES = {
    "retry_death_spiral": {
        "trigger_label": "Open Flu Surge Telehealth Window",
        "scenario_family": "surge_retry_spiral",
        "demand": {
            "base_rate": 3.8,
            "peak_rate": 9.2,
            "rise_sec": 30,
            "plateau_sec": 120,
            "decay_sec": 240,
            "noise_sigma": 0.08,
        },
        "services": {
            "scheduling_capacity": 5.6,
            "eligibility_capacity": 5.0,
            "eligibility_health_floor": 0.58,
            "backlog_overload_coeff": 0.055,
        },
        "retry": {
            "base_multiplier": 0.72,
            "queue_sensitivity": 0.015,
            "hold_sensitivity": 0.25,
            "max_multiplier": 2.1,
        },
        "holds": {
            "capacity": 120,
            "initial": 18,
            "hold_fraction": 0.62,
            "ttl_sec": 210,
            "expiration_stress_coeff": 0.9,
        },
        "abandonment": {
            "base_hazard": 0.004,
            "wait_coeff": 0.0026,
        },
        "regional": {
            "primary_headroom": 0.38,
            "secondary_headroom": 0.31,
        },
        "latent": {
            "dependency_pressure": 0.54,
            "retry_pressure": 0.94,
            "hold_pressure": 0.28,
            "regional_pressure": 0.12,
        },
        "thresholds": {
            "early_retry_window_sec": 20,
            "late_retry_queue_depth": 42,
            "restart_safe_queue_depth": 12,
            "restart_safe_retry_rate": 0.16,
            "shift_secondary_floor": 0.24,
        },
    },
    "payment_gateway_flap": {
        "trigger_label": "Open Insurance-Verified Same-Day Visits",
        "scenario_family": "eligibility_flap",
        "demand": {
            "base_rate": 3.2,
            "peak_rate": 6.0,
            "rise_sec": 20,
            "plateau_sec": 90,
            "decay_sec": 180,
            "noise_sigma": 0.06,
        },
        "services": {
            "scheduling_capacity": 5.4,
            "eligibility_capacity": 4.8,
            "eligibility_health_floor": 0.52,
            "backlog_overload_coeff": 0.05,
        },
        "retry": {
            "base_multiplier": 0.46,
            "queue_sensitivity": 0.011,
            "hold_sensitivity": 0.18,
            "max_multiplier": 1.45,
        },
        "holds": {
            "capacity": 120,
            "initial": 16,
            "hold_fraction": 0.56,
            "ttl_sec": 180,
            "expiration_stress_coeff": 0.65,
        },
        "abandonment": {
            "base_hazard": 0.0034,
            "wait_coeff": 0.0022,
        },
        "regional": {
            "primary_headroom": 0.42,
            "secondary_headroom": 0.36,
        },
        "latent": {
            "dependency_pressure": 0.88,
            "retry_pressure": 0.34,
            "hold_pressure": 0.12,
            "regional_pressure": 0.08,
        },
        "thresholds": {
            "early_retry_window_sec": 25,
            "late_retry_queue_depth": 36,
            "restart_safe_queue_depth": 10,
            "restart_safe_retry_rate": 0.14,
            "shift_secondary_floor": 0.22,
        },
    },
    "seat_hold_clog": {
        "trigger_label": "Release High-Demand Specialist Slots",
        "scenario_family": "slot_hold_clog",
        "demand": {
            "base_rate": 2.8,
            "peak_rate": 6.8,
            "rise_sec": 25,
            "plateau_sec": 180,
            "decay_sec": 240,
            "noise_sigma": 0.07,
        },
        "services": {
            "scheduling_capacity": 5.0,
            "eligibility_capacity": 5.2,
            "eligibility_health_floor": 0.78,
            "backlog_overload_coeff": 0.045,
        },
        "retry": {
            "base_multiplier": 0.54,
            "queue_sensitivity": 0.013,
            "hold_sensitivity": 0.32,
            "max_multiplier": 1.6,
        },
        "holds": {
            "capacity": 85,
            "initial": 30,
            "hold_fraction": 0.76,
            "ttl_sec": 320,
            "expiration_stress_coeff": 1.2,
        },
        "abandonment": {
            "base_hazard": 0.0042,
            "wait_coeff": 0.0024,
        },
        "regional": {
            "primary_headroom": 0.41,
            "secondary_headroom": 0.35,
        },
        "latent": {
            "dependency_pressure": 0.24,
            "retry_pressure": 0.48,
            "hold_pressure": 0.96,
            "regional_pressure": 0.08,
        },
        "thresholds": {
            "early_retry_window_sec": 20,
            "late_retry_queue_depth": 30,
            "restart_safe_queue_depth": 10,
            "restart_safe_retry_rate": 0.12,
            "shift_secondary_floor": 0.22,
        },
    },
    "regional_saturation": {
        "trigger_label": "Divert Regional Overflow to Primary Portal",
        "scenario_family": "regional_access_saturation",
        "demand": {
            "base_rate": 3.4,
            "peak_rate": 7.5,
            "rise_sec": 35,
            "plateau_sec": 120,
            "decay_sec": 220,
            "noise_sigma": 0.05,
        },
        "services": {
            "scheduling_capacity": 5.1,
            "eligibility_capacity": 5.0,
            "eligibility_health_floor": 0.86,
            "backlog_overload_coeff": 0.048,
        },
        "retry": {
            "base_multiplier": 0.40,
            "queue_sensitivity": 0.01,
            "hold_sensitivity": 0.18,
            "max_multiplier": 1.35,
        },
        "holds": {
            "capacity": 120,
            "initial": 14,
            "hold_fraction": 0.5,
            "ttl_sec": 170,
            "expiration_stress_coeff": 0.55,
        },
        "abandonment": {
            "base_hazard": 0.0031,
            "wait_coeff": 0.0020,
        },
        "regional": {
            "primary_headroom": 0.17,
            "secondary_headroom": 0.31,
        },
        "latent": {
            "dependency_pressure": 0.24,
            "retry_pressure": 0.42,
            "hold_pressure": 0.18,
            "regional_pressure": 0.96,
        },
        "thresholds": {
            "early_retry_window_sec": 20,
            "late_retry_queue_depth": 38,
            "restart_safe_queue_depth": 11,
            "restart_safe_retry_rate": 0.13,
            "shift_secondary_floor": 0.24,
        },
    },
}


def profile_for_scenario(scenario_id: str | None) -> dict:
    key = scenario_id or "retry_death_spiral"
    return deepcopy(SIMULATION_PROFILES.get(key, SIMULATION_PROFILES["retry_death_spiral"]))


def default_simulation_state(scenario_id: str | None) -> dict:
    profile = profile_for_scenario(scenario_id)
    return {
        "active": False,
        "resolved": False,
        "scenario_family": profile["scenario_family"],
        "seed": 0,
        "step_seconds": STEP_SECONDS,
        "started_at_utc": None,
        "last_updated_at_utc": None,
        "time_sec": 0,
        "queue_depth": 8.0,
        "slot_holds": float(profile["holds"]["initial"]),
        "slot_hold_expired_recent": 0.0,
        "external_arrivals_recent": profile["demand"]["base_rate"],
        "retry_arrivals_recent": 0.12,
        "gated_arrivals_recent": 0.0,
        "completions_recent": 3.6,
        "abandoned_recent": 0.05,
        "effective_service_rate": min(
            profile["services"]["scheduling_capacity"],
            profile["services"]["eligibility_capacity"],
        ),
        "eligibility_health": 0.98,
        "manual_callback_queue": 0.0,
        "regional_headroom_primary": float(profile["regional"]["primary_headroom"]),
        "regional_headroom_secondary": float(profile["regional"]["secondary_headroom"]),
        "fairness_pressure": 0.05,
        "latent": deepcopy(profile.get("latent", {})),
        "thresholds": deepcopy(profile.get("thresholds", {})),
        "controls": {
            "retry_throttle_factor": 1.0,
            "circuit_breaker_enabled": False,
            "retry_backoff_factor": 1.0,
            "traffic_shift_fraction": 0.0,
            "online_scheduling_enabled": True,
            "slot_hold_ttl_factor": 1.0,
            "callback_routing_fraction": 0.0,
            "priority_slot_reserve_fraction": 0.0,
            "restart_outage_remaining_sec": 0,
            "restart_recovery_credit": 0.0,
            "force_dependency_degraded": False,
            "force_portal_surge": False,
            "activation_times": {},
        },
        "history": [],
        "derived_metrics": {
            "latency_p95_ms": 900,
            "error_rate": 0.04,
            "retry_rate": 0.05,
        },
        "derived_business_metrics": {
            "queue_abandonment_rate": 0.05,
            "payment_success_rate": 0.97,
            "retry_amplification_factor": 1.1,
            "fairness_skew": 0.04,
            "seat_hold_utilization": round(profile["holds"]["initial"] / profile["holds"]["capacity"], 2),
            "seat_hold_expiration_rate": 0.07,
        },
    }


def start_incident_simulation(state: dict, scenario_id: str, seed: int = 0) -> dict:
    profile = profile_for_scenario(scenario_id)
    simulation = default_simulation_state(scenario_id)
    now = _utc_now()
    rng = random.Random(f"{scenario_id}:{seed}:latent")
    latent = {
        key: round(_bounded(value + rng.uniform(-0.05, 0.05), 0.05, 1.2), 3)
        for key, value in profile.get("latent", {}).items()
    }
    secondary_low, secondary_high = (-0.015, 0.02)
    if scenario_id == "regional_saturation":
        secondary_low, secondary_high = (-0.04, 0.05)
    secondary_headroom = _bounded(
        profile["regional"]["secondary_headroom"] + rng.uniform(secondary_low, secondary_high),
        0.05,
        0.45,
    )
    primary_headroom = _bounded(
        profile["regional"]["primary_headroom"] + rng.uniform(-0.015, 0.015),
        0.03,
        0.45,
    )
    initial_queue_depth = {
        "retry_death_spiral": 30.0,
        "payment_gateway_flap": 18.0,
        "seat_hold_clog": 14.0,
        "regional_saturation": 12.0,
    }.get(scenario_id, 18.0)
    initial_service_fraction = {
        "retry_death_spiral": 0.60,
        "payment_gateway_flap": 0.66,
        "seat_hold_clog": 0.78,
        "regional_saturation": 0.74,
    }.get(scenario_id, 0.64)
    initial_extra_holds = {
        "retry_death_spiral": 12.0,
        "payment_gateway_flap": 10.0,
        "seat_hold_clog": 34.0,
        "regional_saturation": 8.0,
    }.get(scenario_id, 10.0)
    initial_fairness = {
        "retry_death_spiral": 0.12,
        "payment_gateway_flap": 0.10,
        "seat_hold_clog": 0.20,
        "regional_saturation": 0.12,
    }.get(scenario_id, 0.12)
    simulation.update(
        {
            "active": True,
            "resolved": False,
            "seed": seed,
            "started_at_utc": now,
            "last_updated_at_utc": now,
            "time_sec": 0,
            "queue_depth": initial_queue_depth,
            "slot_holds": float(profile["holds"]["initial"] + initial_extra_holds),
            "external_arrivals_recent": profile["demand"]["peak_rate"] * 0.72,
            "retry_arrivals_recent": profile["demand"]["peak_rate"] * 0.48,
            "completions_recent": min(
                profile["services"]["scheduling_capacity"],
                profile["services"]["eligibility_capacity"],
            ) * initial_service_fraction,
            "effective_service_rate": min(
                profile["services"]["scheduling_capacity"],
                profile["services"]["eligibility_capacity"],
            ) * initial_service_fraction,
            "eligibility_health": profile["services"]["eligibility_health_floor"],
            "manual_callback_queue": 0.0,
            "fairness_pressure": initial_fairness,
            "latent": latent,
            "thresholds": deepcopy(profile.get("thresholds", {})),
            "regional_headroom_primary": primary_headroom,
            "regional_headroom_secondary": secondary_headroom,
        }
    )
    warmup_seconds = {
        "retry_death_spiral": 15,
        "payment_gateway_flap": 10,
        "seat_hold_clog": 15,
        "regional_saturation": 10,
    }.get(scenario_id, 10)
    while simulation["time_sec"] < warmup_seconds:
        _step_simulation(scenario_id, simulation, STEP_SECONDS)
    simulation["started_at_utc"] = (datetime.fromisoformat(now) - timedelta(seconds=warmup_seconds)).astimezone(timezone.utc).isoformat()
    simulation["last_updated_at_utc"] = now
    state["active_scenario"] = scenario_id
    state["simulation"] = simulation
    return _sync_legacy_flags(_recompute_derived_metrics(state))


def advance_simulation_state(state: dict, now: datetime | None = None) -> dict:
    simulation = deepcopy(state.get("simulation") or {})
    if not simulation:
        state["simulation"] = default_simulation_state(state.get("active_scenario"))
        return _sync_legacy_flags(_recompute_derived_metrics(state))
    if not simulation.get("active"):
        state["simulation"] = simulation
        return _sync_legacy_flags(_recompute_derived_metrics(state))

    current = now or datetime.now(timezone.utc)
    started_at = _parse_utc(simulation.get("started_at_utc")) or current
    step_seconds = int(simulation.get("step_seconds", STEP_SECONDS))
    target_time_sec = max(0, int((current - started_at).total_seconds()))
    while simulation["time_sec"] + step_seconds <= target_time_sec:
        _step_simulation(state.get("active_scenario"), simulation, step_seconds)
    simulation["last_updated_at_utc"] = current.isoformat()
    state["simulation"] = simulation
    return _sync_legacy_flags(_recompute_derived_metrics(state))


def apply_action_to_simulation(state: dict, action_id: str) -> dict:
    simulation = deepcopy(state.get("simulation") or default_simulation_state(state.get("active_scenario")))
    controls = simulation.setdefault("controls", {})
    thresholds = simulation.get("thresholds") or profile_for_scenario(state.get("active_scenario")).get("thresholds", {})
    activation_times = controls.setdefault("activation_times", {})
    canonical_action = {
        "throttle_booking_retries": "rate_limit_retries",
        "enable_eligibility_circuit_breaker": "enable_payment_circuit_breaker",
        "restart_eligibility_service": "restart_payment",
        "shift_scheduling_traffic_10_percent": "shift_traffic",
        "shift_scheduling_traffic_25_percent": "shift_traffic",
        "disable_online_scheduling": "disable_flag",
    }.get(action_id, action_id)
    activation_times[canonical_action] = int(simulation.get("time_sec", 0))
    if canonical_action == "rate_limit_retries":
        controls["retry_throttle_factor"] = 0.34
        simulation["retry_arrivals_recent"] = max(0.02, simulation.get("retry_arrivals_recent", 0.0) * 0.18)
        if state.get("active_scenario") == "retry_death_spiral":
            simulation["queue_depth"] = max(0.0, simulation.get("queue_depth", 0.0) * 0.22)
            simulation["slot_holds"] = max(0.0, simulation.get("slot_holds", 0.0) * 0.46)
            simulation["fairness_pressure"] = max(0.02, simulation.get("fairness_pressure", 0.05) * 0.60)
        else:
            simulation["queue_depth"] = max(0.0, simulation.get("queue_depth", 0.0) * 0.55)
            simulation["slot_holds"] = max(0.0, simulation.get("slot_holds", 0.0) * 0.78)
            simulation["fairness_pressure"] = max(0.02, simulation.get("fairness_pressure", 0.05) * 0.88)
    elif canonical_action == "enable_payment_circuit_breaker":
        controls["circuit_breaker_enabled"] = True
    elif canonical_action == "increase_retry_backoff":
        controls["retry_backoff_factor"] = 0.55
        simulation["retry_arrivals_recent"] = max(0.02, simulation.get("retry_arrivals_recent", 0.0) * 0.78)
    elif canonical_action == "shorten_slot_hold_ttl":
        controls["slot_hold_ttl_factor"] = 0.42
        simulation["slot_holds"] = max(0.0, simulation.get("slot_holds", 0.0) * 0.48)
        simulation["queue_depth"] = max(0.0, simulation.get("queue_depth", 0.0) * 0.42)
        simulation["retry_arrivals_recent"] = max(0.02, simulation.get("retry_arrivals_recent", 0.0) * 0.52)
        simulation["fairness_pressure"] = max(0.02, simulation.get("fairness_pressure", 0.05) * 0.58)
    elif canonical_action == "route_to_callback_queue":
        controls["callback_routing_fraction"] = max(
            float(controls.get("callback_routing_fraction", 0.0)),
            0.34,
        )
        simulation["manual_callback_queue"] = simulation.get("manual_callback_queue", 0.0) + simulation.get("queue_depth", 0.0) * 0.08
        simulation["queue_depth"] = max(0.0, simulation.get("queue_depth", 0.0) * 0.58)
        simulation["retry_arrivals_recent"] = max(0.02, simulation.get("retry_arrivals_recent", 0.0) * 0.64)
        simulation["fairness_pressure"] = max(0.02, simulation.get("fairness_pressure", 0.05) * 0.76)
    elif canonical_action == "reserve_priority_slots":
        controls["priority_slot_reserve_fraction"] = max(
            float(controls.get("priority_slot_reserve_fraction", 0.0)),
            0.22,
        )
        simulation["slot_holds"] = max(0.0, simulation.get("slot_holds", 0.0) * 0.82)
        simulation["queue_depth"] = max(0.0, simulation.get("queue_depth", 0.0) * 0.82)
        simulation["fairness_pressure"] = max(0.02, simulation.get("fairness_pressure", 0.05) * 0.52)
    elif canonical_action == "restart_payment":
        retry_share = float(simulation.get("retry_arrivals_recent", 0.0)) / max(
            float(simulation.get("external_arrivals_recent", 0.0))
            + float(simulation.get("retry_arrivals_recent", 0.0)),
            0.1,
        )
        safe_restart = (
            float(simulation.get("queue_depth", 0.0))
            <= float(thresholds.get("restart_safe_queue_depth", 12))
            and retry_share <= float(thresholds.get("restart_safe_retry_rate", 0.16))
        )
        controls["restart_outage_remaining_sec"] = 15
        controls["restart_recovery_credit"] = 0.16 if safe_restart else 0.0
        simulation["eligibility_health"] = min(simulation.get("eligibility_health", 1.0), 0.18)
    elif canonical_action == "disable_flag":
        controls["online_scheduling_enabled"] = False
        controls["callback_routing_fraction"] = max(
            float(controls.get("callback_routing_fraction", 0.0)),
            0.58,
        )
    elif canonical_action == "shift_traffic":
        if action_id == "shift_scheduling_traffic_10_percent":
            controls["traffic_shift_fraction"] = 0.10
        elif action_id == "shift_scheduling_traffic_25_percent":
            controls["traffic_shift_fraction"] = 0.25
        else:
            controls["traffic_shift_fraction"] = 0.10
        projected_secondary = projected_secondary_headroom_from_state(
            {"active_scenario": state.get("active_scenario"), "simulation": simulation},
            shift_fraction=float(controls["traffic_shift_fraction"]),
        )
        safe_floor = float(thresholds.get("shift_secondary_floor", 0.24))
        if projected_secondary > safe_floor:
            simulation["queue_depth"] = max(0.0, simulation.get("queue_depth", 0.0) * 0.52)
            simulation["retry_arrivals_recent"] = max(0.02, simulation.get("retry_arrivals_recent", 0.0) * 0.52)
            simulation["fairness_pressure"] = max(0.02, simulation.get("fairness_pressure", 0.05) * 0.92)
    state["simulation"] = simulation
    return advance_simulation_state(state)


def compute_metrics(state: dict) -> dict:
    simulation = state.get("simulation") or default_simulation_state(state.get("active_scenario"))
    return deepcopy(simulation.get("derived_metrics") or {"latency_p95_ms": 900, "error_rate": 0.04, "retry_rate": 0.05})


def compute_business_metrics(state: dict) -> dict:
    simulation = state.get("simulation") or default_simulation_state(state.get("active_scenario"))
    return deepcopy(
        simulation.get("derived_business_metrics")
        or {
            "queue_abandonment_rate": 0.05,
            "payment_success_rate": 0.97,
            "retry_amplification_factor": 1.1,
            "fairness_skew": 0.04,
            "seat_hold_utilization": 0.45,
            "seat_hold_expiration_rate": 0.07,
        }
    )


def current_constraints_snapshot(state: dict) -> dict:
    simulation = state.get("simulation") or {}
    latent = simulation.get("latent") or {}
    projected_secondary = projected_secondary_headroom_from_state(state)
    return {
        "time_sec": simulation.get("time_sec", 0),
        "queue_depth": round(float(simulation.get("queue_depth", 0.0)), 2),
        "lambda_external": round(float(simulation.get("external_arrivals_recent", 0.0)), 3),
        "lambda_retry": round(float(simulation.get("retry_arrivals_recent", 0.0)), 3),
        "lambda_gated": round(float(simulation.get("gated_arrivals_recent", 0.0)), 3),
        "effective_service_rate": round(float(simulation.get("effective_service_rate", 0.0)), 3),
        "eligibility_health": round(float(simulation.get("eligibility_health", 0.0)), 3),
        "slot_holds": round(float(simulation.get("slot_holds", 0.0)), 2),
        "regional_headroom_primary": round(float(simulation.get("regional_headroom_primary", 0.0)), 3),
        "regional_headroom_secondary": round(float(simulation.get("regional_headroom_secondary", 0.0)), 3),
        "projected_secondary_headroom_after_shift": round(projected_secondary, 3),
        "manual_callback_queue": round(float(simulation.get("manual_callback_queue", 0.0)), 2),
        "fairness_pressure": round(float(simulation.get("fairness_pressure", 0.0)), 3),
        "dependency_pressure": round(float(latent.get("dependency_pressure", 0.0)), 3),
        "retry_pressure": round(float(latent.get("retry_pressure", 0.0)), 3),
        "hold_pressure": round(float(latent.get("hold_pressure", 0.0)), 3),
        "regional_pressure": round(float(latent.get("regional_pressure", 0.0)), 3),
        "slot_hold_ttl_factor": round(float(simulation.get("controls", {}).get("slot_hold_ttl_factor", 1.0)), 2),
        "callback_routing_fraction": round(float(simulation.get("controls", {}).get("callback_routing_fraction", 0.0)), 2),
        "priority_slot_reserve_fraction": round(float(simulation.get("controls", {}).get("priority_slot_reserve_fraction", 0.0)), 2),
    }


def projected_secondary_headroom_from_state(state: dict, shift_fraction: float | None = None) -> float:
    simulation = state.get("simulation") or {}
    profile = profile_for_scenario(state.get("active_scenario"))
    controls = simulation.get("controls") or {}
    latent = simulation.get("latent") or profile.get("latent", {})
    current_secondary = float(
        simulation.get(
            "regional_headroom_secondary",
            profile["regional"]["secondary_headroom"],
        )
    )
    shift_fraction = float(
        controls.get("traffic_shift_fraction", 0.0) if shift_fraction is None else shift_fraction
    )
    if shift_fraction <= 0.0:
        return current_secondary
    projected = current_secondary - shift_fraction * (
        0.12 + 0.18 * float(latent.get("regional_pressure", 0.5))
    )
    return _bounded(projected, 0.02, 0.45)


def _step_simulation(scenario_id: str | None, simulation: dict, dt: int) -> None:
    profile = profile_for_scenario(scenario_id)
    controls = simulation["controls"]
    latent = simulation.get("latent") or deepcopy(profile.get("latent", {}))
    thresholds = simulation.get("thresholds") or deepcopy(profile.get("thresholds", {}))
    activation_times = controls.setdefault("activation_times", {})
    step_index = int(simulation["time_sec"] // dt)
    rng = random.Random(f"{scenario_id}:{simulation['seed']}:{step_index}")

    lambda_base = _external_arrival_rate(profile["demand"], simulation["time_sec"])
    if controls.get("force_portal_surge"):
        lambda_base = max(lambda_base, profile["demand"]["peak_rate"] * 0.95)
    noise = math.exp(rng.gauss(0.0, profile["demand"]["noise_sigma"]))
    lambda_external = max(0.35, lambda_base * noise)
    callback_fraction = float(controls.get("callback_routing_fraction", 0.0))
    lambda_gated = lambda_external * (0.72 if not controls["online_scheduling_enabled"] else 0.0)
    lambda_gated += lambda_external * callback_fraction
    if controls["circuit_breaker_enabled"]:
        lambda_gated += lambda_external * (0.08 + 0.20 * latent.get("dependency_pressure", 0.5))
    lambda_external_local = max(0.0, lambda_external * (1.0 - controls["traffic_shift_fraction"]) - lambda_gated)

    hold_util = simulation["slot_holds"] / max(profile["holds"]["capacity"], 1)
    queue_pressure = simulation["queue_depth"] / 40.0
    retry_multiplier = profile["retry"]["base_multiplier"]
    retry_multiplier += (
        profile["retry"]["queue_sensitivity"]
        * simulation["queue_depth"]
        * (0.65 + 0.55 * latent.get("retry_pressure", 0.5))
    )
    retry_multiplier += (
        profile["retry"]["hold_sensitivity"]
        * hold_util
        * (0.45 + 0.75 * latent.get("hold_pressure", 0.5))
    )
    retry_multiplier += 0.12 * latent.get("dependency_pressure", 0.5) * max(
        0.0,
        1.0 - float(simulation.get("eligibility_health", 1.0)),
    )
    retry_multiplier = min(
        retry_multiplier,
        profile["retry"]["max_multiplier"] * (0.9 + 0.18 * latent.get("retry_pressure", 0.5)),
    )
    throttle_factor = float(controls["retry_throttle_factor"])
    early_throttle_active = False
    timely_retry_control = False
    if throttle_factor < 1.0:
        throttle_applied_at = int(activation_times.get("rate_limit_retries", simulation["time_sec"]))
        if throttle_applied_at <= int(thresholds.get("early_retry_window_sec", 20)):
            throttle_factor *= 0.58
            early_throttle_active = True
        elif scenario_id == "retry_death_spiral" and throttle_applied_at <= 90:
            throttle_factor *= 0.48
            timely_retry_control = True
        if simulation["queue_depth"] >= float(thresholds.get("late_retry_queue_depth", 40)):
            throttle_factor *= 1.10 if scenario_id == "retry_death_spiral" else 1.22
        if scenario_id == "retry_death_spiral":
            throttle_factor *= 0.62 if timely_retry_control else 0.84
    retry_multiplier *= throttle_factor
    backoff_factor = float(controls["retry_backoff_factor"])
    if backoff_factor < 1.0:
        backoff_applied_at = int(activation_times.get("increase_retry_backoff", simulation["time_sec"]))
        backoff_elapsed = max(0, simulation["time_sec"] - backoff_applied_at)
        if backoff_elapsed < 15:
            backoff_factor *= 0.94
        elif backoff_elapsed < 35:
            backoff_factor *= 0.80
        else:
            backoff_factor *= 0.68
    retry_multiplier *= backoff_factor
    if controls["circuit_breaker_enabled"]:
        retry_multiplier *= 0.18 + 0.55 * (1.0 - latent.get("dependency_pressure", 0.5))
    if controls.get("slot_hold_ttl_factor", 1.0) < 1.0:
        retry_multiplier *= 0.46
    if controls.get("priority_slot_reserve_fraction", 0.0) > 0.0:
        retry_multiplier *= 0.78
    if callback_fraction > 0.0:
        retry_multiplier *= max(0.22, 1.0 - callback_fraction * 1.6)
    if not controls["online_scheduling_enabled"]:
        retry_multiplier *= 0.10
    lambda_retry = lambda_external_local * max(0.02, retry_multiplier)
    blocked_retry_fraction = 0.0
    if controls["retry_throttle_factor"] < 1.0:
        throttle_applied_at = int(activation_times.get("rate_limit_retries", simulation["time_sec"]))
        blocked_retry_fraction += 0.38 if throttle_applied_at <= int(thresholds.get("early_retry_window_sec", 20)) else 0.12
        if scenario_id == "retry_death_spiral" and throttle_applied_at <= 90:
            blocked_retry_fraction += 0.28
    if callback_fraction > 0.0:
        blocked_retry_fraction += min(0.32, callback_fraction * 0.75)
    if blocked_retry_fraction > 0.0:
        lambda_gated += lambda_retry * blocked_retry_fraction
        lambda_retry *= max(0.25, 1.0 - blocked_retry_fraction)

    base_health = _eligibility_health(profile["services"], simulation["time_sec"])
    base_health = min(
        base_health,
        1.02 - 0.38 * latent.get("dependency_pressure", 0.5),
    )
    if controls.get("force_dependency_degraded"):
        base_health = min(base_health, profile["services"]["eligibility_health_floor"])
    if controls["restart_outage_remaining_sec"] > 0:
        base_health = min(base_health, 0.18)
        controls["restart_outage_remaining_sec"] = max(0, controls["restart_outage_remaining_sec"] - dt)
    elif controls.get("restart_recovery_credit", 0.0) > 0:
        recovery_credit = float(controls["restart_recovery_credit"])
        base_health = min(1.0, base_health + recovery_credit)
        controls["restart_recovery_credit"] = max(0.0, recovery_credit - 0.03)
    elif simulation.get("time_sec", 0) > 30 and not controls["circuit_breaker_enabled"] and scenario_id == "payment_gateway_flap":
        base_health = min(1.0, base_health + 0.05)
    if early_throttle_active and scenario_id == "retry_death_spiral":
        base_health = min(1.0, base_health + 0.12)
    elif timely_retry_control and scenario_id == "retry_death_spiral":
        base_health = min(1.0, base_health + 0.11)

    overload_coeff = profile["services"]["backlog_overload_coeff"] * (
        1.0
        + 0.35 * latent.get("retry_pressure", 0.5)
        + 0.25 * hold_util * latent.get("hold_pressure", 0.5)
    )
    if controls["circuit_breaker_enabled"]:
        overload_coeff *= max(0.24, 0.52 - 0.24 * latent.get("dependency_pressure", 0.5))
    if controls["retry_throttle_factor"] < 1.0:
        throttle_applied_at = int(activation_times.get("rate_limit_retries", simulation["time_sec"]))
        if throttle_applied_at <= int(thresholds.get("early_retry_window_sec", 20)):
            overload_coeff *= 0.22
        elif scenario_id == "retry_death_spiral" and throttle_applied_at <= 90:
            overload_coeff *= 0.22
        else:
            overload_coeff *= 0.48
    if controls["retry_backoff_factor"] < 1.0:
        backoff_applied_at = int(activation_times.get("increase_retry_backoff", simulation["time_sec"]))
        overload_coeff *= 0.78 if simulation["time_sec"] - backoff_applied_at < 15 else 0.55
    if callback_fraction > 0.0:
        overload_coeff *= max(0.42, 1.0 - callback_fraction * 1.15)
    if controls["traffic_shift_fraction"] > 0:
        projected_secondary = projected_secondary_headroom_from_state(
            {"active_scenario": scenario_id, "simulation": simulation},
            shift_fraction=float(controls["traffic_shift_fraction"]),
        )
        shift_safe_floor = float(thresholds.get("shift_secondary_floor", 0.24))
        if projected_secondary > shift_safe_floor:
            shift_margin = (projected_secondary - shift_safe_floor) / max(0.05, 1.0 - shift_safe_floor)
            overload_coeff *= max(0.72, 1.0 - controls["traffic_shift_fraction"] * (0.90 + 0.55 * shift_margin))
    overload_penalty = 1.0 / (1.0 + overload_coeff * simulation["queue_depth"])
    effective_eligibility_rate = profile["services"]["eligibility_capacity"] * max(profile["services"]["eligibility_health_floor"], base_health) * overload_penalty
    if early_throttle_active and scenario_id == "retry_death_spiral":
        effective_eligibility_rate = max(
            effective_eligibility_rate,
            profile["services"]["eligibility_capacity"] * 0.72,
        )
    elif timely_retry_control and scenario_id == "retry_death_spiral":
        effective_eligibility_rate = max(
            effective_eligibility_rate,
            profile["services"]["eligibility_capacity"] * 0.72,
        )
    effective_scheduling_rate = profile["services"]["scheduling_capacity"]
    if controls["circuit_breaker_enabled"]:
        effective_eligibility_rate = max(
            effective_eligibility_rate * (1.08 + 0.22 * latent.get("dependency_pressure", 0.5)),
            profile["services"]["eligibility_capacity"] * (0.70 + 0.18 * latent.get("dependency_pressure", 0.5)),
        )
    inventory_penalty = 1.0 - min(
        0.76,
        hold_util * (0.36 + 0.42 * latent.get("hold_pressure", 0.5)),
    )
    if controls.get("slot_hold_ttl_factor", 1.0) < 1.0:
        inventory_penalty += min(0.26, (1.0 - controls["slot_hold_ttl_factor"]) * 0.38)
    if controls.get("priority_slot_reserve_fraction", 0.0) > 0.0:
        inventory_penalty += min(0.12, controls["priority_slot_reserve_fraction"] * 0.18)
    if callback_fraction > 0.0:
        inventory_penalty += min(0.14, callback_fraction * 0.22)
    if early_throttle_active and scenario_id == "retry_death_spiral":
        inventory_penalty += 0.08
    elif timely_retry_control and scenario_id == "retry_death_spiral":
        inventory_penalty += 0.18
    effective_scheduling_rate *= max(0.18, inventory_penalty)
    if controls.get("slot_hold_ttl_factor", 1.0) < 1.0:
        effective_scheduling_rate *= 1.62
    if callback_fraction > 0.0:
        effective_scheduling_rate *= 1.0 + min(0.22, callback_fraction * 0.60)
    if controls.get("priority_slot_reserve_fraction", 0.0) > 0.0:
        effective_scheduling_rate *= 1.0 + min(0.16, controls["priority_slot_reserve_fraction"] * 0.55)
    if early_throttle_active and scenario_id == "retry_death_spiral":
        effective_scheduling_rate *= 1.12
    elif timely_retry_control and scenario_id == "retry_death_spiral":
        effective_scheduling_rate *= 1.26

    overflow_penalty = 0.0
    if controls["traffic_shift_fraction"] > 0:
        secondary_safe_floor = float(thresholds.get("shift_secondary_floor", 0.24))
        target_secondary = max(
            0.02,
            profile["regional"]["secondary_headroom"]
            - controls["traffic_shift_fraction"] * (0.12 + 0.18 * latent.get("regional_pressure", 0.5)),
        )
        if target_secondary < secondary_safe_floor:
            overflow_penalty = (secondary_safe_floor - target_secondary) / max(secondary_safe_floor, 0.01)
            lambda_external_local += lambda_external * overflow_penalty * (0.22 + 0.28 * latent.get("regional_pressure", 0.5))
        else:
            safety_margin = (target_secondary - secondary_safe_floor) / max(0.05, 1.0 - secondary_safe_floor)
            lambda_retry *= max(0.42, 1.0 - controls["traffic_shift_fraction"] * (2.10 + 1.10 * safety_margin))
            effective_scheduling_rate *= 1.0 + min(0.55, controls["traffic_shift_fraction"] * (2.40 + 1.20 * safety_margin))
    effective_service_rate = min(effective_scheduling_rate, effective_eligibility_rate)

    arrivals = (lambda_external_local + lambda_retry) * dt
    queue_before_service = simulation["queue_depth"] + arrivals
    wait_proxy = queue_before_service / max(effective_service_rate, 0.2)
    abandon_hazard = profile["abandonment"]["base_hazard"] + profile["abandonment"]["wait_coeff"] * wait_proxy
    if not controls["online_scheduling_enabled"]:
        abandon_hazard += 0.006
    if callback_fraction > 0.0:
        abandon_hazard *= max(0.48, 1.0 - callback_fraction * 0.80)
    if controls.get("slot_hold_ttl_factor", 1.0) < 1.0:
        abandon_hazard *= 0.40
    if controls.get("priority_slot_reserve_fraction", 0.0) > 0.0:
        abandon_hazard *= 0.74
    if timely_retry_control and scenario_id == "retry_death_spiral":
        abandon_hazard *= 0.32
    if overflow_penalty > 0.0:
        abandon_hazard += 0.008 * overflow_penalty
    abandoned = min(queue_before_service * abandon_hazard * dt * 0.18, queue_before_service * 0.24)
    completions = min(max(queue_before_service - abandoned, 0.0), effective_service_rate * dt)
    simulation["queue_depth"] = max(0.0, queue_before_service - completions - abandoned)

    effective_ttl = max(45.0, profile["holds"]["ttl_sec"] * float(controls.get("slot_hold_ttl_factor", 1.0)))
    hold_fraction = profile["holds"]["hold_fraction"] * (1.0 + 0.45 * latent.get("hold_pressure", 0.5))
    hold_fraction *= max(0.45, 1.0 - callback_fraction * 0.75)
    if controls.get("slot_hold_ttl_factor", 1.0) < 1.0:
        hold_fraction *= max(0.22, 1.0 - (1.0 - controls["slot_hold_ttl_factor"]) * 0.90)
    hold_fraction *= max(0.62, 1.0 - controls.get("priority_slot_reserve_fraction", 0.0) * 0.32)
    if early_throttle_active and scenario_id == "retry_death_spiral":
        hold_fraction *= 0.80
    elif timely_retry_control and scenario_id == "retry_death_spiral":
        hold_fraction *= 0.56
    hold_inflow = min(
        max(0.0, profile["holds"]["capacity"] - simulation["slot_holds"]),
        lambda_external_local * hold_fraction * dt,
    )
    hold_expire = min(
        simulation["slot_holds"] + hold_inflow,
        simulation["slot_holds"]
        * (dt / effective_ttl)
        * (1.0 + profile["holds"]["expiration_stress_coeff"] * queue_pressure + 0.35 * latent.get("hold_pressure", 0.5)),
    )
    if controls.get("slot_hold_ttl_factor", 1.0) < 1.0:
        hold_expire = min(
            simulation["slot_holds"] + hold_inflow,
            hold_expire * (1.55 + 1.55 * (1.0 - controls["slot_hold_ttl_factor"])),
        )
    hold_release = min(simulation["slot_holds"] + hold_inflow - hold_expire, completions * 0.92)
    if controls.get("priority_slot_reserve_fraction", 0.0) > 0.0:
        hold_release = min(
            simulation["slot_holds"] + hold_inflow - hold_expire,
            hold_release * (1.08 + controls["priority_slot_reserve_fraction"] * 0.42),
        )
    simulation["slot_holds"] = max(0.0, simulation["slot_holds"] + hold_inflow - hold_expire - hold_release)

    if controls["traffic_shift_fraction"] > 0:
        target_secondary = max(
            0.02,
            profile["regional"]["secondary_headroom"]
            - controls["traffic_shift_fraction"] * (0.12 + 0.18 * latent.get("regional_pressure", 0.5)),
        )
        simulation["regional_headroom_secondary"] = max(
            0.02,
            simulation["regional_headroom_secondary"]
            + 0.35 * (target_secondary - simulation["regional_headroom_secondary"]),
        )
    else:
        simulation["regional_headroom_secondary"] = min(
            profile["regional"]["secondary_headroom"],
            simulation["regional_headroom_secondary"] + 0.004,
        )
    target_primary = max(
        0.03,
        profile["regional"]["primary_headroom"]
        - min(0.20, lambda_external_local / 40.0)
        + controls["traffic_shift_fraction"] * 0.05,
    )
    simulation["regional_headroom_primary"] = max(
        0.03,
        simulation["regional_headroom_primary"] + 0.35 * (target_primary - simulation["regional_headroom_primary"]),
    )
    simulation["manual_callback_queue"] = max(
        0.0,
        simulation["manual_callback_queue"] + lambda_gated * dt - (1.1 + callback_fraction * 0.8) * dt,
    )

    fairness = 0.03
    fairness += min(0.16, lambda_retry / max(lambda_external_local + 0.2, 1.0) * 0.14)
    fairness += min(
        0.22,
        (simulation["slot_holds"] / profile["holds"]["capacity"])
        * (0.12 + 0.10 * latent.get("hold_pressure", 0.5)),
    )
    fairness += min(0.08, simulation["manual_callback_queue"] / 85.0)
    if scenario_id == "seat_hold_clog":
        fairness += 0.07
    if controls.get("priority_slot_reserve_fraction", 0.0) > 0.0:
        fairness -= min(0.22, controls["priority_slot_reserve_fraction"] * 0.72)
    if controls.get("slot_hold_ttl_factor", 1.0) < 1.0:
        fairness -= min(0.26, (1.0 - controls["slot_hold_ttl_factor"]) * 0.42)
    if callback_fraction > 0.0:
        fairness -= min(0.18, callback_fraction * 0.48)
    if overflow_penalty > 0.0:
        fairness += min(0.10, overflow_penalty * 0.18)
    if not controls["online_scheduling_enabled"]:
        fairness += 0.04
    simulation["fairness_pressure"] = min(0.38, fairness)
    simulation["time_sec"] += dt
    simulation["external_arrivals_recent"] = lambda_external_local
    simulation["retry_arrivals_recent"] = lambda_retry
    simulation["gated_arrivals_recent"] = lambda_gated
    simulation["completions_recent"] = completions / dt
    simulation["abandoned_recent"] = abandoned / dt
    simulation["slot_hold_expired_recent"] = hold_expire / dt
    simulation["effective_service_rate"] = effective_service_rate
    simulation["eligibility_health"] = max(profile["services"]["eligibility_health_floor"], base_health)
    _recompute_simulation_outputs(simulation, profile)
    _append_history(simulation)


def _recompute_derived_metrics(state: dict) -> dict:
    simulation = state["simulation"]
    profile = profile_for_scenario(state.get("active_scenario"))
    _recompute_simulation_outputs(simulation, profile)
    return state


def _recompute_simulation_outputs(simulation: dict, profile: dict) -> None:
    queue_depth = float(simulation["queue_depth"])
    lambda_external = float(simulation["external_arrivals_recent"])
    lambda_retry = float(simulation["retry_arrivals_recent"])
    effective_service_rate = max(float(simulation["effective_service_rate"]), 0.2)
    hold_util = float(simulation["slot_holds"]) / max(profile["holds"]["capacity"], 1)
    wait_proxy = queue_depth / effective_service_rate
    latent = simulation.get("latent", {})
    latency = (
        560
        + 240 * math.log1p(wait_proxy * 1.2)
        + 420
        * (1.0 - float(simulation["eligibility_health"]))
        * (0.6 + 0.5 * latent.get("dependency_pressure", 0.5))
        + 280 * hold_util * (0.5 + 0.6 * latent.get("hold_pressure", 0.5))
        + 55 * lambda_retry
        + min(180, float(simulation.get("manual_callback_queue", 0.0)) * 4.5)
    )
    error_rate = 0.02 + 0.10 * (1.0 - float(simulation["eligibility_health"])) + 0.018 * min(4.0, wait_proxy / 4.0)
    if not simulation["controls"]["online_scheduling_enabled"]:
        error_rate += 0.02
    if float(simulation.get("regional_headroom_secondary", 1.0)) < 0.18 and simulation["controls"].get("traffic_shift_fraction", 0.0) > 0.0:
        error_rate += 0.03
    retry_rate = lambda_retry / max(lambda_external + lambda_retry + simulation["gated_arrivals_recent"], 0.1)

    completion_rate = simulation["completions_recent"] / max(lambda_external + simulation["gated_arrivals_recent"], 0.1)
    abandonment_rate = simulation["abandoned_recent"] / max(lambda_external + lambda_retry, 0.1)
    seat_hold_expiration_rate = simulation["slot_hold_expired_recent"] / max(float(simulation["slot_holds"]) + 1.0, 1.0)

    simulation["derived_metrics"] = {
        "latency_p95_ms": int(max(450, min(3200, round(latency)))),
        "error_rate": round(max(0.0, min(1.0, error_rate)), 2),
        "retry_rate": round(max(0.0, min(1.0, retry_rate)), 2),
    }
    simulation["derived_business_metrics"] = {
        "queue_abandonment_rate": round(max(0.02, min(0.35, abandonment_rate)), 2),
        "payment_success_rate": round(max(0.06, min(0.99, completion_rate)), 2),
        "retry_amplification_factor": round(1.0 + lambda_retry / max(lambda_external, 0.2), 1),
        "fairness_skew": round(max(0.02, min(0.4, simulation["fairness_pressure"])), 2),
        "seat_hold_utilization": round(max(0.08, min(0.99, hold_util)), 2),
        "seat_hold_expiration_rate": round(max(0.01, min(0.6, seat_hold_expiration_rate)), 2),
        "manual_callback_queue_depth": round(max(0.0, float(simulation.get("manual_callback_queue", 0.0))), 1),
        "secondary_headroom": round(max(0.0, min(1.0, float(simulation.get("regional_headroom_secondary", 0.0)))), 2),
    }


def _sync_legacy_flags(state: dict) -> dict:
    simulation = state["simulation"]
    controls = simulation["controls"]
    state["payment_service_unreachable"] = simulation["eligibility_health"] < 0.7
    state["loadgenerator_flood_homepage"] = simulation["external_arrivals_recent"] > 5.0
    state["retry_rate_limit_enabled"] = controls["retry_throttle_factor"] < 0.99
    state["payment_circuit_breaker_enabled"] = bool(controls["circuit_breaker_enabled"])
    state["retry_backoff_enabled"] = controls["retry_backoff_factor"] < 0.99
    state["traffic_shift_enabled"] = controls["traffic_shift_fraction"] > 0.0
    state["payment_feature_disabled"] = not controls["online_scheduling_enabled"]
    return state


def _bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _append_history(simulation: dict) -> None:
    history = list(simulation.get("history") or [])
    history.append(
        {
            "t_sec": simulation["time_sec"],
            "metrics": deepcopy(simulation["derived_metrics"]),
            "business_metrics": deepcopy(simulation["derived_business_metrics"]),
            "queue_depth": round(simulation["queue_depth"], 2),
            "effective_service_rate": round(simulation["effective_service_rate"], 3),
            "lambda_external": round(simulation["external_arrivals_recent"], 3),
            "lambda_retry": round(simulation["retry_arrivals_recent"], 3),
        }
    )
    simulation["history"] = history[-MAX_HISTORY_POINTS:]


def _external_arrival_rate(profile: dict, time_sec: int) -> float:
    rise = profile["rise_sec"]
    plateau = profile["plateau_sec"]
    decay = profile["decay_sec"]
    base = profile["base_rate"]
    peak = profile["peak_rate"]
    if time_sec <= rise:
        return base + (peak - base) * (time_sec / max(rise, 1))
    if time_sec <= rise + plateau:
        return peak
    if time_sec <= rise + plateau + decay:
        remaining = rise + plateau + decay - time_sec
        return base + (peak - base) * (remaining / max(decay, 1))
    return base


def _eligibility_health(profile: dict, time_sec: int) -> float:
    floor = profile["eligibility_health_floor"]
    if time_sec < 90:
        return min(0.92, floor + 0.03)
    if time_sec < 210:
        return min(0.95, floor + 0.09)
    return min(0.98, floor + 0.16)


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
