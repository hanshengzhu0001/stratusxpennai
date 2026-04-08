from __future__ import annotations

from tools.simulation import compute_business_metrics, default_simulation_state, profile_for_scenario

SCENARIO_PROFILES = {
    "retry_death_spiral": {
        "id": "retry_death_spiral",
        "label": "Scheduling Retry Spiral",
        "headline": "Appointment-booking retries turn a degraded eligibility dependency into an access outage.",
        "story": "A batch of high-demand telehealth and specialist slots opens, eligibility verification slows down, and patient-portal booking retries amplify the failure until access fairness and booking completion both collapse.",
        "expected_first_action": "rate_limit_retries",
        "sequence_preview": [
            "Stabilize booking retry pressure",
            "Verify scheduling and eligibility recovery",
            "Normalize patient access posture",
        ],
        "state_overrides": {
            "payment_service_unreachable": True,
            "loadgenerator_flood_homepage": True,
        },
        "sentinel_focus": [
            "eligibility latency",
            "booking retry amplification",
            "scheduling abandonment",
        ],
        "alert_profile": {
            "alertname": "SchedulingRetrySpiral",
            "service": "scheduling",
            "dependency": "eligibility",
            "severity": "critical",
            "summary": "Scheduling retry pressure above baseline",
        },
        "trigger_label": profile_for_scenario("retry_death_spiral")["trigger_label"],
    },
    "payment_gateway_flap": {
        "id": "payment_gateway_flap",
        "label": "Eligibility Verification Flap",
        "headline": "Eligibility checks are unstable but not fully down, so retries and timeouts need containment.",
        "story": "The scheduling path can still place some appointments, but intermittent eligibility and cost-estimate timeouts threaten to snowball into a broader access failure.",
        "expected_first_action": "enable_payment_circuit_breaker",
        "sequence_preview": [
            "Contain unstable eligibility calls",
            "Reduce retry pressure",
            "Restore direct booking flow",
        ],
        "state_overrides": {
            "payment_service_unreachable": True,
            "loadgenerator_flood_homepage": False,
        },
        "sentinel_focus": [
            "booking completion rate",
            "timeout spikes",
            "retry backoff need",
        ],
        "alert_profile": {
            "alertname": "EligibilityVerificationFlap",
            "service": "eligibility",
            "dependency": "eligibility",
            "severity": "critical",
            "summary": "Eligibility verification is flapping and causing access instability",
        },
        "trigger_label": profile_for_scenario("payment_gateway_flap")["trigger_label"],
    },
    "seat_hold_clog": {
        "id": "seat_hold_clog",
        "label": "Slot Hold Clog",
        "headline": "Appointment slots are locked faster than they are confirmed, starving real patients.",
        "story": "Slot inventory gets trapped in pending holds while eligibility remains partially available, driving abandonment, access skew, and delayed care.",
        "expected_first_action": "shorten_slot_hold_ttl",
        "sequence_preview": [
            "Relieve slot-hold pressure",
            "Improve booking completion",
            "Restore normal slot-release behavior",
        ],
        "state_overrides": {
            "payment_service_unreachable": False,
            "loadgenerator_flood_homepage": True,
        },
        "sentinel_focus": [
            "slot hold utilization",
            "hold expiration",
            "access skew",
        ],
        "alert_profile": {
            "alertname": "SlotHoldClog",
            "service": "scheduling",
            "dependency": "inventory",
            "severity": "critical",
            "summary": "Slot holds are clogging access and delaying booking completion",
        },
        "trigger_label": profile_for_scenario("seat_hold_clog")["trigger_label"],
    },
    "regional_saturation": {
        "id": "regional_saturation",
        "label": "Regional Access Saturation",
        "headline": "Primary-region stress makes failover tempting, but the backup region is safe only above a narrow headroom threshold.",
        "story": "Portal demand climbs across the telehealth scheduling release while the backup region can absorb only a cautious shift. If that headroom slips below threshold, traffic shift becomes the wrong move and diversion is safer.",
        "expected_first_action": "shift_traffic",
        "sequence_preview": [
            "Stabilize primary-region access pressure",
            "Shift traffic cautiously",
            "Return to balanced routing",
        ],
        "state_overrides": {
            "payment_service_unreachable": False,
            "loadgenerator_flood_homepage": True,
        },
        "sentinel_focus": [
            "regional saturation",
            "headroom",
            "blast-radius risk",
        ],
        "alert_profile": {
            "alertname": "RegionalAccessSaturation",
            "service": "portal",
            "dependency": "region_capacity",
            "severity": "critical",
            "summary": "Primary-region access saturation is approaching unsafe failover territory",
        },
        "trigger_label": profile_for_scenario("regional_saturation")["trigger_label"],
    },
}


def scenario_catalog() -> list[dict]:
    return [dict(profile) for profile in SCENARIO_PROFILES.values()]


def get_scenario(scenario_id: str | None) -> dict:
    if scenario_id and scenario_id in SCENARIO_PROFILES:
        return dict(SCENARIO_PROFILES[scenario_id])
    return dict(SCENARIO_PROFILES["retry_death_spiral"])


def default_state_for_scenario(scenario_id: str | None) -> dict:
    scenario = get_scenario(scenario_id)
    return {
        "active_scenario": scenario["id"],
        "payment_service_unreachable": False,
        "loadgenerator_flood_homepage": False,
        "retry_rate_limit_enabled": False,
        "payment_circuit_breaker_enabled": False,
        "retry_backoff_enabled": False,
        "traffic_shift_enabled": False,
        "payment_feature_disabled": False,
        "last_action": None,
        "simulation": default_simulation_state(scenario["id"]),
    }


def derive_business_metrics(state: dict, technical_metrics: dict) -> dict:
    if state.get("simulation"):
        return compute_business_metrics(state)
    scenario = get_scenario(state.get("active_scenario"))
    latency = float(technical_metrics.get("latency_p95_ms", 0))
    error_rate = float(technical_metrics.get("error_rate", 0.0))
    retry_rate = float(technical_metrics.get("retry_rate", 0.0))

    latency_stress = _clamp((latency - 900) / 1400, 0.0, 1.0)
    retry_stress = _clamp(retry_rate / 0.35, 0.0, 1.0)
    error_stress = _clamp(error_rate / 0.25, 0.0, 1.0)

    queue_abandonment = 0.05 + latency_stress * 0.12 + retry_stress * 0.14
    payment_success = 0.97 - error_stress * 0.26 - retry_stress * 0.10
    fairness_skew = 0.04 + retry_stress * 0.22 + latency_stress * 0.08
    seat_hold_utilization = 0.58 + latency_stress * 0.16 + retry_stress * 0.11
    seat_hold_expiration = 0.09 + error_stress * 0.16 + latency_stress * 0.11

    if scenario["id"] == "payment_gateway_flap":
        payment_success -= 0.08
        seat_hold_utilization -= 0.05
    elif scenario["id"] == "seat_hold_clog":
        fairness_skew += 0.06
        seat_hold_utilization += 0.11
        seat_hold_expiration += 0.09
    elif scenario["id"] == "regional_saturation":
        queue_abandonment += 0.03
        fairness_skew += 0.03

    if state.get("payment_feature_disabled"):
        payment_success = 0.08
        queue_abandonment = 0.42
        fairness_skew = 0.28

    return {
        "queue_abandonment_rate": round(_clamp(queue_abandonment, 0.03, 0.45), 2),
        "payment_success_rate": round(_clamp(payment_success, 0.08, 0.98), 2),
        "retry_amplification_factor": round(1.0 + retry_rate * 10, 1),
        "fairness_skew": round(_clamp(fairness_skew, 0.02, 0.35), 2),
        "seat_hold_utilization": round(_clamp(seat_hold_utilization, 0.45, 0.98), 2),
        "seat_hold_expiration_rate": round(_clamp(seat_hold_expiration, 0.05, 0.45), 2),
    }


def default_sequence_preview(scenario_id: str | None, chosen_action_id: str | None) -> list[dict]:
    scenario = get_scenario(scenario_id)
    labels = list(scenario["sequence_preview"])
    if chosen_action_id:
        labels[0] = f"Execute {chosen_action_id}"
    return [
        {"step": index + 1, "label": label}
        for index, label in enumerate(labels)
    ]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
