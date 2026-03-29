from __future__ import annotations


def generate_candidate_actions(state: dict) -> list[dict]:
    actions = [
        {
            "id": "restart_payment",
            "type": "restart",
            "target": "payment",
            "description": "Restart payment pods",
        },
        {
            "id": "disable_flag",
            "type": "flag",
            "target": "payment_feature",
            "description": "Disable the payment feature flag",
        },
        {
            "id": "rate_limit_retries",
            "type": "throttle",
            "target": "checkout",
            "description": "Rate-limit checkout retries",
        },
        {
            "id": "shift_traffic",
            "type": "traffic_shift",
            "target": "secondary_region",
            "description": "Shift traffic to the secondary region",
        },
    ]

    if "payment" not in state.get("services", []):
        return actions[:3]
    return actions
