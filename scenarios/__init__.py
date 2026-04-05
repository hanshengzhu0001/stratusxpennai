"""
Scenario profile loader.

Each scenario is a JSON file in the scenarios/ directory that defines
an incident's evidence, candidate actions, simulated outcomes, ground truth,
and evaluation labels. Profiles are loaded once and cached in memory.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_SCENARIOS_DIR = Path(__file__).parent
_cache: dict[str, dict] = {}

REQUIRED_KEYS = frozenset({
    "id",
    "name",
    "description",
    "evidence",
    "initial_state",
    "candidate_actions",
    "simulated_outcomes",
    "ground_truth",
    "naive_reflex",
    "evaluation_labels",
})


def load_scenario(scenario_id: str) -> dict:
    """
    Load and validate a scenario profile by ID.

    Args:
        scenario_id: Matches the JSON filename (without extension).

    Returns:
        The full scenario dict, validated and cached.

    Raises:
        FileNotFoundError: If the scenario file does not exist.
        ValueError: If the scenario JSON is missing required keys.
    """
    if not re.fullmatch(r"[a-z0-9_]+", scenario_id):
        raise ValueError(
            f"Invalid scenario ID '{scenario_id}'. "
            f"IDs must contain only lowercase letters, digits, and underscores."
        )

    if scenario_id in _cache:
        return _cache[scenario_id]

    path = _SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        available = list_scenarios()
        raise FileNotFoundError(
            f"Scenario '{scenario_id}' not found at {path}. "
            f"Available scenarios: {available}"
        )

    data = json.loads(path.read_text(encoding="utf-8"))
    _validate(data, scenario_id)
    _cache[scenario_id] = data
    return data


def list_scenarios() -> list[str]:
    """Return a sorted list of available scenario IDs."""
    return sorted(p.stem for p in _SCENARIOS_DIR.glob("*.json"))


def get_scenario_metadata(scenario_id: str) -> dict:
    """Return lightweight metadata for a scenario."""
    scenario = load_scenario(scenario_id)
    return {
        "id": scenario["id"],
        "name": scenario["name"],
        "description": scenario["description"],
        "ground_truth_action": scenario["ground_truth"]["action_id"],
    }


def _validate(data: dict, scenario_id: str) -> None:
    """Validate that a scenario profile has all required keys and cross-references."""
    missing = REQUIRED_KEYS - set(data.keys())
    if missing:
        raise ValueError(
            f"Scenario '{scenario_id}' is missing required keys: {sorted(missing)}"
        )

    gt_id = data["ground_truth"]["action_id"]
    outcome_ids = set(data["simulated_outcomes"].keys())
    action_ids = {a["id"] for a in data["candidate_actions"]}

    if gt_id not in outcome_ids:
        raise ValueError(
            f"Scenario '{scenario_id}': ground_truth action '{gt_id}' "
            f"not found in simulated_outcomes keys: {outcome_ids}"
        )
    if gt_id not in action_ids:
        raise ValueError(
            f"Scenario '{scenario_id}': ground_truth action '{gt_id}' "
            f"not found in candidate_actions: {action_ids}"
        )

    for action in data["candidate_actions"]:
        if action["id"] not in outcome_ids:
            raise ValueError(
                f"Scenario '{scenario_id}': candidate action '{action['id']}' "
                f"has no simulated outcome"
            )

    # Validate sub-keys in ground_truth and naive_reflex
    if "why" not in data["ground_truth"]:
        raise ValueError(
            f"Scenario '{scenario_id}': ground_truth missing 'why' field"
        )
    if "why" not in data["naive_reflex"]:
        raise ValueError(
            f"Scenario '{scenario_id}': naive_reflex missing 'why' field"
        )
