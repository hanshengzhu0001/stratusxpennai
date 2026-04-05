"""
Evaluation agent — produces structured verdicts from plan and outcome data.

Pure Python, deterministic, no LLM calls. Verdicts are generated from
rule-based classification and template-based narrative generation.
"""
from __future__ import annotations

from datetime import datetime, timezone

from agents.simulator import compare_prediction_to_actual


def evaluate(plan: dict, actual_outcome: dict, scenario: dict) -> dict:
    """
    Produce a structured verdict from a completed pipeline run.

    Args:
        plan:           The plan report dict from build_plan_report().
        actual_outcome: The actual outcome dict (from actual_outcome_from_evidence
                        or simulate_action).
        scenario:       The scenario profile dict from load_scenario().

    Returns:
        A verdict dict with keys: scenario_id, timestamp, mode,
        decision_comparison, outcome_evaluation, verdict, case_summary.
    """
    chosen = plan["best_action"]
    chosen_id = chosen["id"]
    gt_id = scenario["ground_truth"]["action_id"]
    naive_id = scenario["naive_reflex"]["action_id"]
    labels = scenario["evaluation_labels"]

    # Determine which rankers chose what
    baseline_chose = plan.get("baseline_best_action", {}).get("id", "unknown")
    stratus_ranking = plan.get("stratus_ranking", [])
    stratus_chose = stratus_ranking[0]["id"] if stratus_ranking else "unknown"

    # Determine mode based on which ranker's pick was used as best_action
    mode = plan.get("mode", "stratus")

    # --- Decision comparison ---
    decision_comparison = {
        "stratus_chose": stratus_chose,
        "baseline_chose": baseline_chose,
        "ground_truth": gt_id,
        "stratus_correct": stratus_chose == gt_id,
        "baseline_correct": baseline_chose == gt_id,
        "stratus_confidence": plan.get("overall_confidence", 0.0),
        "baseline_confidence": plan.get("baseline_confidence", 0.0),
    }

    # --- Outcome evaluation ---
    predicted = plan.get("predicted_effects_by_action", {}).get(chosen_id, {})
    comparison = compare_prediction_to_actual(chosen_id, predicted, actual_outcome)
    drift_score = comparison.get("score", 0.0)
    recovery_class = _classify_recovery(drift_score, actual_outcome)

    before_metrics = plan["observed_condition"]["metrics"]
    after_metrics = {
        "latency_p95_ms": actual_outcome.get("latency_p95_ms", 0),
        "error_rate": actual_outcome.get("error_rate", 0.0),
        "retry_rate": actual_outcome.get("retry_rate", 0.0),
    }

    outcome_evaluation = {
        "predicted_vs_actual": comparison,
        "drift_score": drift_score,
        "recovery_class": recovery_class,
        "metrics_before": before_metrics,
        "metrics_after": after_metrics,
    }

    # --- Verdict ---
    narrative = _generate_narrative(recovery_class, plan, actual_outcome, scenario)
    dangerous_reflex_rejected = (chosen_id != naive_id) and (mode == "stratus")

    verdict = {
        "classification": recovery_class,
        "dangerous_reflex_rejected": dangerous_reflex_rejected,
        "reflex_action": naive_id,
        "reflex_rejected_reason": labels["reflex_rejected_reason"],
        "safe_action_chosen": chosen_id == gt_id,
        "blast_radius_avoided": labels["blast_radius_avoided"],
        "fairness_preserved": chosen_id == gt_id,
        "narrative": narrative,
    }

    # --- Case summary ---
    case_summary = {
        "situation": plan.get("incident_summary", ""),
        "shortlisted_actions": [a["id"] for a in plan.get("candidate_actions", [])],
        "chosen_action": chosen_id,
        "predicted_result": predicted,
        "actual_result": actual_outcome,
        "narrative_verdict": _one_sentence_verdict(recovery_class, chosen_id, gt_id),
    }

    return {
        "scenario_id": scenario["id"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "decision_comparison": decision_comparison,
        "outcome_evaluation": outcome_evaluation,
        "verdict": verdict,
        "case_summary": case_summary,
    }


# ---------------------------------------------------------------------------
# Recovery classification
# ---------------------------------------------------------------------------

def _classify_recovery(drift_score: float, actual_outcome: dict) -> str:
    """
    Classify the recovery outcome.

    Rules (first match wins):
      severe_failure:  drift_score < 0.5, OR recovery==mixed AND blast in (high, very_high)
      partial_failure: drift_score < 0.75, OR recovery==partial
      strong_recovery: all other cases
    """
    recovery = actual_outcome.get("recovery", "partial")
    blast = actual_outcome.get("blast_radius", "medium")

    if drift_score < 0.5:
        return "severe_failure"
    if recovery == "mixed" and blast in ("high", "very_high"):
        return "severe_failure"

    if drift_score < 0.75:
        return "partial_failure"
    if recovery == "partial":
        return "partial_failure"

    return "strong_recovery"


# ---------------------------------------------------------------------------
# Narrative generation
# ---------------------------------------------------------------------------

_TEMPLATES = {
    "strong_recovery": (
        "The system correctly identified that {reflex_desc} — {reflex_why} — "
        "would not resolve the underlying issue. Instead, it chose {safe_desc}, "
        "which {safe_rationale}. Post-action metrics confirm strong recovery: "
        "latency dropped from {lat_before}ms to {lat_after}ms, "
        "retry rate fell from {retry_before} to {retry_after}, "
        "and blast radius remained {blast_after}."
    ),
    "partial_failure": (
        "The chosen action ({chosen_desc}) showed partial improvement: "
        "latency moved from {lat_before}ms to {lat_after}ms, "
        "retry rate changed from {retry_before} to {retry_after}. "
        "However, recovery is classified as {recovery} with {blast_after} blast radius. "
        "The system may need to replan."
    ),
    "severe_failure": (
        "The chosen action ({chosen_desc}) did not achieve the predicted outcome. "
        "Latency is {lat_after}ms (was {lat_before}ms), "
        "error rate is {err_after} (was {err_before}). "
        "Blast radius is {blast_after} and recovery is {recovery}. "
        "Immediate replanning is recommended."
    ),
}


def _generate_narrative(
    classification: str,
    plan: dict,
    actual_outcome: dict,
    scenario: dict,
) -> str:
    """Fill the narrative template with concrete values from the run."""
    labels = scenario["evaluation_labels"]
    before = plan["observed_condition"]["metrics"]
    chosen = plan["best_action"]
    naive = scenario["naive_reflex"]

    values = {
        "reflex_desc": _action_description(naive["action_id"], plan),
        "reflex_why": naive["why"],
        "safe_desc": _action_description(labels["safe_action"], plan),
        "safe_rationale": scenario["ground_truth"]["why"],
        "chosen_desc": _action_description(chosen["id"], plan),
        "lat_before": before.get("latency_p95_ms", "?"),
        "lat_after": actual_outcome.get("latency_p95_ms", "?"),
        "err_before": before.get("error_rate", "?"),
        "err_after": actual_outcome.get("error_rate", "?"),
        "retry_before": before.get("retry_rate", "?"),
        "retry_after": actual_outcome.get("retry_rate", "?"),
        "blast_after": actual_outcome.get("blast_radius", "?"),
        "recovery": actual_outcome.get("recovery", "?"),
    }

    template = _TEMPLATES.get(classification, _TEMPLATES["partial_failure"])
    return template.format(**values)


def _action_description(action_id: str, plan: dict) -> str:
    """Look up human-readable description for an action_id from the plan."""
    for action in plan.get("candidate_actions", []):
        if action["id"] == action_id:
            return f"{action['description'].lower()} ({action_id})"
    return action_id


def _one_sentence_verdict(classification: str, chosen_id: str, ground_truth_id: str) -> str:
    """Generate a one-sentence verdict for the case summary."""
    if classification == "strong_recovery":
        if chosen_id == ground_truth_id:
            return "Strong recovery. The guardrail rejected the dangerous reflex and chose the safer action."
        return "Strong recovery, though the chosen action differs from the expected ground truth."
    if classification == "partial_failure":
        return "Partial recovery. Some metrics improved but key constraints remain violated."
    return "Severe failure. The action did not produce the predicted outcome. Replan needed."
