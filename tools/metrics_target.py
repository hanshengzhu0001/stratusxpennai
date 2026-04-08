from __future__ import annotations

from fastapi import FastAPI, Response
from tools.runtime_state import current_constraints, derive_metrics, load_state
from tools.scenario_catalog import derive_business_metrics

app = FastAPI(title="Synthetic Incident Metrics Target")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    state = load_state()
    current = derive_metrics(state)
    business = derive_business_metrics(state, current)
    constraints = current_constraints(state)
    body = f"""# HELP checkout_latency_p95_ms Synthetic scheduling latency p95 in milliseconds
# TYPE checkout_latency_p95_ms gauge
checkout_latency_p95_ms {current["latency_p95_ms"]}
# HELP checkout_error_rate Synthetic scheduling error rate
# TYPE checkout_error_rate gauge
checkout_error_rate {current["error_rate"]}
# HELP checkout_retry_rate Synthetic scheduling retry rate
# TYPE checkout_retry_rate gauge
checkout_retry_rate {current["retry_rate"]}
# HELP payment_service_unreachable Eligibility-service degradation toggle
# TYPE payment_service_unreachable gauge
payment_service_unreachable {1 if state["payment_service_unreachable"] else 0}
# HELP retry_rate_limit_enabled Booking retry-throttle remediation flag
# TYPE retry_rate_limit_enabled gauge
retry_rate_limit_enabled {1 if state["retry_rate_limit_enabled"] else 0}
# HELP scheduling_abandonment_rate Synthetic scheduling abandonment rate
# TYPE scheduling_abandonment_rate gauge
scheduling_abandonment_rate {business["queue_abandonment_rate"]}
# HELP booking_completion_rate Synthetic booking completion rate
# TYPE booking_completion_rate gauge
booking_completion_rate {business["payment_success_rate"]}
# HELP slot_hold_utilization Synthetic slot hold utilization
# TYPE slot_hold_utilization gauge
slot_hold_utilization {business["seat_hold_utilization"]}
# HELP fairness_skew Synthetic access fairness skew
# TYPE fairness_skew gauge
fairness_skew {business["fairness_skew"]}
# HELP simulation_lambda_external Synthetic external arrival rate
# TYPE simulation_lambda_external gauge
simulation_lambda_external {constraints["lambda_external"]}
# HELP simulation_lambda_retry Synthetic retry-generated arrival rate
# TYPE simulation_lambda_retry gauge
simulation_lambda_retry {constraints["lambda_retry"]}
# HELP simulation_queue_depth Synthetic queue depth proxy
# TYPE simulation_queue_depth gauge
simulation_queue_depth {constraints["queue_depth"]}
# HELP simulation_effective_service_rate Synthetic effective service rate
# TYPE simulation_effective_service_rate gauge
simulation_effective_service_rate {constraints["effective_service_rate"]}
"""
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")
