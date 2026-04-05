# Implementation Architecture: Eason Week 2

**Author:** Eason (Zhou Yincheng)
**Date:** 2026-04-04
**Status:** Ready for implementation
**Companion:** [Design Document](eason-week2-evaluation-baseline-cases.md)

---

This document specifies **exactly what to build, how every function behaves, what every data structure looks like, and how every module connects**. A developer should be able to implement each file by reading only the relevant section.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [File Inventory](#2-file-inventory)
3. [Module 1: Scenario Profiles](#3-module-1-scenario-profiles)
4. [Module 2: Evaluation Agent](#4-module-2-evaluation-agent)
5. [Module 3: Case Library](#5-module-3-case-library)
6. [Module 4: Baseline Toggle & Pipeline Changes](#6-module-4-baseline-toggle--pipeline-changes)
7. [Module 5: Verdict View](#7-module-5-verdict-view)
8. [Existing Module Changes](#8-existing-module-changes)
9. [Data Flow Diagrams](#9-data-flow-diagrams)
10. [Complete Scenario Data](#10-complete-scenario-data)
11. [Error Handling Contract](#11-error-handling-contract)
12. [Integration Test Specifications](#12-integration-test-specifications)
13. [Implementation Order](#13-implementation-order)

---

## 1. System Overview

### Before (Week 1)

```
alerts/latest.json
    │
    ▼
run.py --phase plan|verify|auto
    │
    ├── collect_evidence()          ← tools/prometheus_client.py (mock or live)
    ├── classify_incident()         ← agents/incident_classifier.py
    ├── generate_candidate_actions()← agents/candidate_actions.py (hardcoded 4 actions)
    ├── rank_actions()              ← tools/stratus_guardrail.py
    ├── baseline_choose()           ← agents/baseline.py
    ├── execute_action()            ← tools/execute_action.py → tools/runtime_state.py
    ├── compare_prediction_to_actual() ← agents/simulator.py (hardcoded SIMULATED_OUTCOMES)
    │
    ▼
outputs/alert_latest_{plan,report,browser_playbook}.json
```

**Problems:** One scenario. Hardcoded outcomes. No structured verdict. No case persistence. Baseline runs as separate script.

### After (Week 2)

```
run.py --scenario retry_death_spiral --mode stratus --phase auto
    │
    ├── load_scenario()             ← scenarios/__init__.py (NEW)
    ├── reset_state_for_scenario()  ← tools/runtime_state.py (MODIFIED)
    ├── collect_evidence()          ← tools/prometheus_client.py (scenario overlay)
    ├── classify_incident()         ← agents/incident_classifier.py (scenario overlay)
    ├── generate_candidate_actions()← agents/candidate_actions.py (from scenario)
    ├── rank_actions()              ← tools/stratus_guardrail.py (unchanged)
    ├── baseline_choose()           ← agents/baseline.py (unchanged)
    ├── [mode switch: pick primary] ← run.py logic
    ├── execute_action()            ← tools/execute_action.py (unchanged)
    ├── simulate_action()           ← agents/simulator.py (MODIFIED: reads scenario outcomes)
    ├── evaluate()                  ← agents/evaluator.py (NEW)
    ├── save_case()                 ← tools/case_library.py (NEW)
    │
    ▼
outputs/{scenario_id}_{plan,report,verdict,browser_playbook}.json
cases/{scenario_id}_{timestamp}.json
/verdict endpoint renders verdict HTML
```

---

## 2. File Inventory

### New Files (5)

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `scenarios/__init__.py` | ~80 | Scenario loader with validation and caching |
| `scenarios/retry_death_spiral.json` | ~90 | Flagship scenario profile |
| `scenarios/payment_gateway_flap.json` | ~90 | Secondary scenario profile |
| `scenarios/seat_hold_clog.json` | ~90 | Stretch scenario profile |
| `agents/evaluator.py` | ~200 | Verdict generation engine |
| `tools/case_library.py` | ~100 | Case persistence and retrieval |

### Modified Files (4)

| File | Change Scope | What Changes |
|------|-------------|--------------|
| `run.py` | ~60 lines added/changed | Add `--mode`, `--scenario` args; wire evaluator + case library; scenario-aware evidence/actions |
| `agents/simulator.py` | ~20 lines changed | Accept optional `scenario` param; load outcomes from scenario instead of hardcoded dict |
| `config.py` | ~10 lines added | Add scenario/case constants |
| `tools/visual_control_plane.py` | ~80 lines added | Add `/verdict` and `/verdict/{scenario_id}` endpoints |

### Unchanged Files

All other files remain exactly as they are: `agents/baseline.py`, `agents/candidate_actions.py`, `agents/incident_classifier.py`, `tools/alert_receiver.py`, `tools/execute_action.py`, `tools/llm_client.py`, `tools/metrics_target.py`, `tools/prometheus_client.py`, `tools/runtime_state.py`, `tools/stratus_guardrail.py`.

---

## 3. Module 1: Scenario Profiles

### 3.1 `scenarios/__init__.py` — Complete Specification

```python
"""
Scenario profile loader.

Each scenario is a JSON file in the scenarios/ directory that defines
an incident's evidence, candidate actions, simulated outcomes, ground truth,
and evaluation labels. Profiles are loaded once and cached in memory.
"""
from __future__ import annotations

import json
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
                     Example: "retry_death_spiral" loads scenarios/retry_death_spiral.json

    Returns:
        The full scenario dict, validated and cached.

    Raises:
        FileNotFoundError: If the scenario file does not exist.
            Message includes available scenarios.
        ValueError: If the scenario JSON is missing required keys.
    """
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
    """
    Return a sorted list of available scenario IDs.

    Scans the scenarios/ directory for .json files.
    Excludes __init__.py and any non-JSON files.
    """
    return sorted(
        p.stem for p in _SCENARIOS_DIR.glob("*.json")
    )


def get_scenario_metadata(scenario_id: str) -> dict:
    """
    Return lightweight metadata for a scenario without loading the full profile.

    Returns:
        {"id": "...", "name": "...", "description": "...", "ground_truth_action": "..."}
    """
    scenario = load_scenario(scenario_id)
    return {
        "id": scenario["id"],
        "name": scenario["name"],
        "description": scenario["description"],
        "ground_truth_action": scenario["ground_truth"]["action_id"],
    }


def _validate(data: dict, scenario_id: str) -> None:
    """
    Validate that a scenario profile has all required keys.

    Raises ValueError with a clear message listing what is missing.
    """
    missing = REQUIRED_KEYS - set(data.keys())
    if missing:
        raise ValueError(
            f"Scenario '{scenario_id}' is missing required keys: {sorted(missing)}"
        )

    # Validate ground_truth references a real action
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

    # Validate every candidate action has a simulated outcome
    for action in data["candidate_actions"]:
        if action["id"] not in outcome_ids:
            raise ValueError(
                f"Scenario '{scenario_id}': candidate action '{action['id']}' "
                f"has no simulated outcome"
            )
```

### 3.2 Scenario JSON Schema — Complete Field Reference

Every scenario JSON file **must** contain all of these fields. No optional fields — every scenario is fully self-contained.

```
{
  "id":                  string    — unique slug, matches filename
  "name":                string    — human-readable display name
  "description":         string    — one-sentence description of the incident

  "alert_template": {              — Alertmanager payload template
    "commonLabels":      object    — labels for the simulated alert
    "commonAnnotations": object    — annotations (must include "summary")
  }

  "evidence": {                    — mock evidence (used when Prometheus unavailable)
    "services":          string[]  — affected services
    "metrics": {
      "latency_p95_ms":  int      — baseline latency before action
      "error_rate":      float    — baseline error rate before action
      "retry_rate":      float    — baseline retry rate before action
    }
    "logs":              string[]  — simulated log lines
    "traces":            string[]  — simulated trace descriptions
  }

  "initial_state": {               — runtime state flags at incident start
    "payment_service_unreachable":   bool
    "loadgenerator_flood_homepage":  bool
    "retry_rate_limit_enabled":      bool
    "traffic_shift_enabled":         bool
    "payment_feature_disabled":      bool
  }

  "candidate_actions": [           — ordered list of possible remediations
    {
      "id":              string    — unique action identifier
      "type":            string    — action category (restart|flag|throttle|traffic_shift)
      "target":          string    — what the action targets
      "description":     string    — human-readable description
    }
  ]

  "simulated_outcomes": {          — deterministic outcome per action
    "<action_id>": {
      "latency_p95_ms":            int
      "error_rate":                float
      "retry_rate":                float
      "time_to_effect_seconds":    int
      "risk_level":                string   — low|medium|high|very_high
      "blast_radius":              string   — low|medium|high
      "recovery":                  string   — strong|partial|mixed
      "notes":                     string   — explanation of why this outcome occurs
    }
  }

  "ground_truth": {                — the correct action for this scenario
    "action_id":         string    — must exist in simulated_outcomes
    "why":               string    — one-sentence rationale
  }

  "naive_reflex": {                — what a naive engineer would pick
    "action_id":         string    — must exist in simulated_outcomes
    "why":               string    — one-sentence explanation of the naive reasoning
  }

  "evaluation_labels": {           — labels for verdict narrative generation
    "dangerous_reflex":          string   — action_id of the dangerous choice
    "safe_action":               string   — action_id of the safe choice
    "reflex_rejected_reason":    string   — multi-sentence explanation
    "blast_radius_avoided":      string   — e.g. "medium → low"
    "fairness_impact":           string   — impact on business fairness
  }
}
```

### 3.3 Scenario 1: `retry_death_spiral.json`

This is the **flagship** scenario. It formalizes the currently hardcoded incident.

**Incident narrative:** Payment service degrades. Checkout retries amplify the problem. A tired engineer would restart payment pods, but that doesn't stop the retry loop — the recovering pods get hammered again. The correct action is rate-limiting retries.

```json
{
  "id": "retry_death_spiral",
  "name": "Retry Death Spiral",
  "description": "Payment dependency degrades, checkout retries amplify into a death spiral, latency spikes across the checkout path.",

  "alert_template": {
    "commonLabels": {
      "alertname": "CheckoutRetryStorm",
      "severity": "critical",
      "service": "checkout",
      "dependency": "payment"
    },
    "commonAnnotations": {
      "summary": "Retry pressure above baseline — checkout retry rate exceeds 0.25 threshold with payment service degraded."
    }
  },

  "evidence": {
    "services": ["frontend", "checkout", "payment"],
    "metrics": {
      "latency_p95_ms": 2300,
      "error_rate": 0.18,
      "retry_rate": 0.31
    },
    "logs": [
      "payment timeout spikes observed",
      "checkout retries exceeding normal threshold"
    ],
    "traces": [
      "checkout -> payment span dominates p95",
      "retry fan-out visible across checkout service"
    ]
  },

  "initial_state": {
    "payment_service_unreachable": true,
    "loadgenerator_flood_homepage": true,
    "retry_rate_limit_enabled": false,
    "traffic_shift_enabled": false,
    "payment_feature_disabled": false
  },

  "candidate_actions": [
    {"id": "restart_payment", "type": "restart", "target": "payment", "description": "Restart payment pods"},
    {"id": "disable_flag", "type": "flag", "target": "payment_feature", "description": "Disable the payment feature flag"},
    {"id": "rate_limit_retries", "type": "throttle", "target": "checkout", "description": "Rate-limit checkout retries"},
    {"id": "shift_traffic", "type": "traffic_shift", "target": "secondary_region", "description": "Shift traffic to the secondary region"}
  ],

  "simulated_outcomes": {
    "restart_payment": {
      "latency_p95_ms": 1900,
      "error_rate": 0.14,
      "retry_rate": 0.30,
      "time_to_effect_seconds": 120,
      "risk_level": "medium",
      "blast_radius": "medium",
      "recovery": "partial",
      "notes": "Restarting refreshes pods, but the retry loop keeps pressure on payment."
    },
    "disable_flag": {
      "latency_p95_ms": 500,
      "error_rate": 0.92,
      "retry_rate": 0.02,
      "time_to_effect_seconds": 15,
      "risk_level": "very_high",
      "blast_radius": "high",
      "recovery": "mixed",
      "notes": "Feature disable sheds the degraded dependency path quickly, but disables payment capability entirely."
    },
    "rate_limit_retries": {
      "latency_p95_ms": 1250,
      "error_rate": 0.20,
      "retry_rate": 0.10,
      "time_to_effect_seconds": 30,
      "risk_level": "low",
      "blast_radius": "low",
      "recovery": "strong",
      "notes": "Retry shaping stabilizes the system while keeping the product mostly available."
    },
    "shift_traffic": {
      "latency_p95_ms": 1450,
      "error_rate": 0.11,
      "retry_rate": 0.13,
      "time_to_effect_seconds": 180,
      "risk_level": "high",
      "blast_radius": "high",
      "recovery": "partial",
      "notes": "Traffic shifting spreads load but does not fix the core degraded dependency."
    }
  },

  "ground_truth": {
    "action_id": "rate_limit_retries",
    "why": "Rate limiting directly addresses the retry amplification root cause with minimal blast radius."
  },

  "naive_reflex": {
    "action_id": "restart_payment",
    "why": "A tired engineer restarts what looks broken, but restarting does not stop the retry amplification loop."
  },

  "evaluation_labels": {
    "dangerous_reflex": "restart_payment",
    "safe_action": "rate_limit_retries",
    "reflex_rejected_reason": "Restarting payment pods does not stop the retry amplification loop. The retries continue hammering the recovering service, causing re-degradation within minutes. The system remains in a degraded oscillation.",
    "blast_radius_avoided": "medium → low",
    "fairness_impact": "Rate limiting preserves payment availability for legitimate requests while shedding excess retry load from the amplification loop."
  }
}
```

### 3.4 Scenario 2: `payment_gateway_flap.json`

**Incident narrative:** Payment gateway is flapping (cycling between up and down). Restart makes it worse because the restart itself triggers another flap cycle. Disabling the payment feature flag isolates the system cleanly, letting the gateway stabilize without traffic.

```json
{
  "id": "payment_gateway_flap",
  "name": "Payment Gateway Flap",
  "description": "Payment gateway oscillates between healthy and degraded states, causing cascading timeouts and checkout failures.",

  "alert_template": {
    "commonLabels": {
      "alertname": "PaymentGatewayFlap",
      "severity": "critical",
      "service": "payment",
      "dependency": "payment_gateway"
    },
    "commonAnnotations": {
      "summary": "Payment gateway health check oscillating — service flapping between UP and DOWN states."
    }
  },

  "evidence": {
    "services": ["frontend", "checkout", "payment"],
    "metrics": {
      "latency_p95_ms": 3100,
      "error_rate": 0.35,
      "retry_rate": 0.22
    },
    "logs": [
      "payment gateway health check failed — connection reset",
      "payment gateway recovered — 3 seconds later health check failed again",
      "checkout requests timing out during payment flap window"
    ],
    "traces": [
      "payment span alternates between 50ms (healthy) and timeout (30s)",
      "checkout -> payment shows bimodal latency distribution"
    ]
  },

  "initial_state": {
    "payment_service_unreachable": true,
    "loadgenerator_flood_homepage": false,
    "retry_rate_limit_enabled": false,
    "traffic_shift_enabled": false,
    "payment_feature_disabled": false
  },

  "candidate_actions": [
    {"id": "restart_payment", "type": "restart", "target": "payment", "description": "Restart payment pods"},
    {"id": "disable_flag", "type": "flag", "target": "payment_feature", "description": "Disable the payment feature flag"},
    {"id": "rate_limit_retries", "type": "throttle", "target": "checkout", "description": "Rate-limit checkout retries"},
    {"id": "shift_traffic", "type": "traffic_shift", "target": "secondary_region", "description": "Shift traffic to the secondary region"}
  ],

  "simulated_outcomes": {
    "restart_payment": {
      "latency_p95_ms": 2800,
      "error_rate": 0.32,
      "retry_rate": 0.24,
      "time_to_effect_seconds": 90,
      "risk_level": "high",
      "blast_radius": "high",
      "recovery": "mixed",
      "notes": "Restart triggers another flap cycle. The gateway briefly recovers then degrades again. Net effect: no improvement, added instability."
    },
    "disable_flag": {
      "latency_p95_ms": 450,
      "error_rate": 0.04,
      "retry_rate": 0.03,
      "time_to_effect_seconds": 10,
      "risk_level": "medium",
      "blast_radius": "medium",
      "recovery": "strong",
      "notes": "Disabling the payment feature flag isolates the flapping gateway. Checkout proceeds without payment, which is a controlled degradation. Gateway can stabilize without traffic pressure."
    },
    "rate_limit_retries": {
      "latency_p95_ms": 2600,
      "error_rate": 0.28,
      "retry_rate": 0.08,
      "time_to_effect_seconds": 30,
      "risk_level": "medium",
      "blast_radius": "low",
      "recovery": "partial",
      "notes": "Rate limiting reduces retry pressure but the gateway continues flapping. Latency remains high because the root cause (flapping) is not addressed."
    },
    "shift_traffic": {
      "latency_p95_ms": 1800,
      "error_rate": 0.15,
      "retry_rate": 0.12,
      "time_to_effect_seconds": 120,
      "risk_level": "high",
      "blast_radius": "high",
      "recovery": "partial",
      "notes": "Shifting traffic to secondary region reduces exposure but introduces cross-region latency and does not fix the flapping gateway."
    }
  },

  "ground_truth": {
    "action_id": "disable_flag",
    "why": "Disabling the payment feature flag isolates the flapping gateway, allowing it to stabilize without traffic pressure while maintaining checkout availability in a controlled degradation mode."
  },

  "naive_reflex": {
    "action_id": "restart_payment",
    "why": "A tired engineer sees 'payment is down' and restarts it, but a restart triggers another flap cycle because the underlying gateway instability is not resolved."
  },

  "evaluation_labels": {
    "dangerous_reflex": "restart_payment",
    "safe_action": "disable_flag",
    "reflex_rejected_reason": "Restarting the payment service during a gateway flap triggers another oscillation cycle. The restart itself becomes a perturbation that extends the flapping window. Each restart attempt resets the gateway's backoff timer, preventing natural stabilization.",
    "blast_radius_avoided": "high → medium",
    "fairness_impact": "Feature flag isolation preserves checkout flow for non-payment operations. Customers can still browse and add to cart. Payment is deferred gracefully rather than failing unpredictably mid-transaction."
  }
}
```

### 3.5 Scenario 3: `seat_hold_clog.json`

**Incident narrative:** Concert ticket seat holds are stuck in one region due to a regional cache hot spot. Disabling the feature flag would kill all seat functionality globally. The correct action is shifting traffic to distribute the load across regions.

```json
{
  "id": "seat_hold_clog",
  "name": "Seat Hold Clog",
  "description": "Regional cache hot spot causes seat hold timeouts in the primary region, blocking ticket purchases for a major concert event.",

  "alert_template": {
    "commonLabels": {
      "alertname": "SeatHoldTimeout",
      "severity": "critical",
      "service": "checkout",
      "dependency": "seat_hold_cache"
    },
    "commonAnnotations": {
      "summary": "Seat hold acquisition timeout rate exceeds threshold — primary region cache saturated during concert ticket drop."
    }
  },

  "evidence": {
    "services": ["frontend", "checkout", "payment"],
    "metrics": {
      "latency_p95_ms": 4500,
      "error_rate": 0.25,
      "retry_rate": 0.18
    },
    "logs": [
      "seat hold cache timeout in us-east-1 — 98% utilization",
      "seat hold acquisition failing for concert event ID 7823",
      "customers receiving 'seats unavailable' despite inventory remaining"
    ],
    "traces": [
      "checkout -> seat_hold_cache spans show 4s+ latency in us-east-1",
      "us-west-2 seat_hold_cache responding normally at 120ms"
    ]
  },

  "initial_state": {
    "payment_service_unreachable": false,
    "loadgenerator_flood_homepage": true,
    "retry_rate_limit_enabled": false,
    "traffic_shift_enabled": false,
    "payment_feature_disabled": false
  },

  "candidate_actions": [
    {"id": "restart_payment", "type": "restart", "target": "payment", "description": "Restart payment pods"},
    {"id": "disable_flag", "type": "flag", "target": "payment_feature", "description": "Disable the payment feature flag"},
    {"id": "rate_limit_retries", "type": "throttle", "target": "checkout", "description": "Rate-limit checkout retries"},
    {"id": "shift_traffic", "type": "traffic_shift", "target": "secondary_region", "description": "Shift traffic to the secondary region"}
  ],

  "simulated_outcomes": {
    "restart_payment": {
      "latency_p95_ms": 4200,
      "error_rate": 0.23,
      "retry_rate": 0.17,
      "time_to_effect_seconds": 120,
      "risk_level": "medium",
      "blast_radius": "medium",
      "recovery": "partial",
      "notes": "Restarting payment pods has minimal effect because the bottleneck is the regional seat hold cache, not the payment service itself."
    },
    "disable_flag": {
      "latency_p95_ms": 400,
      "error_rate": 0.90,
      "retry_rate": 0.01,
      "time_to_effect_seconds": 10,
      "risk_level": "very_high",
      "blast_radius": "high",
      "recovery": "mixed",
      "notes": "Disabling the payment feature flag kills all seat hold and ticket purchase functionality. Customers cannot buy tickets at all. This is catastrophic during a concert drop."
    },
    "rate_limit_retries": {
      "latency_p95_ms": 3800,
      "error_rate": 0.22,
      "retry_rate": 0.06,
      "time_to_effect_seconds": 30,
      "risk_level": "low",
      "blast_radius": "low",
      "recovery": "partial",
      "notes": "Rate limiting reduces retry noise but does not address the regional cache saturation. Seat holds still time out."
    },
    "shift_traffic": {
      "latency_p95_ms": 1200,
      "error_rate": 0.08,
      "retry_rate": 0.05,
      "time_to_effect_seconds": 60,
      "risk_level": "medium",
      "blast_radius": "medium",
      "recovery": "strong",
      "notes": "Shifting traffic to the secondary region distributes seat hold load across both regional caches. us-west-2 has capacity. Latency normalizes."
    }
  },

  "ground_truth": {
    "action_id": "shift_traffic",
    "why": "Traffic shifting distributes the seat hold cache load across regions, relieving the saturated us-east-1 cache while preserving full ticket purchase functionality."
  },

  "naive_reflex": {
    "action_id": "disable_flag",
    "why": "A panicking engineer disables the feature flag to stop the errors, but this kills all ticket purchasing during the most critical revenue moment."
  },

  "evaluation_labels": {
    "dangerous_reflex": "disable_flag",
    "safe_action": "shift_traffic",
    "reflex_rejected_reason": "Disabling the payment feature flag during a concert ticket drop destroys the revenue event entirely. While it eliminates errors, it eliminates all ticket purchases too. The business impact is catastrophic — customers are locked out during a time-sensitive sale.",
    "blast_radius_avoided": "high → medium",
    "fairness_impact": "Traffic shifting preserves ticket purchase access for all customers by distributing load. It prevents the scenario where only customers routed to the healthy region can buy tickets while others are completely blocked."
  }
}
```

---

## 4. Module 2: Evaluation Agent

### 4.1 `agents/evaluator.py` — Complete Specification

```python
"""
Evaluation agent — produces structured verdicts from plan and outcome data.

Pure Python, deterministic, no LLM calls. Verdicts are generated from
rule-based classification and template-based narrative generation.
"""
from __future__ import annotations

from datetime import datetime, timezone

from agents.simulator import compare_prediction_to_actual


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate(plan: dict, actual_outcome: dict, scenario: dict) -> dict:
    """
    Produce a structured verdict from a completed pipeline run.

    Args:
        plan:           The plan report dict from build_plan_report().
                        Must contain: best_action, baseline_best_action,
                        overall_confidence, baseline_confidence,
                        predicted_effects_by_action, observed_condition,
                        candidate_actions, stratus_ranking, baseline_ranking.

        actual_outcome: The actual outcome dict from actual_outcome_from_evidence()
                        or simulate_action(). Must contain: latency_p95_ms,
                        error_rate, retry_rate, recovery, blast_radius, risk_level.

        scenario:       The scenario profile dict from load_scenario().
                        Must contain: id, ground_truth, naive_reflex,
                        evaluation_labels, evidence.

    Returns:
        A verdict dict with the following top-level keys:
        - scenario_id: str
        - timestamp: str (ISO 8601)
        - mode: str ("stratus" or "baseline")
        - decision_comparison: dict
        - outcome_evaluation: dict
        - verdict: dict
        - case_summary: dict
    """
```

**Return value — exact structure:**

```python
{
    "scenario_id": str,             # e.g. "retry_death_spiral"
    "timestamp": str,               # ISO 8601, e.g. "2026-04-04T14:30:00+00:00"
    "mode": str,                    # "stratus" or "baseline"

    "decision_comparison": {
        "stratus_chose": str,       # action_id chosen by Stratus
        "baseline_chose": str,      # action_id chosen by baseline
        "ground_truth": str,        # action_id from scenario.ground_truth
        "stratus_correct": bool,    # stratus_chose == ground_truth
        "baseline_correct": bool,   # baseline_chose == ground_truth
        "stratus_confidence": float,# from plan.overall_confidence
        "baseline_confidence": float,# from plan.baseline_confidence
    },

    "outcome_evaluation": {
        "predicted_vs_actual": dict, # output of compare_prediction_to_actual()
        "drift_score": float,        # 0.0 to 1.0 — higher is better
        "recovery_class": str,       # "strong_recovery"|"partial_failure"|"severe_failure"
        "metrics_before": {          # from plan.observed_condition.metrics
            "latency_p95_ms": int,
            "error_rate": float,
            "retry_rate": float,
        },
        "metrics_after": {           # from actual_outcome
            "latency_p95_ms": int,
            "error_rate": float,
            "retry_rate": float,
        },
    },

    "verdict": {
        "classification": str,       # same as recovery_class
        "dangerous_reflex_rejected": bool,  # True if chosen != naive_reflex AND mode == stratus
        "reflex_action": str,        # action_id of the naive reflex
        "reflex_rejected_reason": str,# from scenario.evaluation_labels
        "safe_action_chosen": bool,  # True if chosen == ground_truth
        "blast_radius_avoided": str, # from scenario.evaluation_labels, e.g. "medium → low"
        "fairness_preserved": bool,  # True if safe_action_chosen
        "narrative": str,            # generated narrative paragraph
    },

    "case_summary": {
        "situation": str,            # from plan.incident_summary
        "shortlisted_actions": list[str],  # [action_id, ...]
        "chosen_action": str,        # the action that was actually executed
        "predicted_result": dict,    # predicted effects for chosen action
        "actual_result": dict,       # actual outcome metrics
        "narrative_verdict": str,    # one-sentence summary
    },
}
```

### 4.2 Internal Functions — Complete Logic

```python
# ---------------------------------------------------------------------------
# Recovery classification
# ---------------------------------------------------------------------------

def _classify_recovery(drift_score: float, actual_outcome: dict) -> str:
    """
    Classify the recovery outcome into one of three classes.

    Classification rules (evaluated in order — first match wins):

    1. severe_failure:
       - drift_score < 0.5
       - OR recovery == "mixed" AND blast_radius in ("high", "very_high")

    2. partial_failure:
       - drift_score < 0.75
       - OR recovery == "partial"

    3. strong_recovery:
       - all other cases (drift_score >= 0.75 AND recovery not partial/mixed-with-high-blast)

    Args:
        drift_score:    Float 0.0-1.0 from compare_prediction_to_actual()
        actual_outcome: Dict with 'recovery' and 'blast_radius' keys

    Returns:
        One of: "strong_recovery", "partial_failure", "severe_failure"
    """
    recovery = actual_outcome.get("recovery", "partial")
    blast = actual_outcome.get("blast_radius", "medium")

    # Severe: prediction was very wrong, or outcome is actively harmful
    if drift_score < 0.5:
        return "severe_failure"
    if recovery == "mixed" and blast in ("high", "very_high"):
        return "severe_failure"

    # Partial: some improvement but not fully resolved
    if drift_score < 0.75:
        return "partial_failure"
    if recovery == "partial":
        return "partial_failure"

    # Strong: prediction matched reality, recovery is solid
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
    """
    Fill the narrative template with concrete values from the run.

    All values are extracted from plan, actual_outcome, and scenario dicts.
    No external calls. Deterministic output for the same inputs.
    """
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
    """Look up human-readable description for an action_id from the plan's candidate list."""
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
```

### 4.3 Top-level `evaluate()` — Complete Implementation Logic

```python
def evaluate(plan: dict, actual_outcome: dict, scenario: dict) -> dict:
    chosen = plan["best_action"]
    chosen_id = chosen["id"]
    gt_id = scenario["ground_truth"]["action_id"]
    naive_id = scenario["naive_reflex"]["action_id"]
    labels = scenario["evaluation_labels"]

    # Determine mode from plan
    # If plan was built with baseline as primary, baseline_best_action == best_action
    baseline_chose = plan.get("baseline_best_action", {}).get("id", "unknown")
    stratus_chose = plan.get("stratus_ranking", [{}])[0].get("id", "unknown")

    # If the chosen action matches baseline's pick, mode is baseline
    mode = "baseline" if chosen_id == baseline_chose and chosen_id != stratus_chose else "stratus"

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
```

---

## 5. Module 3: Case Library

### 5.1 `tools/case_library.py` — Complete Specification

```python
"""
Case library — file-based persistence for completed incident response cases.

Each case is a JSON file in the cases/ directory. Cases are append-only
and identified by {scenario_id}_{timestamp} slugs.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

CASES_DIR = Path("cases")


def save_case(verdict: dict, plan: dict, scenario: dict) -> str | None:
    """
    Persist a complete case record from a pipeline run.

    Assembles the case from the verdict, plan, and scenario data.
    Creates the cases/ directory if it doesn't exist.

    Args:
        verdict:  The verdict dict from evaluator.evaluate()
        plan:     The plan dict from build_plan_report()
        scenario: The scenario dict from load_scenario()

    Returns:
        The case_id string (e.g. "retry_death_spiral_20260404_143000"),
        or None if the write fails.

    Side effects:
        Writes cases/{case_id}.json
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
        import sys
        print(f"WARNING: Failed to save case {case_id}: {exc}", file=sys.stderr)
        return None


def load_case(case_id: str) -> dict:
    """
    Load a single case by its case_id.

    Raises:
        FileNotFoundError: If the case file does not exist.
    """
    path = _case_path(case_id)
    if not path.exists():
        raise FileNotFoundError(f"Case not found: {case_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_cases(scenario_id: str | None = None) -> list[dict]:
    """
    List cases with lightweight metadata.

    Args:
        scenario_id: If provided, filter to cases matching this scenario.
                     If None, return all cases.

    Returns:
        List of dicts with keys: case_id, scenario_id, timestamp, mode,
        chosen_action, recovery_class. Sorted by timestamp descending.
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
        except (json.JSONDecodeError, KeyError):
            continue  # skip malformed case files

    cases.sort(key=lambda c: c["timestamp"], reverse=True)
    return cases


def find_similar(scenario_id: str, action_id: str) -> list[dict]:
    """
    Find prior cases with the same scenario and chosen action.

    Useful for showing "this scenario has been seen before with this action"
    in the Verdict View.

    Returns:
        List of case metadata dicts (same shape as list_cases output),
        filtered to matching scenario_id AND chosen_action.
    """
    all_cases = list_cases(scenario_id)
    return [c for c in all_cases if c["chosen_action"] == action_id]


def _case_path(case_id: str) -> Path:
    """Return the file path for a case."""
    return CASES_DIR / f"{case_id}.json"


def _generate_case_id(scenario_id: str, timestamp: datetime) -> str:
    """Generate a unique, sortable case ID."""
    ts = timestamp.strftime("%Y%m%d_%H%M%S")
    return f"{scenario_id}_{ts}"
```

---

## 6. Module 4: Baseline Toggle & Pipeline Changes

### 6.1 `run.py` — Complete Diff Specification

This section specifies every change to `run.py` as exact modifications.

#### 6.1.1 New Imports (add after existing imports)

```python
from scenarios import load_scenario, list_scenarios
from agents.evaluator import evaluate as evaluate_verdict
from tools.case_library import save_case
```

#### 6.1.2 Argument Parser Changes

Current:
```python
parser.add_argument("input_path", nargs="?", default="alerts/latest.json")
parser.add_argument("--phase", choices=["plan", "verify", "auto"], default="auto")
```

New:
```python
parser.add_argument("input_path", nargs="?", default="alerts/latest.json")
parser.add_argument("--phase", choices=["plan", "verify", "auto"], default="auto")
parser.add_argument("--mode", choices=["stratus", "baseline"], default="stratus")
parser.add_argument("--scenario", default=None,
                    help=f"Scenario ID. Available: use --list-scenarios to see options.")
parser.add_argument("--list-scenarios", action="store_true",
                    help="List available scenarios and exit.")
```

#### 6.1.3 `main()` Changes

```python
def main() -> None:
    load_local_env()
    parser = argparse.ArgumentParser()
    # ... (args as above)
    args = parser.parse_args()

    # --- NEW: list scenarios ---
    if args.list_scenarios:
        for sid in list_scenarios():
            print(sid)
        return

    # --- NEW: load scenario if specified ---
    scenario = None
    if args.scenario:
        try:
            scenario = load_scenario(args.scenario)
        except (FileNotFoundError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

    input_path = Path(args.input_path)
    payload = json.loads(input_path.read_text())
    source_type, state, evidence = load_incident_input(payload, scenario)
    scenario_id = args.scenario or payload.get("id", "alert_latest")

    # --- Inject scenario into state for downstream modules ---
    if scenario:
        # Reset runtime state to scenario's initial state
        from tools.runtime_state import save_state
        save_state(scenario["initial_state"])

    if args.phase == "plan":
        plan = build_plan_report(payload, scenario_id, source_type, input_path,
                                 state, evidence, args.mode, scenario)
        write_outputs(plan, scenario_id, "plan")
        write_browser_playbook(plan, scenario_id)
        print(json.dumps(plan, indent=2))
        return

    if args.phase == "verify":
        plan = load_saved_plan(scenario_id)
        verify = build_verify_report(
            payload=payload, scenario_id=scenario_id, source_type=source_type,
            input_path=input_path, current_state=state, current_evidence=evidence,
            plan=plan, execution=None, mode_label="browser_verify", scenario=scenario,
        )
        write_outputs(verify, scenario_id, "report")
        print(json.dumps(verify, indent=2))
        return

    # auto mode
    plan = build_plan_report(payload, scenario_id, source_type, input_path,
                             state, evidence, args.mode, scenario)
    chosen = plan["best_action"]
    execution = execute_action(chosen)

    refreshed_evidence = collect_evidence(payload)
    refreshed_state = classify_incident(payload, refreshed_evidence)

    report = build_verify_report(
        payload=payload, scenario_id=scenario_id, source_type=source_type,
        input_path=input_path, current_state=refreshed_state,
        current_evidence=refreshed_evidence, plan=plan, execution=execution,
        mode_label="auto_apply", scenario=scenario,
    )

    write_outputs(plan, scenario_id, "plan")
    write_browser_playbook(plan, scenario_id)
    write_outputs(report, scenario_id, "report")
    print(json.dumps(report, indent=2))
```

#### 6.1.4 `load_incident_input()` Changes

```python
def load_incident_input(payload: dict, scenario: dict | None = None) -> tuple[str, dict, dict]:
    """
    Load incident input, optionally overlaying scenario evidence.

    If scenario is provided, use its evidence instead of collecting from Prometheus.
    The alert payload is still used for classification labels.
    """
    if scenario:
        evidence = scenario["evidence"]
    else:
        evidence = collect_evidence(payload)
    state = classify_incident(payload, evidence)
    return "alertmanager_webhook", state, evidence
```

#### 6.1.5 `build_plan_report()` Changes

New signature:
```python
def build_plan_report(
    payload: dict,
    scenario_id: str,
    source_type: str,
    input_path: Path,
    state: dict,
    evidence: dict,
    mode: str = "stratus",          # NEW
    scenario: dict | None = None,   # NEW
) -> dict:
```

Key logic change — mode switch:
```python
    # Generate actions from scenario if available, otherwise from state
    if scenario:
        actions = scenario["candidate_actions"]
    else:
        actions = generate_candidate_actions(state)

    # Always run BOTH rankers
    ranking = rank_actions(state, actions)
    baseline_ranking = baseline_choose(state, actions)

    # Mode switch: which ranker is primary?
    if mode == "baseline":
        primary_ranking = baseline_ranking
        primary_chosen = normalize_best_action(baseline_ranking["best_action"], actions)
        primary_confidence = baseline_ranking["overall_confidence"]
    else:
        primary_ranking = ranking
        primary_chosen = normalize_best_action(ranking["best_action"], actions)
        primary_confidence = ranking["overall_confidence"]

    # The plan always includes both, but "best_action" reflects the mode
    return {
        # ... existing fields ...
        "best_action": primary_chosen,
        "overall_confidence": primary_confidence,
        "mode": mode,
        # ... stratus and baseline fields always populated ...
    }
```

#### 6.1.6 `build_verify_report()` Changes

New signature adds `scenario` parameter. After building the existing report, append evaluation and case writeback:

```python
def build_verify_report(
    payload: dict,
    scenario_id: str,
    source_type: str,
    input_path: Path,
    current_state: dict,
    current_evidence: dict,
    plan: dict,
    execution: dict | None,
    mode_label: str,
    scenario: dict | None = None,   # NEW
) -> dict:
    # ... existing report building logic (unchanged) ...

    report = { ... }  # existing report dict

    # --- NEW: Evaluation + Case Library ---
    if scenario:
        verdict = evaluate_verdict(plan, actual, scenario)
        write_outputs(verdict, scenario_id, "verdict")

        case_id = save_case(verdict, plan, scenario)
        verdict["case_id"] = case_id

        report["verdict"] = verdict
        report["case_id"] = case_id

    return report
```

---

## 7. Module 5: Verdict View

### 7.1 `tools/visual_control_plane.py` — New Endpoints

Add these two endpoints after the existing `/api/reset` endpoint:

```python
@app.get("/verdict", response_class=HTMLResponse)
def verdict_latest() -> str:
    """Render the latest verdict. Tries the most recently modified verdict file in outputs/."""
    outputs = Path("outputs")
    verdict_files = sorted(outputs.glob("*_verdict.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not verdict_files:
        return _verdict_empty_page()
    verdict = json.loads(verdict_files[0].read_text(encoding="utf-8"))
    return _render_verdict_html(verdict)


@app.get("/verdict/{scenario_id}", response_class=HTMLResponse)
def verdict_by_scenario(scenario_id: str) -> str:
    """Render the verdict for a specific scenario."""
    path = Path("outputs") / f"{scenario_id}_verdict.json"
    if not path.exists():
        return _verdict_empty_page(scenario_id)
    verdict = json.loads(path.read_text(encoding="utf-8"))
    return _render_verdict_html(verdict)
```

### 7.2 HTML Rendering Function — Complete Implementation

```python
def _render_verdict_html(v: dict) -> str:
    """Render a verdict dict as a polished HTML page."""
    dc = v.get("decision_comparison", {})
    oe = v.get("outcome_evaluation", {})
    vd = v.get("verdict", {})
    cs = v.get("case_summary", {})
    mb = oe.get("metrics_before", {})
    ma = oe.get("metrics_after", {})

    # Color helpers
    def drift_color(score: float) -> str:
        if score >= 0.75: return "#2d7a3a"    # green
        if score >= 0.5:  return "#b8860b"    # amber
        return "#b0432c"                       # red

    def bool_badge(val: bool, yes: str = "Yes", no: str = "No") -> str:
        color = "#2d7a3a" if val else "#b0432c"
        label = yes if val else no
        return f'<span style="background:{color};color:white;padding:4px 10px;border-radius:8px;">{escape(label)}</span>'

    drift = oe.get("drift_score", 0.0)
    d_color = drift_color(drift)

    # Metric rows
    def metric_row(label: str, before: object, after: object) -> str:
        return f"<tr><td>{escape(label)}</td><td>{escape(str(before))}</td><td>{escape(str(after))}</td></tr>"

    metrics_table = (
        metric_row("Latency P95 (ms)", mb.get("latency_p95_ms", "—"), ma.get("latency_p95_ms", "—"))
        + metric_row("Error Rate", mb.get("error_rate", "—"), ma.get("error_rate", "—"))
        + metric_row("Retry Rate", mb.get("retry_rate", "—"), ma.get("retry_rate", "—"))
    )

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Verdict — {escape(v.get("scenario_id", ""))}</title>
  <style>
    :root {{ --bg:#f6f1e8; --ink:#18222f; --accent:#c65d2e; --card:#fffaf1; --line:#d9c8ac; --green:#2d7a3a; --red:#b0432c; }}
    body {{ font-family: Georgia, serif; margin:0; background:radial-gradient(circle at top, #fff6e7, var(--bg)); color:var(--ink); }}
    .wrap {{ max-width:1020px; margin:0 auto; padding:32px; }}
    h1 {{ margin-bottom:4px; }}
    .subtitle {{ color:#666; margin-bottom:24px; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:18px; padding:20px; box-shadow:0 12px 32px rgba(24,34,47,0.06); }}
    .card h2 {{ font-size:1.1rem; margin:0 0 12px 0; color:var(--accent); }}
    .full {{ grid-column: 1 / -1; }}
    table {{ width:100%; border-collapse:collapse; margin:8px 0; }}
    th, td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); }}
    th {{ font-weight:600; }}
    .drift {{ font-size:1.4rem; font-weight:700; }}
    .narrative {{ line-height:1.6; font-style:italic; background:#f0e8d8; padding:14px; border-radius:12px; margin-top:10px; }}
    .badge {{ display:inline-block; padding:4px 10px; border-radius:8px; font-size:0.85rem; }}
    .vs {{ display:flex; gap:16px; align-items:center; margin:8px 0; }}
    .vs-item {{ flex:1; padding:12px; border-radius:12px; text-align:center; }}
    .vs-wrong {{ background:#fde8e4; border:2px solid var(--red); }}
    .vs-right {{ background:#e4f5e8; border:2px solid var(--green); }}
    .arrow {{ font-size:1.5rem; color:#999; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Incident Verdict</h1>
    <p class="subtitle">Scenario: <strong>{escape(v.get("scenario_id", ""))}</strong> &middot;
       Mode: <strong>{escape(v.get("mode", ""))}</strong> &middot;
       {escape(v.get("timestamp", "")[:19])}</p>

    <div class="grid">

      <!-- Card 1: Predicted vs Actual -->
      <div class="card">
        <h2>Predicted vs Actual</h2>
        <table>
          <tr><th>Metric</th><th>Before</th><th>After</th></tr>
          {metrics_table}
        </table>
        <div class="drift" style="color:{d_color}; margin-top:8px;">
          Drift Score: {drift:.2f}
        </div>
        <div>Recovery: <strong>{escape(oe.get("recovery_class", "—"))}</strong></div>
      </div>

      <!-- Card 2: Dangerous Reflex Rejected -->
      <div class="card">
        <h2>Dangerous Reflex Rejected</h2>
        <div>{bool_badge(vd.get("dangerous_reflex_rejected", False), "Rejected", "Not rejected")}</div>
        <div class="vs">
          <div class="vs-item vs-wrong">
            <div style="font-size:0.8rem;color:var(--red);">Baseline chose</div>
            <div><strong>{escape(dc.get("baseline_chose", "—"))}</strong></div>
            <div style="font-size:0.8rem;">Correct: {bool_badge(dc.get("baseline_correct", False))}</div>
          </div>
          <div class="arrow">&rarr;</div>
          <div class="vs-item vs-right">
            <div style="font-size:0.8rem;color:var(--green);">Guardrail chose</div>
            <div><strong>{escape(dc.get("stratus_chose", "—"))}</strong></div>
            <div style="font-size:0.8rem;">Correct: {bool_badge(dc.get("stratus_correct", False))}</div>
          </div>
        </div>
        <p style="font-size:0.9rem;">{escape(vd.get("reflex_rejected_reason", ""))}</p>
      </div>

      <!-- Card 3: Blast Radius -->
      <div class="card">
        <h2>Blast Radius Avoided</h2>
        <div style="font-size:1.6rem; text-align:center; margin:16px 0;">
          {escape(vd.get("blast_radius_avoided", "—"))}
        </div>
        <div>Fairness preserved: {bool_badge(vd.get("fairness_preserved", False))}</div>
        <div>Safe action chosen: {bool_badge(vd.get("safe_action_chosen", False))}</div>
      </div>

      <!-- Card 4: Case Saved -->
      <div class="card">
        <h2>Case Saved</h2>
        <div>Case ID: <code>{escape(str(v.get("case_id", "—")))}</code></div>
        <div style="margin-top:8px;">Classification: <strong>{escape(vd.get("classification", "—"))}</strong></div>
        <div style="margin-top:8px;">Chosen action: <strong>{escape(cs.get("chosen_action", "—"))}</strong></div>
      </div>

      <!-- Card 5: Narrative (full width) -->
      <div class="card full">
        <h2>Narrative Verdict</h2>
        <div class="narrative">
          {escape(vd.get("narrative", "No narrative generated."))}
        </div>
      </div>

    </div>
  </div>
</body>
</html>"""


def _verdict_empty_page(scenario_id: str | None = None) -> str:
    """Render a page when no verdict is available."""
    msg = f"No verdict available for scenario '{escape(scenario_id)}'." if scenario_id else "No verdict available."
    return f"""<!doctype html>
<html>
<head><meta charset="utf-8"><title>No Verdict</title>
<style>body {{ font-family: Georgia, serif; text-align:center; padding:80px; background:#f6f1e8; color:#18222f; }}</style>
</head>
<body>
  <h1>No Verdict Available</h1>
  <p>{msg}</p>
  <p>Run the pipeline first:</p>
  <code>.venv/bin/python run.py alerts/latest.json --phase auto</code>
</body>
</html>"""
```

Add required import at the top of `visual_control_plane.py`:

```python
import json
from pathlib import Path
```

---

## 8. Existing Module Changes

### 8.1 `agents/simulator.py` — Changes

The simulator must accept an optional `scenario` parameter to load outcomes from the scenario profile instead of the hardcoded `SIMULATED_OUTCOMES` dict.

**Change to `simulate_action()`:**

```python
def simulate_action(state: dict, action: dict, scenario: dict | None = None) -> dict:
    """
    Simulate the outcome of an action.

    If scenario is provided, use its simulated_outcomes.
    Otherwise, fall back to the hardcoded SIMULATED_OUTCOMES dict (backward compatible).
    """
    outcomes = scenario["simulated_outcomes"] if scenario else SIMULATED_OUTCOMES

    if action["id"] not in outcomes:
        raise KeyError(
            f"No simulated outcome for action '{action['id']}'. "
            f"Available: {list(outcomes.keys())}"
        )

    baseline = state.get("metrics", {})
    outcome = dict(outcomes[action["id"]])
    outcome["action_id"] = action["id"]
    outcome["latency_direction"] = _direction(
        baseline.get("latency_p95_ms"), outcome["latency_p95_ms"]
    )
    outcome["error_direction"] = _direction(baseline.get("error_rate"), outcome["error_rate"])
    outcome["retry_storm_risk"] = _retry_risk(outcome["retry_rate"])
    return outcome
```

The hardcoded `SIMULATED_OUTCOMES` dict **stays** — it provides backward compatibility when no scenario is specified. The only change is the new optional parameter.

### 8.2 `config.py` — Additions

Add at the bottom:

```python
# --- Scenario / Case Library ---
DEFAULT_SCENARIO_ID = "retry_death_spiral"
CASES_DIR = "cases"
VERDICT_OUTPUT_SUFFIX = "verdict"
```

---

## 9. Data Flow Diagrams

### 9.1 Plan Phase Data Flow

```
Input:
  --scenario retry_death_spiral --mode stratus --phase plan alerts/latest.json

Flow:
  1. load_scenario("retry_death_spiral")
     → scenario dict (cached)

  2. save_state(scenario["initial_state"])
     → state/runtime_state.json reset

  3. load_incident_input(payload, scenario)
     a. evidence = scenario["evidence"]          # skip Prometheus
     b. state = classify_incident(payload, evidence)
     → (source_type, state, evidence)

  4. actions = scenario["candidate_actions"]      # from scenario, not hardcoded

  5. ranking = rank_actions(state, actions)        # Stratus (unchanged)
     baseline_ranking = baseline_choose(state, actions)  # baseline (unchanged)

  6. mode == "stratus" → primary = ranking
     best_action = ranking.best_action

Output:
  → outputs/retry_death_spiral_plan.json
  → outputs/retry_death_spiral_browser_playbook.json
```

### 9.2 Auto Phase Data Flow (Plan + Execute + Verify + Evaluate)

```
Steps 1-6: same as plan phase

  7. execute_action(chosen)
     → apply_action(chosen["id"]) to runtime_state.json

  8. refreshed_evidence = collect_evidence(payload)
     refreshed_state = classify_incident(payload, refreshed_evidence)

  9. actual = actual_outcome_from_evidence(before_metrics, after_metrics, action_id)
     → OR: actual = simulate_action(state, chosen, scenario)  # if using simulation

  10. verdict = evaluate_verdict(plan, actual, scenario)
      a. compare_prediction_to_actual() → drift_score
      b. _classify_recovery() → recovery_class
      c. _generate_narrative() → narrative string
      → verdict dict

  11. case_id = save_case(verdict, plan, scenario)
      → cases/retry_death_spiral_20260404_143000.json

Output:
  → outputs/retry_death_spiral_plan.json
  → outputs/retry_death_spiral_report.json
  → outputs/retry_death_spiral_verdict.json       # NEW
  → outputs/retry_death_spiral_browser_playbook.json
  → cases/retry_death_spiral_20260404_143000.json  # NEW
  → /verdict endpoint now serves the verdict HTML
```

### 9.3 Baseline vs Stratus Comparison Flow

```
Demo run 1: --mode stratus --scenario retry_death_spiral
  → Stratus picks rate_limit_retries (correct)
  → Verdict: strong_recovery, dangerous_reflex_rejected=true
  → Case saved

Demo run 2: --mode baseline --scenario retry_death_spiral
  → Baseline picks restart_payment (wrong)
  → Verdict: partial_failure, dangerous_reflex_rejected=false
  → Case saved

Verdict View at /verdict shows:
  - Stratus chose rate_limit_retries ✓
  - Baseline chose restart_payment ✗
  - Blast radius: medium → low
  - Narrative explains why restart was wrong
```

---

## 10. Complete Scenario Data

### 10.1 Expected Outcomes Matrix

| Scenario | Action | Latency After | Error After | Retry After | Recovery | Blast | Ground Truth? | Baseline Picks? |
|----------|--------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Retry Death Spiral** | restart_payment | 1900 | 0.14 | 0.30 | partial | medium | No | **Yes** |
| | disable_flag | 500 | 0.92 | 0.02 | mixed | high | No | No |
| | **rate_limit_retries** | **1250** | **0.20** | **0.10** | **strong** | **low** | **Yes** | No |
| | shift_traffic | 1450 | 0.11 | 0.13 | partial | high | No | No |
| **Payment Gateway Flap** | restart_payment | 2800 | 0.32 | 0.24 | mixed | high | No | **Yes** |
| | **disable_flag** | **450** | **0.04** | **0.03** | **strong** | **medium** | **Yes** | No |
| | rate_limit_retries | 2600 | 0.28 | 0.08 | partial | low | No | No |
| | shift_traffic | 1800 | 0.15 | 0.12 | partial | high | No | No |
| **Seat Hold Clog** | restart_payment | 4200 | 0.23 | 0.17 | partial | medium | No | No |
| | disable_flag | 400 | 0.90 | 0.01 | mixed | high | No | **Yes** |
| | rate_limit_retries | 3800 | 0.22 | 0.06 | partial | low | No | No |
| | **shift_traffic** | **1200** | **0.08** | **0.05** | **strong** | **medium** | **Yes** | No |

### 10.2 Baseline Keyword Match Analysis

The baseline keyword rules in `agents/baseline.py` are:
```python
{"trigger": "retry",   "prefer": "restart"}
{"trigger": "latency", "prefer": "restart"}
{"trigger": "timeout", "prefer": "restart"}
{"trigger": "crash",   "prefer": "restart"}
```

- **Retry Death Spiral:** "retry" in incident text → matches `restart_payment` (keyword score 1+). Correct: naive picks restart.
- **Payment Gateway Flap:** "timeout" in logs → matches `restart_payment`. Correct: naive picks restart.
- **Seat Hold Clog:** "timeout" in logs → would match `restart_payment`, but the LLM baseline (if configured) with the "panicking engineer" prompt would likely pick `disable_flag` (the "turn it off" reflex). For keyword fallback, it picks `restart_payment` — either way, it picks wrong.

---

## 11. Error Handling Contract

| Error Condition | Where | Behavior | User-Visible Effect |
|----------------|-------|----------|-------------------|
| `--scenario foo` where `foo.json` doesn't exist | `run.py` → `load_scenario()` | `FileNotFoundError` with available list | Prints error, exits 1 |
| Scenario JSON missing `ground_truth` key | `scenarios/__init__.py` → `_validate()` | `ValueError` listing missing keys | Prints error, exits 1 |
| `simulate_action()` called with action_id not in scenario outcomes | `agents/simulator.py` | `KeyError` with available action list | Prints error, exits 1 |
| Case library `cases/` dir not writable | `tools/case_library.py` → `save_case()` | Catches `OSError`, prints warning, returns `None` | Warning to stderr, pipeline continues |
| `/verdict` endpoint with no verdict file | `visual_control_plane.py` | Returns empty-state HTML page | "No verdict available" page |
| Malformed case file in `cases/` | `tools/case_library.py` → `list_cases()` | Catches `JSONDecodeError`/`KeyError`, skips file | Silently omitted from listing |
| `--mode baseline` but LLM not configured | `agents/baseline.py` (unchanged) | Falls back to keyword rules, prints warning | Warning to stderr, keyword fallback used |
| Stratus API call fails | `tools/stratus_guardrail.py` (unchanged) | Falls back to mock ranking | Note in ranking output |

**No new exceptions are introduced.** All new code either raises the standard `FileNotFoundError`/`ValueError`/`KeyError` (for programmer errors and bad data), or catches and gracefully degrades (for runtime I/O failures).

---

## 12. Integration Test Specifications

### 12.1 Scenario Loading Tests

```
Test: load all scenarios
  For each scenario in list_scenarios():
    load_scenario(scenario_id) succeeds
    All REQUIRED_KEYS present
    ground_truth.action_id exists in simulated_outcomes
    Every candidate_action has a simulated_outcome entry

Test: load nonexistent scenario
  load_scenario("nonexistent") raises FileNotFoundError
  Error message contains available scenario list

Test: scenario cache works
  load_scenario("retry_death_spiral") twice → same object (identity check)
```

### 12.2 Evaluator Tests

```
Test: strong recovery verdict
  Given: plan where Stratus chose rate_limit_retries, baseline chose restart_payment
         actual_outcome = scenario["simulated_outcomes"]["rate_limit_retries"]
         scenario = retry_death_spiral
  Expect: verdict.classification == "strong_recovery"
          verdict.dangerous_reflex_rejected == True
          verdict.safe_action_chosen == True
          verdict.narrative contains "rate-limit" and "retry"
          decision_comparison.stratus_correct == True
          decision_comparison.baseline_correct == False

Test: partial failure verdict
  Given: plan where mode=baseline, baseline chose restart_payment
         actual_outcome = scenario["simulated_outcomes"]["restart_payment"]
         scenario = retry_death_spiral
  Expect: verdict.classification == "partial_failure"
          verdict.dangerous_reflex_rejected == False

Test: severe failure verdict
  Given: plan where chosen action has mixed recovery + high blast radius
         actual_outcome = scenario["simulated_outcomes"]["disable_flag"]  (for retry scenario)
  Expect: verdict.classification == "severe_failure"
```

### 12.3 Case Library Tests

```
Test: save and load case
  save_case(verdict, plan, scenario) → case_id (not None)
  load_case(case_id) → case dict
  case.scenario_id == scenario["id"]
  case.verdict.classification == verdict["verdict"]["classification"]

Test: list cases by scenario
  Save 2 cases for "retry_death_spiral", 1 for "payment_gateway_flap"
  list_cases("retry_death_spiral") → 2 items
  list_cases("payment_gateway_flap") → 1 item
  list_cases(None) → 3 items

Test: find similar cases
  Save case with action "rate_limit_retries" for "retry_death_spiral"
  find_similar("retry_death_spiral", "rate_limit_retries") → 1 item
  find_similar("retry_death_spiral", "restart_payment") → 0 items
```

### 12.4 End-to-End Pipeline Tests

```
Test: full auto run with scenario
  Command: python run.py alerts/latest.json --scenario retry_death_spiral --mode stratus --phase auto
  Expect:
    outputs/retry_death_spiral_plan.json exists and contains:
      mode == "stratus"
      best_action.id == "rate_limit_retries"
      baseline_best_action.id == "restart_payment"
    outputs/retry_death_spiral_verdict.json exists and contains:
      verdict.classification in ("strong_recovery", "partial_failure", "severe_failure")
      verdict.narrative is non-empty string
      decision_comparison.ground_truth == "rate_limit_retries"
    cases/ directory contains at least one .json file

Test: baseline mode run
  Command: python run.py alerts/latest.json --scenario retry_death_spiral --mode baseline --phase auto
  Expect:
    outputs/retry_death_spiral_plan.json:
      best_action.id == "restart_payment"  (baseline's pick)
      mode == "baseline"
    outputs/retry_death_spiral_verdict.json:
      verdict.dangerous_reflex_rejected == False

Test: backward compatibility (no --scenario)
  Command: python run.py alerts/latest.json --phase auto
  Expect: works exactly as before, using hardcoded outcomes.
          No verdict file produced (scenario is None).
```

---

## 13. Implementation Order

```
Step 1: scenarios/__init__.py + 3 JSON files
        ↳ Foundation. Everything depends on this.
        ↳ Can be tested independently: python -c "from scenarios import load_scenario; ..."
        ↳ No changes to existing code needed.

Step 2: agents/simulator.py modification
        ↳ Add optional scenario parameter to simulate_action().
        ↳ Backward compatible — no scenario = uses hardcoded dict.
        ↳ Test: simulate_action(state, action, scenario) uses scenario outcomes.

Step 3: agents/evaluator.py
        ↳ New file, depends on scenarios and simulator.
        ↳ Test: evaluate(plan, actual, scenario) returns valid verdict dict.

Step 4: tools/case_library.py
        ↳ New file, no dependencies.
        ↳ Test: save_case + load_case + list_cases round-trip works.

Step 5: run.py modifications
        ↳ Add --mode, --scenario, --list-scenarios args.
        ↳ Wire scenario loading, evaluator, case library.
        ↳ This is the integration step — run full pipeline end-to-end.

Step 6: tools/visual_control_plane.py — verdict endpoints
        ↳ Add /verdict and /verdict/{scenario_id}.
        ↳ Test: start server, run pipeline, open /verdict in browser.

Step 7: config.py additions
        ↳ Minor — add constants used by other modules.
        ↳ Can be done alongside any step.
```

Each step produces independently testable output. No step breaks existing functionality.

---

*Implementation architecture for Eason Week 2. All module boundaries, function signatures, data schemas, and integration contracts are specified above. A developer should be able to implement each file by reading only the relevant section.*
