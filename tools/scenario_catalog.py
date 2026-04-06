from __future__ import annotations


SCENARIO_PROFILES = {
    "retry_death_spiral": {
        "id": "retry_death_spiral",
        "label": "Retry Death Spiral",
        "headline": "Checkout retries turn a payment wobble into a self-inflicted outage.",
        "story": "Ticket demand surges, payment degrades, and checkout retries amplify the failure until fairness and conversion both collapse.",
        "expected_first_action": "rate_limit_retries",
        "sequence_preview": [
            "Stabilize retry pressure",
            "Verify checkout and payment recovery",
            "Normalize traffic posture",
        ],
        "state_overrides": {
            "payment_service_unreachable": True,
            "loadgenerator_flood_homepage": True,
        },
        "sentinel_focus": [
            "payment latency",
            "retry amplification",
            "queue abandonment",
        ],
    },
    "payment_gateway_flap": {
        "id": "payment_gateway_flap",
        "label": "Payment Gateway Flap",
        "headline": "Payment is unstable but not fully down, so retries and timeouts need containment.",
        "story": "Checkout can still move some transactions, but intermittent payment timeouts threaten to snowball into a broader failure.",
        "expected_first_action": "enable_payment_circuit_breaker",
        "sequence_preview": [
            "Contain unstable payment attempts",
            "Reduce retry pressure",
            "Restore direct payment flow",
        ],
        "state_overrides": {
            "payment_service_unreachable": True,
            "loadgenerator_flood_homepage": False,
        },
        "sentinel_focus": [
            "payment success rate",
            "timeout spikes",
            "retry backoff need",
        ],
    },
    "seat_hold_clog": {
        "id": "seat_hold_clog",
        "label": "Seat Hold Clog",
        "headline": "Seats are locked faster than they are converted, starving real buyers.",
        "story": "Inventory gets trapped in pending holds while payment remains partially available, driving abandonment and fairness stress.",
        "expected_first_action": "increase_retry_backoff",
        "sequence_preview": [
            "Relieve hold pressure",
            "Improve payment completion",
            "Restore normal hold behavior",
        ],
        "state_overrides": {
            "payment_service_unreachable": False,
            "loadgenerator_flood_homepage": True,
        },
        "sentinel_focus": [
            "seat hold utilization",
            "hold expiration",
            "fairness skew",
        ],
    },
    "regional_saturation": {
        "id": "regional_saturation",
        "label": "Regional Saturation",
        "headline": "Primary-region stress makes failover tempting, but secondary capacity is limited.",
        "story": "Traffic pressure climbs across the ticket drop while the backup region has only enough headroom for a cautious shift.",
        "expected_first_action": "shift_traffic",
        "sequence_preview": [
            "Stabilize primary region pressure",
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
        "payment_service_unreachable": scenario["state_overrides"]["payment_service_unreachable"],
        "loadgenerator_flood_homepage": scenario["state_overrides"]["loadgenerator_flood_homepage"],
        "retry_rate_limit_enabled": False,
        "payment_circuit_breaker_enabled": False,
        "retry_backoff_enabled": False,
        "traffic_shift_enabled": False,
        "payment_feature_disabled": False,
        "last_action": None,
    }


def derive_business_metrics(state: dict, technical_metrics: dict) -> dict:
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
