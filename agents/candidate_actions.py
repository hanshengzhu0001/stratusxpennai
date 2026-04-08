from __future__ import annotations

from tools.simulation import projected_secondary_headroom_from_state


RETAIL_ACTION_LIBRARY = [
    {
        "id": "rate_limit_retries",
        "type": "throttle",
        "target": "checkout",
        "category": "retry_overload_control",
        "description": "Throttle appointment-booking retries to break retry amplification.",
        "execution_surface": "browser",
    },
    {
        "id": "enable_payment_circuit_breaker",
        "type": "circuit_breaker",
        "target": "payment",
        "category": "retry_overload_control",
        "description": "Enable an eligibility fail-fast circuit breaker instead of retrying into a degraded dependency.",
        "execution_surface": "browser",
    },
    {
        "id": "increase_retry_backoff",
        "type": "config",
        "target": "checkout",
        "category": "retry_overload_control",
        "description": "Increase booking retry backoff so the portal stops hammering eligibility during partial degradation.",
        "execution_surface": "browser",
    },
    {
        "id": "shorten_slot_hold_ttl",
        "type": "inventory_policy",
        "target": "slot_inventory",
        "category": "fairness_and_inventory_control",
        "description": "Shorten slot-hold TTL so trapped appointment inventory is released back to real patients faster.",
        "execution_surface": "browser",
    },
    {
        "id": "route_to_callback_queue",
        "type": "fallback_routing",
        "target": "overflow_patients",
        "category": "graceful_access_fallback",
        "description": "Route a controlled slice of demand to staffed callback instead of letting repeated online attempts starve access.",
        "execution_surface": "browser",
    },
    {
        "id": "reserve_priority_slots",
        "type": "fairness_policy",
        "target": "high_priority_cohorts",
        "category": "fairness_and_inventory_control",
        "description": "Reserve a protected slice of scarce appointment capacity for higher-priority patients and reduce hold concentration.",
        "execution_surface": "browser",
    },
    {
        "id": "restart_payment",
        "type": "restart",
        "target": "payment",
        "category": "service_recovery",
        "description": "Restart eligibility workers after backlog pressure is reduced.",
        "execution_surface": "browser",
    },
    {
        "id": "shift_traffic",
        "type": "traffic_shift",
        "target": "secondary_region",
        "category": "traffic_management",
        "description": "Shift a slice of patient-portal scheduling traffic to the secondary region.",
        "execution_surface": "browser",
    },
    {
        "id": "disable_flag",
        "type": "flag",
        "target": "payment_feature",
        "category": "graceful_degradation",
        "description": "Disable online self-scheduling as a last-resort kill switch and route patients to staffed fallback.",
        "execution_surface": "browser",
    },
    {
        "id": "rollback_payment_deploy",
        "type": "rollback",
        "target": "payment",
        "category": "deployment_recovery",
        "description": "Rollback the most recent eligibility-service deployment.",
        "execution_surface": "k8s",
    },
    {
        "id": "rollback_checkout_deploy",
        "type": "rollback",
        "target": "checkout",
        "category": "deployment_recovery",
        "description": "Rollback the most recent scheduling-service deployment.",
        "execution_surface": "k8s",
    },
    {
        "id": "disable_nonessential_checkout_features",
        "type": "degrade",
        "target": "checkout",
        "category": "graceful_degradation",
        "description": "Disable nonessential patient-portal features and keep the scheduling path alive.",
        "execution_surface": "future",
    },
    {
        "id": "serve_stale_catalog_cache",
        "type": "cache",
        "target": "catalog",
        "category": "graceful_degradation",
        "description": "Serve stale provider-directory and slot-search data to preserve scheduling capacity.",
        "execution_surface": "future",
    },
    {
        "id": "failover_to_secondary_region",
        "type": "failover",
        "target": "region",
        "category": "traffic_management",
        "description": "Fail over fully to the secondary scheduling region when the primary region is no longer safe.",
        "execution_surface": "k8s",
    },
    {
        "id": "pause_noncritical_background_jobs",
        "type": "workload_shaping",
        "target": "background_workers",
        "category": "capacity_management",
        "description": "Pause noncritical batch work to free capacity for patient access and eligibility checks.",
        "execution_surface": "k8s",
    },
]


def retail_action_library() -> list[dict]:
    return [dict(action) for action in RETAIL_ACTION_LIBRARY]


def generate_candidate_actions(state: dict) -> list[dict]:
    return build_scenario_shortlist(state)["shortlist"]


def build_scenario_shortlist(state: dict, limit: int = 6) -> dict:
    library = retail_action_library()
    action_by_id = {action["id"]: action for action in library}
    patterns = set(state.get("pattern_matches", []))
    services = set(state.get("services", []))
    labels = state.get("labels", {})
    scenario_id = str(
        labels.get("scenario_id")
        or state.get("scenario_id")
        or ""
    )
    metrics = state.get("metrics", {})
    business = state.get("business_metrics", {})
    constraints = state.get("constraints", {})

    scores = {
        action["id"]: (0.0 if action.get("execution_surface") == "browser" else -8.0)
        for action in library
    }
    reasons: dict[str, list[str]] = {action["id"]: [] for action in library}
    selection_rationale: list[str] = []

    def bump(action_id: str, weight: float, reason: str) -> None:
        if action_id not in scores:
            return
        scores[action_id] += weight
        if reason not in reasons[action_id]:
            reasons[action_id].append(reason)

    retry_rate = float(metrics.get("retry_rate", 0.0))
    latency = float(metrics.get("latency_p95_ms", 0.0))
    abandonment = float(business.get("queue_abandonment_rate", 0.0))
    hold_util = float(business.get("seat_hold_utilization", 0.0))
    fairness = float(business.get("fairness_skew", 0.0))
    eligibility_health = float(constraints.get("eligibility_health", 1.0))
    queue_depth = float(constraints.get("queue_depth", 0.0))
    secondary_headroom = float(constraints.get("regional_headroom_secondary", 1.0))
    projected_secondary_headroom = float(
        constraints.get("projected_secondary_headroom_after_shift", projected_secondary_headroom_from_state(state, shift_fraction=0.10))
    )
    callback_queue = float(constraints.get("manual_callback_queue", 0.0))
    dependency_pressure = float(constraints.get("dependency_pressure", 0.0))
    retry_pressure = float(constraints.get("retry_pressure", 0.0))
    hold_pressure = float(constraints.get("hold_pressure", 0.0))

    if {"retry_amplification", "high_checkout_latency"} & patterns or retry_rate >= 0.22:
        bump("rate_limit_retries", 5.5, "Retry amplification is elevated and early throttling can still break the spiral.")
        bump("increase_retry_backoff", 4.0, "Retry pressure is significant and backoff can smooth residual load.")
        bump("enable_payment_circuit_breaker", 3.0, "Dependency isolation is relevant while retry pressure remains active.")
        selection_rationale.append(
            "Planner Agent detected retry amplification and kept three access-stabilizing actions in contention."
        )

    if {"payment_degradation"} & patterns or eligibility_health <= 0.56 or {"payment", "eligibility"} & services:
        bump("enable_payment_circuit_breaker", 5.5, "Eligibility degradation appears material enough that fail-fast protection may be safest.")
        bump("route_to_callback_queue", 2.5, "Controlled diversion can protect access while dependency health is unstable.")
        bump("restart_payment", 1.5, "The intuitive local reflex stays visible so Guardrail can veto it if backlog is still unsafe.")
        selection_rationale.append(
            "Planner Agent kept dependency-facing actions because eligibility health is part of the incident surface."
        )

    if scenario_id == "retry_death_spiral":
        bump("rate_limit_retries", 3.0, "This scenario rewards early retry throttling before backlog fully entrenches.")
        bump("restart_payment", -2.5, "Restart remains timing-sensitive and unsafe while retry share is still high.")
        if dependency_pressure >= retry_pressure + 0.18 and eligibility_health <= 0.48:
            bump("enable_payment_circuit_breaker", 2.6, "Hidden dependency pressure is dominant enough that fail-fast protection may beat throttle.")
            bump("rate_limit_retries", -1.2, "Retry shaping helps, but dependency isolation becomes more urgent when health is materially degraded.")
        else:
            bump("rate_limit_retries", 1.2, "Retry pressure remains the leading driver of the current surge.")
    if scenario_id == "payment_gateway_flap":
        bump("enable_payment_circuit_breaker", 3.0, "Eligibility flap is the dominant root cause in this scenario family.")
        bump("increase_retry_backoff", 1.5, "Retry smoothing is a viable follow-on recovery move.")
    if scenario_id == "seat_hold_clog" or {"slot_hold_clog", "fairness_breakdown"} & patterns or hold_util >= 0.78:
        bump("shorten_slot_hold_ttl", 6.0, "Scarce slot inventory is trapped in holds and TTL relief directly frees access.")
        bump("route_to_callback_queue", 4.8, "Overflow diversion reduces repeat holding pressure without shutting the portal.")
        bump("reserve_priority_slots", 4.5, "Fairness controls matter when slot concentration is the real pathology.")
        bump("enable_payment_circuit_breaker", -2.5, "Dependency isolation alone does not clear the inventory clog.")
        if hold_pressure >= max(0.55, dependency_pressure + 0.12):
            bump("shorten_slot_hold_ttl", 1.5, "Hidden hold pressure confirms that inventory release should outrank dependency actions.")
            bump("reserve_priority_slots", 1.2, "Fairness-aware capacity protection matters once holds dominate downstream access.")
        if callback_queue <= 8:
            bump("route_to_callback_queue", 3.2, "Callback capacity is still available, so controlled diversion can relieve live contention immediately.")
        selection_rationale.append(
            "Planner Agent recognized slot-hold pathology, so inventory and fairness actions outrank generic dependency fixes."
        )
    if scenario_id == "regional_saturation" or "regional_headroom_risk" in patterns:
        first_step_safe = projected_secondary_headroom > 0.30 and retry_pressure <= 0.36 and queue_depth <= 18
        if first_step_safe:
            bump("shift_traffic", 5.0, "Projected post-shift secondary headroom remains comfortably safe for a cautious diversion.")
        elif projected_secondary_headroom > 0.24:
            bump("shift_traffic", 0.8, "Traffic shift may become viable after stabilization, but retry pressure is still too high for it to be the first move.")
            bump("route_to_callback_queue", 3.8, "Callback diversion preserves headroom while the first move reduces retry-driven stress.")
            bump("rate_limit_retries", 3.0, "Retry shaping is safer as the first step when regional headroom is only moderately safe.")
        else:
            bump("shift_traffic", -4.5, "Projected post-shift headroom would fall into unsafe territory, making shift dangerous.")
            bump("route_to_callback_queue", 4.5, "Demand diversion is safer than consuming unsafe secondary headroom.")
            bump("rate_limit_retries", 2.6, "Retry shaping reduces primary stress without exporting overload into the secondary region.")
        selection_rationale.append(
            f"Planner Agent treated traffic shift as threshold-sensitive using projected post-shift headroom ({projected_secondary_headroom:.2f}) and only allows it as a first step once retry pressure and queue depth are already stabilized."
        )

    if fairness >= 0.18:
        bump("reserve_priority_slots", 2.5, "Fairness skew is high enough that protected-capacity policy may matter.")
        bump("route_to_callback_queue", 1.8, "Callback diversion can reduce repeat attempts from the same cohort.")

    if callback_queue >= 20:
        bump("disable_flag", 2.0, "Manual fallback is already under pressure, so the kill switch remains visible as a last resort.")

    if queue_depth <= 12 and retry_rate <= 0.16:
        bump("restart_payment", 3.0, "Restart becomes more defensible only after queue depth and retry share are already contained.")
    else:
        bump("restart_payment", -3.0, "Restart is unsafe while backlog or retry share remains above stabilization thresholds.")

    bump("disable_flag", -1.5, "Keep the last-resort kill switch visible, but penalize it while safer actions exist.")

    ranked_ids = sorted(
        scores,
        key=lambda action_id: (
            scores[action_id],
            action_by_id[action_id]["category"],
            action_id,
        ),
        reverse=True,
    )
    shortlist_ids = ranked_ids[:limit]
    if "restart_payment" not in shortlist_ids and scenario_id in {"retry_death_spiral", "payment_gateway_flap"}:
        shortlist_ids = shortlist_ids[:-1] + ["restart_payment"]
    shortlist_ids = _dedupe(shortlist_ids)
    shortlist = [action_by_id[action_id] for action_id in shortlist_ids]

    top_reasons = [
        f"{action_id}: {'; '.join(reasons[action_id][:2])}"
        for action_id in shortlist_ids
        if reasons[action_id]
    ]
    selection_rationale.extend(top_reasons[:3])

    return {
        "action_library": library,
        "shortlist": shortlist,
        "selection_strategy": "broader_healthcare_access_action_library -> constraint_aware_shortlist -> stratus_ranking",
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
