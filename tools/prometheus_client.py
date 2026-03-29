from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_EVIDENCE = {
    "services": ["frontend", "checkout", "payment"],
    "metrics": {
        "latency_p95_ms": 2300,
        "error_rate": 0.18,
        "retry_rate": 0.31,
    },
    "logs": [
        "payment timeout spikes observed",
        "checkout retries exceeding normal threshold",
    ],
    "traces": [
        "checkout -> payment span dominates p95",
        "retry fan-out visible across checkout service",
    ],
    "prometheus": {
        "source": "mock",
        "queries": {
            "checkout_latency_p95_ms": 2300,
            "checkout_error_rate": 0.18,
            "checkout_retry_rate": 0.31,
        },
        "active_alerts": [
            "CheckoutLatencyHigh",
            "CheckoutRetryStorm",
        ],
    },
}


def collect_evidence(alert_payload: dict) -> dict:
    evidence = json.loads(json.dumps(DEFAULT_EVIDENCE))
    prom_base = os.environ.get("PROMETHEUS_BASE_URL")
    if not prom_base:
        evidence["prometheus"]["note"] = "PROMETHEUS_BASE_URL not set; using mock evidence."
        return evidence

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
        evidence["logs"] = _alert_summaries(alert_payload)
        return evidence
    except Exception as exc:
        evidence["prometheus"]["note"] = (
            f"Prometheus query failed; using mock evidence: {exc.__class__.__name__}"
        )
        return evidence


def save_alert_payload(payload: dict, path: str | Path = "alerts/latest.json") -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
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
