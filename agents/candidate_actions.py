from __future__ import annotations


RETAIL_ACTION_LIBRARY = [
    {
        "id": "rate_limit_retries",
        "type": "throttle",
        "target": "checkout",
        "category": "retry_overload_control",
        "description": "Rate-limit checkout retries to break retry amplification.",
        "execution_surface": "browser",
    },
    {
        "id": "enable_payment_circuit_breaker",
        "type": "circuit_breaker",
        "target": "payment",
        "category": "retry_overload_control",
        "description": "Enable a payment circuit breaker to fail fast instead of retrying into a degraded dependency.",
        "execution_surface": "browser",
    },
    {
        "id": "increase_retry_backoff",
        "type": "config",
        "target": "checkout",
        "category": "retry_overload_control",
        "description": "Increase retry backoff so checkout stops hammering payment during partial degradation.",
        "execution_surface": "browser",
    },
    {
        "id": "restart_payment",
        "type": "restart",
        "target": "payment",
        "category": "service_recovery",
        "description": "Restart payment pods to clear local state after backlog is reduced.",
        "execution_surface": "browser",
    },
    {
        "id": "shift_traffic",
        "type": "traffic_shift",
        "target": "secondary_region",
        "category": "traffic_management",
        "description": "Shift a slice of checkout traffic to the secondary region.",
        "execution_surface": "browser",
    },
    {
        "id": "disable_flag",
        "type": "flag",
        "target": "payment_feature",
        "category": "graceful_degradation",
        "description": "Disable the payment feature flag as a last-resort kill switch.",
        "execution_surface": "browser",
    },
    {
        "id": "rollback_payment_deploy",
        "type": "rollback",
        "target": "payment",
        "category": "deployment_recovery",
        "description": "Rollback the most recent payment deployment.",
        "execution_surface": "k8s",
    },
    {
        "id": "rollback_checkout_deploy",
        "type": "rollback",
        "target": "checkout",
        "category": "deployment_recovery",
        "description": "Rollback the most recent checkout deployment.",
        "execution_surface": "k8s",
    },
    {
        "id": "disable_nonessential_checkout_features",
        "type": "degrade",
        "target": "checkout",
        "category": "graceful_degradation",
        "description": "Disable nonessential checkout features and keep the transaction path alive.",
        "execution_surface": "future",
    },
    {
        "id": "serve_stale_catalog_cache",
        "type": "cache",
        "target": "catalog",
        "category": "graceful_degradation",
        "description": "Serve stale catalog data to preserve capacity for checkout and payment.",
        "execution_surface": "future",
    },
    {
        "id": "failover_to_secondary_region",
        "type": "failover",
        "target": "region",
        "category": "traffic_management",
        "description": "Fail over fully to the secondary region when the primary region is no longer safe.",
        "execution_surface": "k8s",
    },
    {
        "id": "pause_noncritical_background_jobs",
        "type": "workload_shaping",
        "target": "background_workers",
        "category": "capacity_management",
        "description": "Pause noncritical background jobs to free capacity for checkout and payment.",
        "execution_surface": "k8s",
    },
]


def retail_action_library() -> list[dict]:
    return [dict(action) for action in RETAIL_ACTION_LIBRARY]


def generate_candidate_actions(state: dict) -> list[dict]:
    return build_scenario_shortlist(state)["shortlist"]


def build_scenario_shortlist(state: dict, limit: int = 6) -> dict:
    library = retail_action_library()
    patterns = set(state.get("pattern_matches", []))
    services = set(state.get("services", []))

    selected_ids: list[str] = []
    selection_rationale: list[str] = []

    if {"retry_amplification", "high_checkout_latency"} & patterns:
        selected_ids.extend(
            [
                "rate_limit_retries",
                "enable_payment_circuit_breaker",
                "increase_retry_backoff",
            ]
        )
        selection_rationale.append(
            "Planner Agent prioritized overload-control actions because retry amplification and elevated checkout latency are present."
        )

    if "payment" in services:
        selected_ids.extend(
            [
                "restart_payment",
                "shift_traffic",
                "disable_flag",
            ]
        )
        selection_rationale.append(
            "Planner Agent kept one tempting local reflex, one traffic option, and one kill switch so Guardrail can compare safe versus dangerous futures."
        )

    if not selected_ids:
        selected_ids.extend(
            [
                "rate_limit_retries",
                "enable_payment_circuit_breaker",
                "restart_payment",
                "shift_traffic",
            ]
        )
        selection_rationale.append(
            "Planner Agent used a generic retail shortlist because the incident did not strongly match a narrower pattern."
        )

    shortlist_ids = _dedupe(selected_ids)[:limit]
    shortlist = [action for action in library if action["id"] in shortlist_ids]
    shortlist.sort(key=lambda action: shortlist_ids.index(action["id"]))

    return {
        "action_library": library,
        "shortlist": shortlist,
        "selection_strategy": "broader_retail_action_library -> scenario_shortlist -> stratus_ranking",
        "selection_rationale": selection_rationale,
    }


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered
