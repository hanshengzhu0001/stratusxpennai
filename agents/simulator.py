from __future__ import annotations


SIMULATED_OUTCOMES = {
    "restart_payment": {
        "latency_p95_ms": 1900,
        "error_rate": 0.14,
        "retry_rate": 0.30,
        "time_to_effect_seconds": 120,
        "risk_level": "medium",
        "impact": "medium",
        "blast_radius": "medium",
        "recovery": "partial",
        "notes": "Restarting refreshes pods, but the retry loop keeps pressure on payment.",
    },
    "disable_flag": {
        "latency_p95_ms": 500,
        "error_rate": 0.92,
        "retry_rate": 0.02,
        "time_to_effect_seconds": 15,
        "risk_level": "very_high",
        "impact": "high",
        "blast_radius": "high",
        "recovery": "mixed",
        "notes": "Feature disable sheds the degraded dependency path quickly, but disables payment capability.",
    },
    "rate_limit_retries": {
        "latency_p95_ms": 1250,
        "error_rate": 0.20,
        "retry_rate": 0.10,
        "time_to_effect_seconds": 30,
        "risk_level": "low",
        "impact": "medium_high",
        "blast_radius": "low",
        "recovery": "strong",
        "notes": "Retry shaping stabilizes the system while keeping the product mostly available.",
    },
    "shift_traffic": {
        "latency_p95_ms": 1450,
        "error_rate": 0.11,
        "retry_rate": 0.13,
        "time_to_effect_seconds": 180,
        "risk_level": "high",
        "impact": "high",
        "blast_radius": "high",
        "recovery": "partial",
        "notes": "Traffic shifting spreads load but does not fix the core degraded dependency.",
    },
}


def simulate_action(state: dict, action: dict) -> dict:
    baseline = state.get("metrics", {})
    outcome = dict(SIMULATED_OUTCOMES[action["id"]])
    outcome["action_id"] = action["id"]
    outcome["latency_direction"] = _direction(
        baseline.get("latency_p95_ms"), outcome["latency_p95_ms"]
    )
    outcome["error_direction"] = _direction(baseline.get("error_rate"), outcome["error_rate"])
    outcome["retry_storm_risk"] = _retry_risk(outcome["retry_rate"])
    return outcome


def compare_prediction_to_actual(action_id: str, predicted: dict, actual: dict) -> dict:
    normalized_predicted = normalize_prediction(predicted)
    normalized_actual = normalize_actual(actual)

    matches = {
        "latency_direction": normalized_predicted["latency_direction"]
        == normalized_actual["latency_direction"],
        "error_direction": normalized_predicted["error_direction"]
        == normalized_actual["error_direction"],
        "retry_storm_risk": normalized_predicted["retry_storm_risk"]
        == normalized_actual["retry_storm_risk"],
        "risk_level": normalized_predicted["risk_level"] == normalized_actual["risk_level"],
    }
    score = round(sum(1 for value in matches.values() if value) / len(matches), 2)
    return {
        "action_id": action_id,
        "normalized_predicted": normalized_predicted,
        "normalized_actual": normalized_actual,
        "matches": matches,
        "score": score,
    }


def normalize_prediction(predicted: dict) -> dict:
    latency_direction = predicted.get("latency_direction")
    if latency_direction is None and "latency_p95_ms" in predicted:
        latency_direction = "down"

    error_direction = predicted.get("error_direction")
    if error_direction is None and "error_rate" in predicted:
        error_direction = "down" if predicted["error_rate"] <= 0.18 else "mixed"

    retry_storm_risk = predicted.get("retry_storm_risk")
    if retry_storm_risk is None and "retry_rate" in predicted:
        retry_storm_risk = _retry_risk(predicted["retry_rate"])

    risk_level = predicted.get("risk_level")
    if risk_level is None and "blast_radius" in predicted:
        risk_level = predicted["blast_radius"]

    return {
        "latency_direction": latency_direction or "mixed",
        "error_direction": error_direction or "mixed",
        "retry_storm_risk": retry_storm_risk or "medium",
        "risk_level": _canonical_risk(risk_level or "medium"),
    }


def normalize_actual(actual: dict) -> dict:
    return {
        "latency_direction": actual.get("latency_direction", "mixed"),
        "error_direction": actual.get("error_direction", "mixed"),
        "retry_storm_risk": actual.get("retry_storm_risk", "medium"),
        "risk_level": _canonical_risk(actual.get("risk_level") or actual.get("blast_radius", "medium")),
    }


def _direction(before: float | int | None, after: float | int | None) -> str:
    if before is None or after is None:
        return "mixed"
    if after < before:
        return "down"
    if after > before:
        return "up"
    return "mixed"


def _retry_risk(retry_rate: float) -> str:
    if retry_rate <= 0.12:
        return "low"
    if retry_rate <= 0.25:
        return "medium"
    return "high"


def _canonical_risk(risk_level: str) -> str:
    normalized = risk_level.strip().lower().replace("-", "_")
    aliases = {
        "very_high": "high",
        "medium_high": "medium",
        "medium_low": "medium",
        "critical": "high",
    }
    return aliases.get(normalized, normalized)
