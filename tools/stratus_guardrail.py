from __future__ import annotations

import json
import os
import re
import signal
import time
from contextlib import contextmanager

from openai import OpenAI

from tools.simulation import projected_secondary_headroom_from_state


def rank_actions(state: dict, actions: list[dict]) -> dict:
    api_key = os.environ.get("STRATUS_API_KEY")
    if not api_key:
        return _mock_ranking(
            state,
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
- Use the hidden operational constraints in the incident state. In particular:
  - slot-hold and fairness pathologies can make inventory actions safer than dependency actions.
  - restart is only safe after backlog and retry pressure are already controlled.
  - traffic shift is threshold-sensitive to secondary-region headroom.
  - rate limiting is strongest when applied early in retry-driven incidents.
- Output JSON only.
""".strip()

    request_timeout_seconds = max(5.0, float(os.environ.get("STRATUS_TIMEOUT_SECONDS", "20")))
    max_attempts = max(1, int(os.environ.get("STRATUS_MAX_ATTEMPTS", "3")))

    try:
        payload = _live_ranking(
            client,
            prompt,
            request_timeout_seconds=request_timeout_seconds,
            max_attempts=max_attempts,
        )
        payload = _normalize_live_payload(payload, state, actions)
        payload.setdefault("notes", [])
        payload["notes"].append("Used live Stratus ranking.")
        return payload
    except Exception as exc:
        return _mock_ranking(
            state,
            actions,
            notes=[
                f"Live Stratus call failed; using deterministic fallback: {exc.__class__.__name__}"
            ],
        )


def _mock_ranking(state: dict, actions: list[dict], notes: list[str] | None = None) -> dict:
    action_ids = {action["id"] for action in actions}
    labels = state.get("labels", {})
    scenario_id = str(labels.get("scenario_id") or state.get("scenario_id") or "")
    metrics = state.get("metrics", {})
    business = state.get("business_metrics", {})
    constraints = state.get("constraints", {})

    retry_rate = float(metrics.get("retry_rate", 0.0))
    eligibility_health = float(constraints.get("eligibility_health", 1.0))
    queue_depth = float(constraints.get("queue_depth", 0.0))
    secondary_headroom = float(constraints.get("regional_headroom_secondary", 1.0))
    projected_secondary_headroom = float(
        constraints.get("projected_secondary_headroom_after_shift", projected_secondary_headroom_from_state(state, shift_fraction=0.10))
    )
    hold_util = float(business.get("seat_hold_utilization", 0.0))
    fairness = float(business.get("fairness_skew", 0.0))
    time_sec = float(constraints.get("time_sec", 0.0))
    dependency_pressure = float(constraints.get("dependency_pressure", 0.0))
    retry_pressure = float(constraints.get("retry_pressure", 0.0))
    hold_pressure = float(constraints.get("hold_pressure", 0.0))

    scorecard = {
        action_id: {"score": 0.0, "reason": "Retained for comparison against safer futures."}
        for action_id in action_ids
    }

    def score(action_id: str, points: float, reason: str) -> None:
        if action_id not in scorecard:
            return
        scorecard[action_id]["score"] += points
        scorecard[action_id]["reason"] = reason

    if retry_rate >= 0.22:
        score("rate_limit_retries", 4.8, "Breaks retry amplification while access remains online.")
        score("increase_retry_backoff", 3.4, "Smooths retry pressure but acts more gradually.")
        score("enable_payment_circuit_breaker", 2.6, "Contains dependency retries when eligibility is a real bottleneck.")

    if scenario_id == "retry_death_spiral":
        if eligibility_health <= 0.46 and dependency_pressure >= retry_pressure + 0.18:
            score("enable_payment_circuit_breaker", 3.4, "Dependency health is bad enough that fail-fast protection matters most.")
        else:
            score("rate_limit_retries", 3.2, "Retry amplification dominates more than dependency degradation right now.")
        if time_sec <= 20:
            score("rate_limit_retries", 1.4, "Early throttle timing still has enough leverage to win.")

    if scenario_id == "payment_gateway_flap":
        score("enable_payment_circuit_breaker", 5.2, "Eligibility instability is the primary root cause here.")
        score("increase_retry_backoff", 2.4, "Backoff is useful after dependency isolation, not before it.")

    if scenario_id == "seat_hold_clog" or hold_util >= 0.78 or fairness >= 0.18:
        score("shorten_slot_hold_ttl", 5.8, "Inventory is trapped in holds, so TTL relief directly restores access.")
        score("route_to_callback_queue", 4.6, "Overflow diversion reduces repeated online contention for scarce slots.")
        score("reserve_priority_slots", 4.1, "Fairness controls matter when hold concentration is the real pathology.")
        score("enable_payment_circuit_breaker", -3.0, "Dependency isolation does not clear the slot-hold clog.")
        if hold_pressure >= max(0.55, dependency_pressure + 0.12):
            score("shorten_slot_hold_ttl", 1.3, "Hidden hold pressure confirms that releasing inventory should happen before dependency isolation.")
        if float(constraints.get("manual_callback_queue", 0.0)) <= 8:
            score("route_to_callback_queue", 3.2, "Staffed callback still has room, so controlled diversion can relieve live contention immediately.")

    if scenario_id == "regional_saturation":
        first_step_safe = projected_secondary_headroom > 0.30 and retry_pressure <= 0.36 and queue_depth <= 18
        if first_step_safe:
            score("shift_traffic", 4.8, "Projected post-shift secondary headroom stays comfortably safe enough for a cautious diversion.")
        elif projected_secondary_headroom > 0.24:
            score("shift_traffic", 0.8, "Traffic shift may become viable after stabilization, but it is risky as the first move.")
            score("route_to_callback_queue", 3.8, "Callback diversion preserves headroom while the first move reduces retry-driven stress.")
            score("rate_limit_retries", 3.0, "Retry shaping is the safer first move when headroom is only moderately safe.")
        else:
            score("shift_traffic", -4.4, "Projected post-shift headroom falls below the safety floor, so shift becomes dangerous.")
            score("route_to_callback_queue", 4.7, "Demand diversion is safer than consuming unsafe secondary headroom.")
            score("rate_limit_retries", 2.6, "Retry shaping reduces primary stress without exporting overload into the secondary region.")

    if queue_depth <= 12 and retry_rate <= 0.16:
        score("restart_payment", 2.2, "Restart becomes safer after backlog and retry pressure are already contained.")
    else:
        score("restart_payment", -3.5, "Tempting local fix, but unsafe while backlog and retry pressure remain high.")

    score("disable_flag", -2.4, "Last-resort kill switch protects systems but harms patient access.")
    ranked_actions = [
        {
            "id": action_id,
            "rank": index,
            "confidence": round(max(0.24, min(0.95, 0.55 + scorecard[action_id]["score"] * 0.05)), 2),
            "rationale": scorecard[action_id]["reason"],
        }
        for index, action_id in enumerate(
            sorted(action_ids, key=lambda action_id: scorecard[action_id]["score"], reverse=True),
            start=1,
        )
    ]
    action_ids = {action["id"] for action in actions}
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
        "shorten_slot_hold_ttl": {
            "latency_direction": "mixed",
            "error_direction": "mixed",
            "blast_radius": "low",
            "retry_storm_risk": "medium",
        },
        "route_to_callback_queue": {
            "latency_direction": "down",
            "error_direction": "mixed",
            "blast_radius": "low",
            "retry_storm_risk": "low",
        },
        "reserve_priority_slots": {
            "latency_direction": "mixed",
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
    if scenario_id == "seat_hold_clog":
        predicted_effects_by_action["shorten_slot_hold_ttl"] = {
            "latency_direction": "down",
            "error_direction": "mixed",
            "blast_radius": "low",
            "retry_storm_risk": "low",
        }
    if scenario_id == "regional_saturation" and projected_secondary_headroom <= 0.24 and "shift_traffic" in predicted_effects_by_action:
        predicted_effects_by_action["shift_traffic"] = {
            "latency_direction": "up",
            "error_direction": "up",
            "blast_radius": "high",
            "retry_storm_risk": "medium",
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


def _live_ranking(
    client: OpenAI,
    prompt: str,
    request_timeout_seconds: float,
    max_attempts: int,
) -> dict:
    model = os.environ.get("STRATUS_MODEL", "stratus-x1ac-base-claude-sonnet-4-5")
    retry_backoff_seconds = max(0.0, float(os.environ.get("STRATUS_RETRY_BACKOFF_SECONDS", "1.5")))
    hard_timeout_seconds = max(request_timeout_seconds + 5.0, request_timeout_seconds * 1.5)
    errors: list[Exception] = []
    for attempt in range(max_attempts):
        try:
            with _hard_timeout(hard_timeout_seconds):
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "Return compact JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    timeout=request_timeout_seconds,
                    temperature=0,
                )
            content = _extract_response_content(resp)
            return _parse_json_payload(content)
        except Exception as exc:
            errors.append(exc)
            if attempt < max_attempts - 1 and retry_backoff_seconds > 0:
                time.sleep(retry_backoff_seconds * (attempt + 1))
    raise errors[-1]


class StratusDeadlineExceeded(TimeoutError):
    pass


@contextmanager
def _hard_timeout(seconds: float):
    if seconds <= 0 or os.name == "nt":
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)

    def _raise_timeout(signum, frame):
        raise StratusDeadlineExceeded(f"Stratus deadline exceeded after {seconds:.1f}s")

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _normalize_live_payload(payload: dict, state: dict, actions: list[dict]) -> dict:
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
    fallback = _mock_ranking(state, actions)
    ranked_or_fallback = ranked or fallback["ranked_actions"]

    predicted = {}
    raw_predicted = payload.get("predicted_effects_by_action", {})
    for action_id in action_by_id:
        source = raw_predicted.get(action_id, {}) or fallback["predicted_effects_by_action"].get(action_id, {})
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
