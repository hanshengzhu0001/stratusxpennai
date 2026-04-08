from __future__ import annotations


def classify_incident(alert_payload: dict, evidence: dict) -> dict:
    metrics = evidence["metrics"]
    business_metrics = evidence.get("business_metrics", {})
    constraints = evidence.get("constraints", {})
    labels = alert_payload.get("commonLabels", {})
    common = alert_payload.get("commonAnnotations", {})
    summary = common.get(
        "summary",
        "Scheduling latency spiked after eligibility dependency degradation and retries are increasing.",
    )
    patterns = []
    if metrics.get("retry_rate", 0) >= 0.2 or business_metrics.get("retry_amplification_factor", 1.0) >= 1.8:
        patterns.append("retry_amplification")
    services = set(evidence.get("services", []))
    if {"payment", "eligibility"} & services and float(constraints.get("eligibility_health", 1.0)) <= 0.62:
        patterns.append("payment_degradation")
    if metrics.get("latency_p95_ms", 0) >= 2000:
        patterns.append("high_checkout_latency")
    if business_metrics.get("seat_hold_utilization", 0.0) >= 0.78 or business_metrics.get("seat_hold_expiration_rate", 0.0) >= 0.16:
        patterns.append("slot_hold_clog")
    if business_metrics.get("fairness_skew", 0.0) >= 0.18:
        patterns.append("fairness_breakdown")
    if float(constraints.get("regional_headroom_secondary", 1.0)) <= 0.24:
        patterns.append("regional_headroom_risk")
    if float(constraints.get("manual_callback_queue", 0.0)) >= 20:
        patterns.append("manual_fallback_pressure")

    return {
        "summary": summary,
        "services": evidence.get("services", []),
        "metrics": metrics,
        "business_metrics": business_metrics,
        "constraints": constraints,
        "logs": evidence.get("logs", []),
        "traces": evidence.get("traces", []),
        "labels": labels,
        "scenario_id": labels.get("scenario_id"),
        "pattern_matches": patterns,
        "source": "alertmanager+prometheus",
    }
