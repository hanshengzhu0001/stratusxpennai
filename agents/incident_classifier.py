from __future__ import annotations

from config import LATENCY_P95_THRESHOLD_MS, RETRY_RATE_THRESHOLD


def classify_incident(alert_payload: dict, evidence: dict) -> dict:
    metrics = evidence["metrics"]
    common = alert_payload.get("commonAnnotations", {})
    summary = common.get(
        "summary",
        "Checkout latency spiked after payment dependency degradation and retries are increasing.",
    )
    patterns = []
    if metrics.get("retry_rate", 0) >= RETRY_RATE_THRESHOLD:
        patterns.append("retry_amplification")
    if "payment" in evidence.get("services", []):
        patterns.append("payment_degradation")
    if metrics.get("latency_p95_ms", 0) >= LATENCY_P95_THRESHOLD_MS:
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
