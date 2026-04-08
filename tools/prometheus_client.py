from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from tools.runtime_state import current_constraints, derive_metrics, ensure_alert_scenario_active, load_state
from tools.scenario_catalog import derive_business_metrics


DEFAULT_EVIDENCE = {
    "services": ["portal", "scheduling", "eligibility"],
    "metrics": {
        "latency_p95_ms": 2300,
        "error_rate": 0.18,
        "retry_rate": 0.31,
    },
    "business_metrics": {
        "queue_abandonment_rate": 0.24,
        "payment_success_rate": 0.72,
        "retry_amplification_factor": 2.7,
        "fairness_skew": 0.16,
        "seat_hold_utilization": 0.78,
        "seat_hold_expiration_rate": 0.14,
    },
    "constraints": {
        "queue_depth": 28.0,
        "eligibility_health": 0.46,
        "regional_headroom_secondary": 0.27,
        "manual_callback_queue": 0.0,
    },
    "logs": [
        "eligibility timeout spikes observed",
        "scheduling retries exceeding normal threshold",
    ],
    "traces": [
        "scheduling -> eligibility span dominates p95",
        "retry fan-out visible across scheduling service",
    ],
    "prometheus": {
        "source": "mock",
        "queries": {
            "checkout_latency_p95_ms": 2300,
            "checkout_error_rate": 0.18,
            "checkout_retry_rate": 0.31,
        },
        "active_alerts": [
            "SchedulingLatencyHigh",
            "SchedulingRetrySpiral",
        ],
    },
}


def collect_evidence(alert_payload: dict) -> dict:
    ensure_alert_scenario_active(alert_payload)
    state = load_state()
    evidence = json.loads(json.dumps(DEFAULT_EVIDENCE))
    _enrich_with_state(evidence, alert_payload, state)
    prom_base = os.environ.get("PROMETHEUS_BASE_URL")
    if not prom_base:
        return _direct_evidence_fallback(
            evidence,
            alert_payload,
            state,
            "PROMETHEUS_BASE_URL not set; using direct control-plane metrics.",
        )

    query_results = {}
    active_alerts = []
    try:
        for key, query in _prom_queries().items():
            query_results[key] = _query_prometheus(prom_base, query)
        active_alerts = _get_active_alerts(prom_base)
        evidence["metrics"]["latency_p95_ms"] = int(query_results["checkout_latency_p95_ms"] or 2300)
        evidence["metrics"]["error_rate"] = round(float(query_results["checkout_error_rate"] or 0.18), 4)
        evidence["metrics"]["retry_rate"] = round(float(query_results["checkout_retry_rate"] or 0.31), 4)
        evidence["prometheus"] = {
            "source": "live",
            "queries": query_results,
            "active_alerts": active_alerts,
        }
        _enrich_with_state(evidence, alert_payload, state)
        return evidence
    except Exception as exc:
        return _direct_evidence_fallback(
            evidence,
            alert_payload,
            state,
            f"Prometheus query failed; using direct control-plane metrics: {exc.__class__.__name__}",
        )


def save_alert_payload(payload: dict, path: str | Path = "alerts/latest.json") -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=out_path.parent,
        prefix=f".{out_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
        tmp_path = Path(handle.name)
    tmp_path.replace(out_path)
    return out_path


def _query_prometheus(base_url: str, query: str) -> float | None:
    encoded = urllib.parse.urlencode({"query": query})
    url = f"{base_url.rstrip('/')}/api/v1/query?{encoded}"
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data", {})
    result_type = data.get("resultType")
    result = data.get("result")
    if result_type == "vector":
        if not result:
            return None
        value = result[0].get("value", [None, None])[1]
        return float(value) if value is not None else None
    if result_type == "scalar":
        value = result[1] if isinstance(result, list) and len(result) > 1 else None
        return float(value) if value is not None else None
    return None


def _get_active_alerts(base_url: str) -> list[str]:
    url = f"{base_url.rstrip('/')}/api/v1/alerts"
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    alerts = payload.get("data", {}).get("alerts", [])
    return [item.get("labels", {}).get("alertname", "unknown") for item in alerts]


def _alert_summaries(alert_payload: dict) -> list[str]:
    summaries = []
    for alert in alert_payload.get("alerts", []):
        summary = alert.get("annotations", {}).get("summary")
        if summary:
            summaries.append(summary)
    return summaries or list(DEFAULT_EVIDENCE["logs"])


def _direct_evidence_fallback(evidence: dict, alert_payload: dict, state: dict, note: str) -> dict:
    metrics = derive_metrics(state)
    evidence["metrics"]["latency_p95_ms"] = int(metrics["latency_p95_ms"])
    evidence["metrics"]["error_rate"] = round(float(metrics["error_rate"]), 4)
    evidence["metrics"]["retry_rate"] = round(float(metrics["retry_rate"]), 4)
    evidence["prometheus"] = {
        "source": "direct_fallback",
        "queries": {
            "checkout_latency_p95_ms": evidence["metrics"]["latency_p95_ms"],
            "checkout_error_rate": evidence["metrics"]["error_rate"],
            "checkout_retry_rate": evidence["metrics"]["retry_rate"],
        },
        "active_alerts": _active_alerts_from_metrics(evidence["metrics"]),
        "note": note,
    }
    _enrich_with_state(evidence, alert_payload, state)
    return evidence


def _enrich_with_state(evidence: dict, alert_payload: dict, state: dict) -> None:
    scenario_id = (
        alert_payload.get("commonLabels", {}).get("scenario_id")
        or alert_payload.get("alerts", [{}])[0].get("labels", {}).get("scenario_id")
        or state.get("active_scenario")
        or "retry_death_spiral"
    )
    technical = evidence.get("metrics", {})
    evidence["business_metrics"] = derive_business_metrics(state, technical)
    evidence["constraints"] = current_constraints(state)
    evidence["services"] = {
        "retry_death_spiral": ["portal", "scheduling", "eligibility"],
        "payment_gateway_flap": ["portal", "scheduling", "eligibility"],
        "seat_hold_clog": ["portal", "scheduling", "inventory"],
        "regional_saturation": ["portal", "scheduling", "region_capacity"],
    }.get(scenario_id, ["portal", "scheduling", "eligibility"])
    hints = _scenario_hints(scenario_id)
    summaries = _alert_summaries(alert_payload)
    evidence["logs"] = summaries + [item for item in hints["logs"] if item not in summaries]
    evidence["traces"] = hints["traces"]


def _active_alerts_from_metrics(metrics: dict) -> list[str]:
    alerts: list[str] = []
    if float(metrics.get("latency_p95_ms", 0)) > 2000:
        alerts.append("SchedulingLatencyHigh")
    if float(metrics.get("retry_rate", 0.0)) > 0.25:
        alerts.append("SchedulingRetrySpiral")
    return alerts


def _scenario_hints(scenario_id: str) -> dict:
    hints = {
        "retry_death_spiral": {
            "logs": [
                "eligibility timeouts are clustering behind a retry burst",
                "portal retries are consuming more capacity than new booking demand",
            ],
            "traces": [
                "scheduling -> eligibility spans dominate p95 while retry fan-out is still increasing",
                "hold backlog is present but not the primary root cause",
            ],
        },
        "payment_gateway_flap": {
            "logs": [
                "eligibility verifier is oscillating between healthy and degraded responses",
                "backlog exists, but dependency instability is the dominant bottleneck",
            ],
            "traces": [
                "eligibility span health drives most of the latency variance",
                "retry fan-out is moderate relative to dependency degradation",
            ],
        },
        "seat_hold_clog": {
            "logs": [
                "slot-hold TTL is trapping scarce inventory for duplicate attempts",
                "fairness degradation is rising faster than eligibility error rate",
            ],
            "traces": [
                "inventory pressure dominates user-visible wait more than dependency latency",
                "eligibility is partially healthy, but hold release lags booking completion",
            ],
        },
        "regional_saturation": {
            "logs": [
                "secondary-region headroom is close to the failover safety floor",
                "a small traffic shift may help, but the margin is narrow",
            ],
            "traces": [
                "regional ingress saturation dominates more than dependency health",
                "routing decisions change blast radius more than retry tuning alone",
            ],
        },
    }
    return hints.get(scenario_id, hints["retry_death_spiral"])


def _prom_queries() -> dict[str, str]:
    return {
        "checkout_latency_p95_ms": os.environ.get(
            "PROMQL_CHECKOUT_LATENCY_P95_MS",
            'histogram_quantile(0.95, sum(rate(http_server_duration_milliseconds_bucket{service="checkout"}[5m])) by (le)) * 1000',
        ),
        "checkout_error_rate": os.environ.get(
            "PROMQL_CHECKOUT_ERROR_RATE",
            'sum(rate(http_requests_total{service="checkout",status=~"5.."}[5m])) / sum(rate(http_requests_total{service="checkout"}[5m]))',
        ),
        "checkout_retry_rate": os.environ.get(
            "PROMQL_CHECKOUT_RETRY_RATE",
            'sum(rate(checkout_retries_total[5m])) / sum(rate(checkout_requests_total[5m]))',
        ),
    }
