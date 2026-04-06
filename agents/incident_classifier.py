from __future__ import annotations


def classify_incident(alert_payload: dict, evidence: dict) -> dict:
    metrics = evidence["metrics"]
    common = alert_payload.get("commonAnnotations", {})
    summary = common.get(
        "summary",
        "Scheduling latency spiked after eligibility dependency degradation and retries are increasing.",
    )
    patterns = []
    if metrics.get("retry_rate", 0) >= 0.2:
        patterns.append("retry_amplification")
    services = set(evidence.get("services", []))
    if {"payment", "eligibility"} & services:
        patterns.append("payment_degradation")
    if metrics.get("latency_p95_ms", 0) >= 2000:
        patterns.append("high_checkout_latency")

    return {
        "summary": summary,
        "services": evidence.get("services", []),
        "metrics": metrics,
        "logs": evidence.get("logs", []),
        "traces": evidence.get("traces", []),
        "labels": alert_payload.get("commonLabels", {}),
        "pattern_matches": patterns,
        "source": "alertmanager+prometheus",
    }
