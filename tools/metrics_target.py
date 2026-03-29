from __future__ import annotations

from fastapi import FastAPI, Response
from tools.runtime_state import derive_metrics, load_state

app = FastAPI(title="Synthetic Incident Metrics Target")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    state = load_state()
    current = derive_metrics(state)
    body = f"""# HELP checkout_latency_p95_ms Synthetic checkout latency p95 in milliseconds
# TYPE checkout_latency_p95_ms gauge
checkout_latency_p95_ms {current["latency_p95_ms"]}
# HELP checkout_error_rate Synthetic checkout error rate
# TYPE checkout_error_rate gauge
checkout_error_rate {current["error_rate"]}
# HELP checkout_retry_rate Synthetic checkout retry rate
# TYPE checkout_retry_rate gauge
checkout_retry_rate {current["retry_rate"]}
# HELP payment_service_unreachable Feature-flag style incident toggle
# TYPE payment_service_unreachable gauge
payment_service_unreachable {1 if state["payment_service_unreachable"] else 0}
# HELP retry_rate_limit_enabled Remediation flag
# TYPE retry_rate_limit_enabled gauge
retry_rate_limit_enabled {1 if state["retry_rate_limit_enabled"] else 0}
"""
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")
