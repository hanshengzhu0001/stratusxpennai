from __future__ import annotations

import json
import os
import re

from openai import OpenAI


def rank_actions(state: dict, actions: list[dict]) -> dict:
    api_key = os.environ.get("STRATUS_API_KEY")
    if not api_key:
        return _mock_ranking(
            actions,
            notes=[
                "Using deterministic local ranking because STRATUS_API_KEY is not configured."
            ],
        )

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

Return compact JSON only. No markdown fences.

Schema:
{{
  "ranked_actions": [
    {{"id": "action_id", "rank": 1, "confidence": 0.91, "rationale": "short reason"}}
  ],
  "best_action": {{"id": "action_id"}},
  "predicted_effects_by_action": {{
    "action_id": {{
      "latency_direction": "down|up|mixed",
      "error_direction": "down|up|mixed",
      "blast_radius": "low|medium|high",
      "retry_storm_risk": "low|medium|high"
    }}
  }},
  "overall_confidence": 0.91
}}

Rules:
- Use only action ids from the provided shortlist.
- Keep rationale under 18 words.
- Every shortlisted action must appear in ranked_actions and predicted_effects_by_action.
- best_action.id must be one shortlisted action id.
- Output JSON only.
""".strip()

    try:
        payload = _live_ranking(client, prompt)
        payload = _normalize_live_payload(payload, actions)
        payload.setdefault("notes", [])
        payload["notes"].append("Used live Stratus ranking.")
        return payload
    except Exception as exc:
        return _mock_ranking(
            actions,
            notes=[
                f"Live Stratus call failed; using deterministic fallback: {exc.__class__.__name__}"
            ],
        )


def _mock_ranking(actions: list[dict], notes: list[str] | None = None) -> dict:
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
        "notes": notes or [
            "Using deterministic local ranking because live Stratus was not available."
        ],
    }


def _live_ranking(client: OpenAI, prompt: str) -> dict:
    model = os.environ.get("STRATUS_MODEL", "stratus-x1ac-base-claude-sonnet-4-5")
    errors: list[Exception] = []
    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Return compact JSON only."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                timeout=30,
                temperature=0,
            )
            content = _extract_response_content(resp)
            return _parse_json_payload(content)
        except Exception as exc:
            errors.append(exc)
    raise errors[-1]


def _normalize_live_payload(payload: dict, actions: list[dict]) -> dict:
    action_by_id = {action["id"]: action for action in actions}
    ranked = []
    for index, item in enumerate(payload.get("ranked_actions", []), start=1):
        action_id = item.get("id")
        if action_id not in action_by_id:
            continue
        ranked.append(
            {
                "id": action_id,
                "rank": int(item.get("rank", index)),
                "confidence": float(item.get("confidence", 0.0)),
                "rationale": str(item.get("rationale", "")).strip(),
            }
        )
    ranked.sort(key=lambda item: item["rank"])
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    fallback = _mock_ranking(actions)
    ranked_or_fallback = ranked or fallback["ranked_actions"]

    predicted = {}
    raw_predicted = payload.get("predicted_effects_by_action", {})
    for action_id in action_by_id:
        source = raw_predicted.get(action_id, {})
        predicted[action_id] = {
            "latency_direction": _enum_or_default(source.get("latency_direction"), "mixed"),
            "error_direction": _enum_or_default(source.get("error_direction"), "mixed"),
            "blast_radius": _enum_or_default(source.get("blast_radius"), "medium"),
            "retry_storm_risk": _enum_or_default(source.get("retry_storm_risk"), "medium"),
        }

    best_raw = payload.get("best_action", {})
    best_id = best_raw.get("id") if isinstance(best_raw, dict) else str(best_raw)
    best_action = action_by_id.get(best_id)
    if not best_action:
        fallback_best_id = ranked_or_fallback[0]["id"] if ranked_or_fallback else actions[0]["id"]
        best_action = action_by_id.get(fallback_best_id, actions[0])

    overall_confidence = payload.get("overall_confidence")
    if overall_confidence is None and ranked_or_fallback:
        overall_confidence = ranked_or_fallback[0]["confidence"]

    return {
        "ranked_actions": ranked_or_fallback,
        "best_action": best_action,
        "predicted_effects_by_action": predicted,
        "overall_confidence": float(overall_confidence or 0.0),
    }


def _extract_response_content(resp: object) -> str:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        raise ValueError("Stratus returned no choices")

    message = getattr(choices[0], "message", None)
    if message is None:
        raise ValueError("Stratus choice was missing a message")

    content = getattr(message, "content", None)
    if isinstance(content, str):
        text = content.strip()
        if text:
            return text

    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
                continue
            text = getattr(part, "text", None)
            if text:
                parts.append(str(text))
        combined = "".join(parts).strip()
        if combined:
            return combined

    refusal = getattr(message, "refusal", None)
    if refusal:
        raise ValueError(f"Stratus refused the request: {refusal}")

    raise ValueError("Stratus response content was empty")


def _enum_or_default(value: object, default: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"down", "up", "mixed", "low", "medium", "high"}:
        return normalized
    return default


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
