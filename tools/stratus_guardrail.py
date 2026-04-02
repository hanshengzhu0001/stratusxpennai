from __future__ import annotations

import json
import os
import re

from openai import OpenAI


def rank_actions(state: dict, actions: list[dict]) -> dict:
    api_key = os.environ.get("STRATUS_API_KEY")
    if not api_key:
        return _mock_ranking(actions)

    client = OpenAI(
        base_url=os.environ.get("STRATUS_BASE_URL", "https://api.stratus.run/v1"),
        api_key=api_key,
    )

    prompt = f"""
You are an incident-response decision guardrail.

Incident state:
{json.dumps(state, indent=2)}

Planner-shortlisted candidate actions:
{json.dumps(actions, indent=2)}

Return valid JSON only with:
- ranked_actions
- best_action
- predicted_effects_by_action
- overall_confidence
""".strip()

    try:
        resp = client.chat.completions.create(
            model=os.environ.get(
                "STRATUS_MODEL", "stratus-x1ac-base-claude-sonnet-4-5"
            ),
            messages=[
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            timeout=20,
        )
        payload = _parse_json_payload(resp.choices[0].message.content or "")
        payload.setdefault("notes", [])
        payload["notes"].append("Used live Stratus ranking.")
        return payload
    except Exception as exc:
        payload = _mock_ranking(actions)
        payload["notes"].append(
            f"Live Stratus call failed; using deterministic fallback: {exc.__class__.__name__}"
        )
        return payload


def _mock_ranking(actions: list[dict]) -> dict:
    ranked_actions = [
        {
            "id": "rate_limit_retries",
            "rank": 1,
            "confidence": 0.84,
            "rationale": "Controls retry amplification directly with low blast radius.",
        },
        {
            "id": "enable_payment_circuit_breaker",
            "rank": 2,
            "confidence": 0.78,
            "rationale": "Fails fast at the dependency edge and contains retry pressure, but may drop some payment attempts.",
        },
        {
            "id": "increase_retry_backoff",
            "rank": 3,
            "confidence": 0.72,
            "rationale": "Reduces retry pressure more gently, but takes longer to stabilize the feedback loop.",
        },
        {
            "id": "restart_payment",
            "rank": 4,
            "confidence": 0.43,
            "rationale": "Tempting local fix, but it can amplify retries while payment is unstable.",
        },
        {
            "id": "shift_traffic",
            "rank": 5,
            "confidence": 0.30,
            "rationale": "Widens exposure without directly reducing the retry storm.",
        },
        {
            "id": "disable_flag",
            "rank": 6,
            "confidence": 0.24,
            "rationale": "Stops the pain quickly, but is too destructive to be the first safe move.",
        },
    ]
    action_ids = {action["id"] for action in actions}
    ranked_actions = [item for item in ranked_actions if item["id"] in action_ids]
    ranked_actions.sort(key=lambda item: item["rank"])
    for index, item in enumerate(ranked_actions, start=1):
        item["rank"] = index
    predicted_effects_by_action = {
        "restart_payment": {
            "latency_direction": "mixed",
            "error_direction": "down",
            "blast_radius": "medium",
            "retry_storm_risk": "high",
        },
        "enable_payment_circuit_breaker": {
            "latency_direction": "down",
            "error_direction": "mixed",
            "blast_radius": "low",
            "retry_storm_risk": "low",
        },
        "increase_retry_backoff": {
            "latency_direction": "down",
            "error_direction": "mixed",
            "blast_radius": "low",
            "retry_storm_risk": "medium",
        },
        "disable_flag": {
            "latency_direction": "down",
            "error_direction": "down",
            "blast_radius": "low",
            "retry_storm_risk": "low",
        },
        "rate_limit_retries": {
            "latency_direction": "down",
            "error_direction": "mixed",
            "blast_radius": "low",
            "retry_storm_risk": "low",
        },
        "shift_traffic": {
            "latency_direction": "mixed",
            "error_direction": "mixed",
            "blast_radius": "high",
            "retry_storm_risk": "medium",
        },
    }
    predicted_effects_by_action = {
        action_id: effect
        for action_id, effect in predicted_effects_by_action.items()
        if action_id in action_ids
    }
    best_action = next((action for action in actions if action["id"] == ranked_actions[0]["id"]), actions[0])
    return {
        "ranked_actions": ranked_actions,
        "best_action": best_action,
        "predicted_effects_by_action": predicted_effects_by_action,
        "overall_confidence": ranked_actions[0]["confidence"],
        "notes": [
            "Using deterministic local ranking because Stratus credentials are missing or network access is unavailable."
        ],
    }


def _parse_json_payload(content: str) -> dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.DOTALL)
        if fenced:
            return json.loads(fenced.group(1))

        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start : end + 1])
        raise
