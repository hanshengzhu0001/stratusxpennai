"""
Case library — file-based persistence for completed incident response cases.

Each case is a JSON file in the cases/ directory. Cases are append-only
and identified by {scenario_id}_{timestamp} slugs.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import CASES_DIR as _CASES_DIR_STR

CASES_DIR = Path(_CASES_DIR_STR)


def save_case(verdict: dict, plan: dict, scenario: dict) -> str | None:
    """
    Persist a complete case record from a pipeline run.

    Returns the case_id string, or None if the write fails.
    """
    timestamp = datetime.now(timezone.utc)
    case_id = _generate_case_id(scenario["id"], timestamp)

    chosen_id = plan["best_action"]["id"]
    ranking = plan.get("stratus_ranking", plan.get("baseline_ranking", []))

    case = {
        "case_id": case_id,
        "scenario_id": scenario["id"],
        "timestamp": timestamp.isoformat(),
        "mode": verdict.get("mode", "stratus"),

        "situation": {
            "incident_summary": plan.get("incident_summary", ""),
            "services": plan["observed_condition"].get("services", []),
            "metrics_before": plan["observed_condition"]["metrics"],
            "pattern_matches": plan["observed_condition"].get("pattern_matches", []),
        },

        "decision": {
            "shortlisted_actions": [a["id"] for a in plan.get("candidate_actions", [])],
            "chosen_action": chosen_id,
            "ranking": ranking,
            "mode": verdict.get("mode", "stratus"),
        },

        "outcome": {
            "predicted": plan.get("predicted_effects_by_action", {}).get(chosen_id, {}),
            "actual": verdict["outcome_evaluation"]["metrics_after"],
            "drift_score": verdict["outcome_evaluation"]["drift_score"],
            "recovery_class": verdict["outcome_evaluation"]["recovery_class"],
        },

        "verdict": {
            "classification": verdict["verdict"]["classification"],
            "narrative": verdict["verdict"]["narrative"],
            "dangerous_reflex_rejected": verdict["verdict"]["dangerous_reflex_rejected"],
            "baseline_comparison": {
                "baseline_chose": verdict["decision_comparison"]["baseline_chose"],
                "baseline_correct": verdict["decision_comparison"]["baseline_correct"],
                "stratus_chose": verdict["decision_comparison"]["stratus_chose"],
                "stratus_correct": verdict["decision_comparison"]["stratus_correct"],
            },
        },
    }

    try:
        CASES_DIR.mkdir(parents=True, exist_ok=True)
        path = _case_path(case_id)
        path.write_text(json.dumps(case, indent=2), encoding="utf-8")
        return case_id
    except OSError as exc:
        print(f"WARNING: Failed to save case {case_id}: {exc}", file=sys.stderr)
        return None


def load_case(case_id: str) -> dict:
    """
    Load a single case by its case_id.

    Raises FileNotFoundError if the case file does not exist.
    """
    path = _case_path(case_id)
    if not path.exists():
        raise FileNotFoundError(f"Case not found: {case_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_cases(scenario_id: str | None = None) -> list[dict]:
    """
    List cases with lightweight metadata.

    Returns list of dicts with keys: case_id, scenario_id, timestamp,
    mode, chosen_action, recovery_class. Sorted by timestamp descending.
    """
    if not CASES_DIR.exists():
        return []

    pattern = f"{scenario_id}_*.json" if scenario_id else "*.json"
    cases = []
    for path in CASES_DIR.glob(pattern):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cases.append({
                "case_id": data["case_id"],
                "scenario_id": data["scenario_id"],
                "timestamp": data["timestamp"],
                "mode": data.get("mode", "unknown"),
                "chosen_action": data["decision"]["chosen_action"],
                "recovery_class": data["outcome"]["recovery_class"],
            })
        except (json.JSONDecodeError, KeyError) as exc:
            print(f"WARNING: Skipping malformed case file {path}: {exc}", file=sys.stderr)
            continue

    cases.sort(key=lambda c: c["timestamp"], reverse=True)
    return cases


def find_similar(scenario_id: str, action_id: str) -> list[dict]:
    """Find prior cases with the same scenario and chosen action."""
    all_cases = list_cases(scenario_id)
    return [c for c in all_cases if c["chosen_action"] == action_id]


def _case_path(case_id: str) -> Path:
    """Return the file path for a case."""
    return CASES_DIR / f"{case_id}.json"


def _generate_case_id(scenario_id: str, timestamp: datetime) -> str:
    """Generate a unique, sortable case ID."""
    ts = timestamp.strftime("%Y%m%d_%H%M%S")
    return f"{scenario_id}_{ts}"
