"""
Baseline incident response action chooser.

Takes the same (state, actions) input as stratus_guardrail.rank_actions()
and returns the same output shape -- but uses a naive single-shot LLM call
or keyword heuristic instead of Stratus multi-agent reasoning.

This is the control group. Deliberately simple and weak.
"""
from __future__ import annotations

import json
import sys

from config import BASELINE_CHOSEN_CONFIDENCE, BASELINE_LLM_TEMPERATURE, BASELINE_OTHER_CONFIDENCE
from tools.llm_client import API_KEY, BASE_URL, MODEL, is_configured


BASELINE_PROMPT = """\
You are a junior on-call engineer who just got paged at 3am. You are tired and want to \
fix this as fast as possible and go back to sleep. Pick the quickest, most obvious fix. \
Do NOT overthink. Do NOT analyze root causes. Do NOT consider cascading effects on other services. \
Just pick the action that directly addresses the most visible symptom right now.

Incident state:
{state_json}

Candidate actions:
{actions_json}

Return ONLY valid JSON:
{{"best_action": "<action_id>", "reason": "<one sentence>"}}"""


# Keyword rules for fallback when no LLM is configured
KEYWORD_RULES = [
    {"trigger": "retry", "prefer": "restart"},
    {"trigger": "oom", "prefer": "restart"},
    {"trigger": "latency", "prefer": "restart"},
    {"trigger": "crash", "prefer": "restart"},
    {"trigger": "timeout", "prefer": "restart"},
    {"trigger": "cpu", "prefer": "shift_traffic"},
    {"trigger": "memory", "prefer": "restart"},
]


def choose_action(state: dict, actions: list[dict]) -> dict:
    """Pick an action. Returns same shape as stratus_guardrail.rank_actions()."""
    if not actions:
        print("No candidate actions provided.", file=sys.stderr)
        sys.exit(1)
    if is_configured():
        return _choose_via_llm(state, actions)
    else:
        print(
            "WARNING: LLM not configured (set BASELINE_LLM_API_KEY). Using keyword fallback.",
            file=sys.stderr,
        )
        return _choose_via_keywords(state, actions)


def _choose_via_llm(state: dict, actions: list[dict]) -> dict:
    try:
        from openai import OpenAI
    except ImportError:
        print(
            "openai package not installed. Run: pip install openai",
            file=sys.stderr,
        )
        sys.exit(1)

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=30)

    prompt = BASELINE_PROMPT.format(
        state_json=json.dumps(state, indent=2),
        actions_json=json.dumps(actions, indent=2),
    )

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=BASELINE_LLM_TEMPERATURE,
        )
    except Exception as exc:
        print(
            f"LLM API call failed ({exc.__class__.__name__}): {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not response.choices or response.choices[0].message.content is None:
        print("LLM returned empty response (no choices or content=None).", file=sys.stderr)
        sys.exit(1)

    raw = response.choices[0].message.content.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        raw = "\n".join(lines).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        print(f"LLM returned non-JSON output: {raw[:200]}", file=sys.stderr)
        sys.exit(1)

    if "best_action" not in parsed:
        print(f"LLM response missing 'best_action' key: {parsed}", file=sys.stderr)
        sys.exit(1)

    chosen_id = parsed["best_action"]
    reason = parsed.get("reason", "LLM baseline pick.")

    return _build_result(chosen_id, reason, actions, source="llm")


def _choose_via_keywords(state: dict, actions: list[dict]) -> dict:
    """Naive keyword matching fallback."""
    incident_text = " ".join([
        state.get("summary", ""),
        " ".join(state.get("logs", [])),
        " ".join(str(v) for v in state.get("pattern_matches", [])),
    ]).lower()

    scores: dict[str, int] = {}
    for action in actions:
        label = (action.get("description", "") + " " + action["id"]).lower()
        score = 0
        for rule in KEYWORD_RULES:
            if rule["trigger"] in incident_text and rule["prefer"] in label:
                score += 1
        scores[action["id"]] = score

    best = max(actions, key=lambda a: scores[a["id"]])
    best_score = scores[best["id"]]
    if best_score == 0:
        reason = "No keyword rules matched; picked first action arbitrarily."
    else:
        reason = f"Keyword match (score: {best_score})."
    return _build_result(best["id"], reason, actions, source="keyword_fallback")


def _build_result(chosen_id: str, reason: str, actions: list[dict], source: str) -> dict:
    """Build result matching stratus_guardrail.rank_actions() output shape."""
    # Find the full action dict
    chosen_action = next((a for a in actions if a["id"] == chosen_id), None)
    if chosen_action is None:
        raise ValueError(
            f"Baseline chose unknown action_id: '{chosen_id}'. "
            f"Valid IDs: {[a['id'] for a in actions]}"
        )

    # Build a flat ranking: chosen = rank 1, rest in original order
    ranked = [{"id": chosen_id, "rank": 1, "confidence": BASELINE_CHOSEN_CONFIDENCE, "rationale": reason}]
    rank = 2
    for a in actions:
        if a["id"] != chosen_id:
            ranked.append({"id": a["id"], "rank": rank, "confidence": BASELINE_OTHER_CONFIDENCE, "rationale": "Not chosen."})
            rank += 1

    # Populate with neutral "unknown" predictions so downstream comparison
    # code can distinguish "no prediction" from "predicted nothing"
    predicted_effects = {
        a["id"]: {
            "latency_direction": "mixed",
            "error_direction": "mixed",
            "blast_radius": "medium",
            "retry_storm_risk": "medium",
        }
        for a in actions
    }

    return {
        "ranked_actions": ranked,
        "best_action": chosen_action,
        "predicted_effects_by_action": predicted_effects,
        "overall_confidence": BASELINE_CHOSEN_CONFIDENCE,
        "notes": [f"Baseline chooser ({source}). No Stratus reasoning."],
    }
