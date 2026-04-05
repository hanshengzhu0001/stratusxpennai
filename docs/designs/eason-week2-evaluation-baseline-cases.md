# Design Document: Eason Week 2 — Evaluation Agent, Baseline Toggle, Case Library, Simulation Profiles, Verdict View

**Author:** Eason (Zhou Yincheng)
**Date:** 2026-04-04
**Status:** Draft

---

## 1. Problem Statement

The current system (week 1) handles **one hardcoded incident** with **one set of fixed outcomes**. It produces raw JSON diffs as "evaluation" and runs the baseline via a separate script (`run_baseline.py`) disconnected from the main pipeline. There is no case library, no narrative verdicts, no multi-scenario support, and no frontend verdict surface.

Week 2 requires Eason to deliver five capabilities:

1. **Evaluation Agent** — structured post-action evaluation that produces narrative verdicts, not just JSON diffs
2. **Baseline Demo Toggle** — integrated into the main `run.py` flow so the same shortlist can be ranked by either Stratus or naive baseline with a single flag
3. **Case-Library Writeback & Replay** — persist every run as a reusable case; retrieve prior cases for context
4. **Realistic Incident Simulation Profiles** — a portfolio of scenarios (Retry Death Spiral, Payment Gateway Flap, Seat Hold Clog) with scenario-specific metrics, outcomes, and overlays on top of runtime state
5. **Verdict View** — a frontend panel in the Guardrail Console showing: predicted vs actual, dangerous reflex rejected, blast radius avoided, case saved

The target users are the hackathon judges and the team. The goal is to make the Stratus advantage **visually and narratively obvious**.

## 2. Goals

- **G1:** Evaluation agent produces a structured verdict with narrative explanation ("The obvious fix was wrong because…") for every run
- **G2:** A single `--mode baseline` flag on `run.py` swaps Stratus ranking for baseline on the same shortlist — no separate script needed
- **G3:** Every completed run (plan → execute → verify) persists a full case record to `cases/` that is replay-friendly
- **G4:** At least 3 incident scenarios with distinct metrics, outcomes, and ground truths that can be switched via `--scenario`
- **G5:** The Verdict View frontend endpoint renders the evaluation result as a polished HTML page the judges can see

## 3. Proposed Solution

### 3.1 Overview

Extend the existing pipeline architecture (which already separates agents, tools, and orchestration cleanly) with four new modules and one new frontend endpoint:

- `agents/evaluator.py` — a pure-Python evaluation engine that takes plan + actual outcomes and produces a structured verdict with narrative text. No LLM needed — the verdicts are rule-generated from comparison data, which keeps them deterministic and fast.
- Baseline toggle is a thin layer in `run.py`: when `--mode baseline` is set, the pipeline calls `baseline_choose()` as the primary ranker instead of `rank_actions()`, but still records both for comparison.
- `tools/case_library.py` — file-based case persistence (one JSON file per case in `cases/`). Supports write, list, and retrieve by scenario or action.
- `scenarios/` directory with one JSON profile per scenario, consumed by `agents/simulator.py` and `agents/incident_classifier.py` to overlay scenario-specific metrics and outcomes.
- `/verdict` endpoint in `tools/visual_control_plane.py` that reads `outputs/{scenario_id}_verdict.json` and renders it as HTML.

This approach adds zero new dependencies, keeps every module self-contained, and integrates into the existing `run.py` pipeline with minimal changes.

## 4. Technical Design

### 4.1 Tech Stack

| Criteria | Chosen: Existing Python + FastAPI | Alternative: Add Jinja2 templating | Alternative: Separate React frontend |
| -------- | --------------------------------- | ---------------------------------- | ------------------------------------ |
| Maturity | Python 3.11+, FastAPI 0.115 — battle-tested | Jinja2 is mature, but adds a dependency | React is overkill for this scope |
| Ecosystem | Already in `requirements.txt` | Would need `pip install jinja2` | Would need Node, npm, build tooling |
| Performance | f-string HTML is fast enough for dashboard pages | Slightly slower but negligible | Adds SSR or CSR complexity |
| Team familiarity | Team already writes f-string HTML in `visual_control_plane.py` | Moderate learning curve | Low familiarity for this team |
| Maintainability | Simple, all-in-one, consistent with existing code | Cleaner separation of HTML, but overkill for 4 pages | Way overkill |
| Integration cost | Zero — extend existing files | Low — add one package | High — separate build, CORS, etc. |

**Decision:** Stay with the existing Python + FastAPI + f-string HTML stack. The frontend surface is 4 views total (Eason owns Verdict View only), each is a single endpoint returning an HTML page. Adding Jinja2 or React would be premature abstraction for a hackathon prototype. The current pattern in `visual_control_plane.py` (f-string templates, inline CSS, no JS framework) is already proven and consistent with what the team uses. If the pages grow beyond ~150 lines of HTML, we can extract the HTML into string constants in a `templates.py` file, but there is no reason to do this upfront.

The file-based case library uses plain JSON files rather than SQLite or any database. This is the right call because: (a) the system writes at most a few cases per demo, (b) JSON files are human-readable and git-friendly, (c) no additional dependencies, (d) the case library is a demo feature, not a production database.

### 4.2 File System Structure

```
project/
├── agents/
│   ├── __init__.py                    # (existing)
│   ├── baseline.py                    # (existing, no changes needed)
│   ├── candidate_actions.py           # (existing, minor: accept scenario overlay)
│   ├── evaluator.py                   # NEW — verdict generation engine (~150 lines)
│   ├── incident_classifier.py         # (existing, minor: accept scenario overlay)
│   └── simulator.py                   # (existing, refactor: load outcomes from scenario profile)
├── scenarios/                         # NEW — scenario profile directory
│   ├── __init__.py                    # Scenario loader: load_scenario(scenario_id) → dict
│   ├── retry_death_spiral.json        # Flagship scenario (current incident, formalized)
│   ├── payment_gateway_flap.json      # Secondary scenario
│   └── seat_hold_clog.json            # Stretch scenario
├── tools/
│   ├── __init__.py                    # (existing)
│   ├── case_library.py               # NEW — case persistence & retrieval (~120 lines)
│   ├── visual_control_plane.py        # (existing, extend: add /verdict endpoint)
│   └── ... (other existing tools unchanged)
├── cases/                             # NEW — runtime case storage (gitignored)
│   └── (JSON files created at runtime)
├── config.py                          # (existing, extend: add scenario/case config)
├── run.py                             # (existing, extend: --mode and --scenario flags)
├── run_baseline.py                    # (existing, deprecated — replaced by --mode baseline)
└── outputs/
    └── {scenario_id}_verdict.json     # NEW output artifact per run
```

**Key decisions:**
- `scenarios/` is a top-level directory (not under `agents/`) because scenario profiles are data, not logic
- `cases/` is a runtime directory (like `state/`) — gitignored, created on first write
- `agents/evaluator.py` lives with other agents because it is a decision-making component in the pipeline
- No new files in `tools/` except `case_library.py`, which is a pure data-access module

### 4.3 Encapsulation Design

```
┌─────────────────────────────────────────────────────────────────┐
│                         run.py (orchestrator)                    │
│  Accepts: --phase plan|verify|auto  --mode stratus|baseline      │
│           --scenario retry_death_spiral|payment_gateway_flap|... │
│  Imports from all modules below. No business logic here.         │
└───────┬──────────┬──────────┬──────────┬──────────┬──────────────┘
        │          │          │          │          │
        ▼          ▼          ▼          ▼          ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐
│scenarios/│ │ agents/  │ │ agents/  │ │ agents/  │ │ tools/       │
│__init__ │ │evaluator │ │simulator │ │baseline  │ │case_library  │
│          │ │          │ │          │ │          │ │              │
│ Public:  │ │ Public:  │ │ Public:  │ │ Public:  │ │ Public:      │
│ load()   │ │ evaluate │ │ simulate │ │ choose   │ │ save_case()  │
│ list()   │ │          │ │ compare  │ │          │ │ load_case()  │
│ get()    │ │ Returns: │ │          │ │          │ │ list_cases() │
│          │ │ Verdict  │ │          │ │          │ │ find_similar()│
│ Internal:│ │ dict     │ │ Internal:│ │ Internal:│ │              │
│ _validate│ │          │ │ outcomes │ │ _llm()   │ │ Internal:    │
│ _merge   │ │ Internal:│ │ loaded   │ │ _kw()    │ │ _case_path() │
│          │ │ _score() │ │ from     │ │ _build() │ │ _index_path()│
│          │ │ _narrate│ │ scenario │ │          │ │              │
│          │ │ _class() │ │ profile  │ │          │ │              │
└──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────────┘
        │          │          │
        │          │          │
        └──────────┼──────────┘
                   │
        scenarios/ ─┘  (evaluator and simulator both read scenario profiles)
```

**Module: `scenarios/__init__.py`**
- Public API: `load_scenario(scenario_id: str) -> dict`, `list_scenarios() -> list[str]`, `get_scenario_metadata(scenario_id: str) -> dict`
- Internal: `_validate_profile(data: dict) -> None`, `_merge_with_defaults(data: dict) -> dict`
- Dependencies: None (reads JSON files from its own directory)
- NOT exposed: file paths, raw JSON parsing logic

**Module: `agents/evaluator.py`**
- Public API: `evaluate(plan: dict, actual_outcome: dict, scenario: dict) -> dict` — returns a verdict dict
- Internal: `_compute_scores(predicted: dict, actual: dict) -> dict`, `_classify_recovery(scores: dict) -> str`, `_generate_narrative(plan: dict, scores: dict, classification: str) -> str`, `_baseline_comparison_narrative(plan: dict, actual: dict, scenario: dict) -> str`
- Dependencies: `scenarios` (for ground truth), `agents.simulator` (for `compare_prediction_to_actual`)
- NOT exposed: scoring weights, narrative templates, classification thresholds

**Module: `tools/case_library.py`**
- Public API: `save_case(case: dict) -> str` (returns case_id), `load_case(case_id: str) -> dict`, `list_cases(scenario_id: str | None) -> list[dict]`, `find_similar(scenario_id: str, action_id: str) -> list[dict]`
- Internal: `_case_path(case_id: str) -> Path`, `_generate_case_id(scenario_id: str) -> str`
- Dependencies: None (pure file I/O)
- NOT exposed: file system paths, ID generation logic

**Dependency rules:**
- `run.py` → all modules (orchestrator)
- `agents/evaluator.py` → `scenarios/`, `agents/simulator.py`
- `agents/simulator.py` → `scenarios/` (loads outcome profiles)
- `tools/case_library.py` → nothing (leaf module)
- `agents/baseline.py` → no changes, no new dependencies
- No circular dependencies exist

### 4.4 Scalability & Parallelism

**Parallel operations:**
- Stratus ranking and baseline ranking already run sequentially in `build_plan_report()`. They COULD run in parallel via `concurrent.futures.ThreadPoolExecutor` since both are independent I/O-bound calls. However, for a hackathon demo, the added complexity is not worth the ~1s savings. Keep sequential for now; the architecture supports parallelism if needed later.
- Case library writes are fire-and-forget — the verdict generation does not depend on the case being saved. These can run in a background thread if needed.

**Scaling strategy (if this were production):**
- All modules are stateless except `runtime_state.py` (file-based). The case library is append-only. This means the system could scale horizontally by replacing file I/O with a shared store (Redis, S3), but this is irrelevant for the hackathon.
- Scenario profiles are read-only data — zero contention.

**Async patterns:**
- The FastAPI endpoints (`/verdict`) are already async-capable via FastAPI. The verdict endpoint reads a JSON file and returns HTML — no async needed.
- The LLM call in `baseline.py` is synchronous (OpenAI SDK). This is fine — it blocks for ~1-2s max, and there's only one concurrent user (the demo).

**Caching:**
- Scenario profiles should be loaded once and cached in memory (they're static JSON files). Use a module-level dict in `scenarios/__init__.py`.
- Verdict HTML can be cached per scenario_id since it only changes when a new run completes. Not worth implementing for the hackathon.

**Data partitioning:**
- Cases are partitioned by scenario_id in their filename: `cases/{scenario_id}_{timestamp}.json`. This makes `list_cases(scenario_id)` a simple glob.

### 4.5 Detailed Design

#### 4.5.1 Scenario Profile Schema

Each scenario JSON file in `scenarios/` defines:

```json
{
  "id": "retry_death_spiral",
  "name": "Retry Death Spiral",
  "description": "Payment dependency degrades, checkout retries amplify, latency spikes.",

  "alert_template": {
    "commonLabels": {"alertname": "CheckoutRetryStorm", "severity": "critical", "service": "checkout", "dependency": "payment"},
    "commonAnnotations": {"summary": "Retry pressure above baseline..."}
  },

  "evidence": {
    "services": ["frontend", "checkout", "payment"],
    "metrics": {"latency_p95_ms": 2300, "error_rate": 0.18, "retry_rate": 0.31},
    "logs": ["payment timeout spikes observed", "checkout retries exceeding normal threshold"],
    "traces": ["checkout -> payment span dominates p95", "retry fan-out visible across checkout service"]
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
      "latency_p95_ms": 1900, "error_rate": 0.14, "retry_rate": 0.30,
      "time_to_effect_seconds": 120, "risk_level": "medium", "blast_radius": "medium",
      "recovery": "partial",
      "notes": "Restarting refreshes pods, but the retry loop keeps pressure on payment."
    },
    "disable_flag": {
      "latency_p95_ms": 500, "error_rate": 0.92, "retry_rate": 0.02,
      "time_to_effect_seconds": 15, "risk_level": "very_high", "blast_radius": "high",
      "recovery": "mixed",
      "notes": "Feature disable sheds the degraded dependency path quickly, but disables payment capability."
    },
    "rate_limit_retries": {
      "latency_p95_ms": 1250, "error_rate": 0.20, "retry_rate": 0.10,
      "time_to_effect_seconds": 30, "risk_level": "low", "blast_radius": "low",
      "recovery": "strong",
      "notes": "Retry shaping stabilizes the system while keeping the product mostly available."
    },
    "shift_traffic": {
      "latency_p95_ms": 1450, "error_rate": 0.11, "retry_rate": 0.13,
      "time_to_effect_seconds": 180, "risk_level": "high", "blast_radius": "high",
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
    "why": "A tired engineer restarts what looks broken, but this doesn't fix the retry loop."
  },

  "evaluation_labels": {
    "dangerous_reflex": "restart_payment",
    "safe_action": "rate_limit_retries",
    "reflex_rejected_reason": "Restarting payment pods does not stop the retry amplification loop. The retries continue hammering the recovering service, causing re-degradation within minutes.",
    "blast_radius_avoided": "medium → low",
    "fairness_impact": "Rate limiting preserves payment availability for legitimate requests while shedding excess retry load."
  }
}
```

#### 4.5.2 Evaluation Agent (`agents/evaluator.py`)

The evaluator takes the plan, actual outcome, and scenario profile, and produces:

```python
def evaluate(plan: dict, actual_outcome: dict, scenario: dict) -> dict:
    """
    Returns:
    {
        "scenario_id": "retry_death_spiral",
        "timestamp": "2026-04-04T14:30:00Z",

        "decision_comparison": {
            "stratus_chose": "rate_limit_retries",
            "baseline_chose": "restart_payment",
            "ground_truth": "rate_limit_retries",
            "stratus_correct": true,
            "baseline_correct": false,
            "stratus_confidence": 0.84,
            "baseline_confidence": 0.50
        },

        "outcome_evaluation": {
            "predicted_vs_actual": { ... },   # from simulator.compare_prediction_to_actual
            "drift_score": 1.0,
            "recovery_class": "strong_recovery",  # strong_recovery | partial_failure | severe_failure
            "metrics_after": { "latency_p95_ms": 1250, ... }
        },

        "verdict": {
            "classification": "strong_recovery",
            "dangerous_reflex_rejected": true,
            "reflex_action": "restart_payment",
            "reflex_rejected_reason": "Restarting payment pods does not stop...",
            "safe_action_chosen": true,
            "blast_radius_avoided": "medium → low",
            "fairness_preserved": true,
            "narrative": "The system correctly identified that restarting payment pods — the obvious 3am reflex — would not resolve the retry amplification loop. Instead, it chose rate-limiting checkout retries, which directly addresses the root cause with minimal blast radius. Post-action metrics confirm strong recovery: latency dropped from 2300ms to 1250ms, retry rate fell from 0.31 to 0.10, and blast radius remained low."
        },

        "case_summary": {
            "situation": "Retry amplification + payment degradation...",
            "shortlisted_actions": ["restart_payment", "disable_flag", "rate_limit_retries", "shift_traffic"],
            "chosen_action": "rate_limit_retries",
            "predicted_result": { ... },
            "actual_result": { ... },
            "narrative_verdict": "Strong recovery. The guardrail rejected the dangerous reflex and chose the safer action."
        }
    }
    """
```

**Recovery classification logic:**

```python
def _classify_recovery(drift_score: float, actual_outcome: dict, scenario: dict) -> str:
    """
    strong_recovery:  drift_score >= 0.75 AND recovery in ('strong',) AND blast_radius in ('low',)
    partial_failure:  drift_score >= 0.5 OR recovery == 'partial'
    severe_failure:   drift_score < 0.5 OR blast_radius in ('high', 'very_high') OR recovery == 'mixed'
    """
```

**Narrative generation** uses string templates filled from the evaluation data — no LLM, fully deterministic:

```python
NARRATIVE_TEMPLATES = {
    "strong_recovery": (
        "The system correctly identified that {reflex_action_desc} — the obvious 3am reflex — "
        "would not resolve the {root_cause}. Instead, it chose {safe_action_desc}, which "
        "{safe_action_rationale}. Post-action metrics confirm strong recovery: {metrics_summary}."
    ),
    "partial_failure": (
        "The chosen action {chosen_action_desc} showed partial improvement: {metrics_summary}. "
        "However, {remaining_issues}. A replan may be needed."
    ),
    "severe_failure": (
        "The chosen action {chosen_action_desc} did not achieve the predicted outcome. "
        "{metrics_summary}. Blast radius {blast_detail}. Immediate replan recommended."
    ),
}
```

#### 4.5.3 Baseline Demo Toggle

Changes to `run.py`:

```python
parser.add_argument("--mode", choices=["stratus", "baseline"], default="stratus")
parser.add_argument("--scenario", default=None)  # None = use alert file as-is
```

When `--mode baseline`:
- The pipeline still generates the same shortlist via `generate_candidate_actions()`
- Instead of `rank_actions()` as primary, use `baseline_choose()` as primary
- Still run both for comparison, but swap which is "best_action" vs "comparison"
- The verify and verdict phases work identically — they just evaluate whichever action was chosen

This means the demo can show:
1. Run with `--mode stratus` → guardrail picks `rate_limit_retries` → strong recovery
2. Run with `--mode baseline` → baseline picks `restart_payment` → partial failure
3. Side-by-side comparison is visible in the Verdict View

#### 4.5.4 Case Library (`tools/case_library.py`)

**Storage format:** One JSON file per case in `cases/`:
```
cases/
  retry_death_spiral_20260404_143000.json
  payment_gateway_flap_20260404_143500.json
```

**Case schema:**
```json
{
  "case_id": "retry_death_spiral_20260404_143000",
  "scenario_id": "retry_death_spiral",
  "timestamp": "2026-04-04T14:30:00Z",
  "mode": "stratus",

  "situation": {
    "incident_summary": "...",
    "services": ["frontend", "checkout", "payment"],
    "metrics_before": {"latency_p95_ms": 2300, "error_rate": 0.18, "retry_rate": 0.31},
    "pattern_matches": ["retry_amplification", "payment_degradation"]
  },

  "decision": {
    "shortlisted_actions": ["restart_payment", "disable_flag", "rate_limit_retries", "shift_traffic"],
    "chosen_action": "rate_limit_retries",
    "ranking": [{"id": "rate_limit_retries", "rank": 1, "confidence": 0.84}, ...],
    "mode": "stratus"
  },

  "outcome": {
    "predicted": {"latency_direction": "down", "retry_storm_risk": "low", ...},
    "actual": {"latency_p95_ms": 1250, "error_rate": 0.20, "retry_rate": 0.10, ...},
    "drift_score": 1.0,
    "recovery_class": "strong_recovery"
  },

  "verdict": {
    "classification": "strong_recovery",
    "narrative": "The system correctly identified...",
    "dangerous_reflex_rejected": true,
    "baseline_comparison": {
      "baseline_chose": "restart_payment",
      "baseline_correct": false,
      "stratus_chose": "rate_limit_retries",
      "stratus_correct": true
    }
  }
}
```

**API:**
```python
def save_case(verdict: dict, plan: dict, scenario: dict) -> str:
    """Persist a complete case record. Returns case_id."""

def load_case(case_id: str) -> dict:
    """Load a single case by ID."""

def list_cases(scenario_id: str | None = None) -> list[dict]:
    """List all cases, optionally filtered by scenario. Returns metadata only (no full verdict)."""

def find_similar(scenario_id: str, action_id: str) -> list[dict]:
    """Find prior cases with the same scenario and action for comparison."""
```

#### 4.5.5 Scenario Profiles

Three scenarios, each with distinct characteristics:

| Scenario | Ground Truth | Naive Reflex | Key Difference |
|----------|-------------|--------------|----------------|
| **Retry Death Spiral** (flagship) | `rate_limit_retries` | `restart_payment` | Retry amplification is the root cause; restart doesn't fix the loop |
| **Payment Gateway Flap** | `disable_flag` | `restart_payment` | Payment service is flapping (up/down cycling); restart makes it worse; disable flag isolates cleanly |
| **Seat Hold Clog** | `shift_traffic` | `disable_flag` | Seat holds are stuck in one region; disabling the flag kills all seat functionality; traffic shift relieves pressure |

Each scenario defines its own:
- `evidence` (different metrics baselines)
- `simulated_outcomes` (different outcome profiles per action)
- `ground_truth` and `naive_reflex`
- `evaluation_labels` (for verdict narrative)
- `initial_state` (different runtime state flags)
- `candidate_actions` (may differ — e.g., seat hold scenario could add `clear_seat_holds`)

#### 4.5.6 Verdict View (Frontend)

New endpoint in `tools/visual_control_plane.py`:

```
GET /verdict                → renders latest verdict for current scenario
GET /verdict/{scenario_id}  → renders verdict for specific scenario
```

The Verdict View displays four sections:

1. **Predicted vs Actual** — side-by-side metrics table with color-coded drift (green = match, red = divergence)
2. **Dangerous Reflex Rejected** — highlights what the baseline chose and why it was wrong, what the guardrail chose and why it was right
3. **Blast Radius Avoided** — visual indicator showing blast radius reduction
4. **Case Saved** — confirmation that the case was persisted to the library, with link to prior similar cases

HTML structure (~100 lines of f-string HTML, consistent with existing dashboard style):

```html
<div class="verdict-grid">
  <div class="card verdict-predicted-actual">
    <h2>Predicted vs Actual</h2>
    <!-- metrics comparison table -->
    <div class="drift-score">Drift Score: 1.00 ✓</div>
  </div>
  <div class="card verdict-reflex">
    <h2>Dangerous Reflex Rejected</h2>
    <div class="reflex-wrong">Baseline chose: restart_payment</div>
    <div class="guardrail-right">Guardrail chose: rate_limit_retries</div>
    <p><!-- narrative explanation --></p>
  </div>
  <div class="card verdict-blast">
    <h2>Blast Radius</h2>
    <div class="blast-before">medium</div> → <div class="blast-after">low</div>
  </div>
  <div class="card verdict-case">
    <h2>Case Saved</h2>
    <p>Case ID: retry_death_spiral_20260404_143000</p>
    <p>Similar prior cases: 2</p>
  </div>
</div>
```

#### 4.5.7 Updated Pipeline Flow

```
run.py --scenario retry_death_spiral --mode stratus --phase auto

1. Load scenario profile from scenarios/retry_death_spiral.json
2. Initialize runtime state from scenario.initial_state
3. Use scenario.evidence as mock evidence (or collect from Prometheus)
4. Classify incident (with scenario overlay)
5. Generate candidate actions (from scenario.candidate_actions)
6. Rank with Stratus AND baseline (both always run)
7. If --mode stratus: best_action = stratus pick
   If --mode baseline: best_action = baseline pick
8. Execute chosen action (apply to runtime state)
9. Collect post-action evidence
10. Evaluate: agents/evaluator.evaluate(plan, actual, scenario) → verdict
11. Save case: tools/case_library.save_case(verdict, plan, scenario)
12. Write outputs:
    - outputs/{scenario_id}_plan.json
    - outputs/{scenario_id}_report.json
    - outputs/{scenario_id}_verdict.json     ← NEW
    - outputs/{scenario_id}_browser_playbook.json
13. Verdict View available at http://127.0.0.1:8010/verdict
```

### 4.6 Error Handling

- **Missing scenario file:** `load_scenario()` raises `FileNotFoundError` with clear message: `"Scenario '{id}' not found. Available: {list_scenarios()}"`. `run.py` catches and prints, exits 1.
- **Invalid scenario JSON:** `_validate_profile()` checks for required keys (`id`, `evidence`, `simulated_outcomes`, `ground_truth`). Raises `ValueError` with the missing key name.
- **Unknown action in simulation:** `simulate_action()` raises `KeyError` if the action_id is not in the scenario's `simulated_outcomes`. This is a data error in the scenario file.
- **Case library write failure:** `save_case()` catches `OSError`, logs a warning, and returns `None` instead of a case_id. The pipeline continues — case library failure is non-fatal.
- **Verdict View with no verdict file:** `/verdict` endpoint returns a "No verdict available. Run the pipeline first." HTML page (not a 500 error).

## 5. Alternatives Considered

### 5.1 LLM-Generated Verdicts

- **Description:** Use an LLM to generate narrative verdicts instead of rule-based templates
- **Pros:** More natural language, can handle edge cases, sounds more impressive
- **Cons:** Non-deterministic — same input could produce different verdicts across runs. Adds latency and API cost. The baseline is supposed to be the only LLM-dependent module; making evaluation LLM-dependent blurs the control group boundary. For a hackathon demo, you need reproducible results.

### 5.2 SQLite Case Library

- **Description:** Use SQLite instead of JSON files for case persistence
- **Pros:** Better querying, proper indexing, ACID guarantees
- **Cons:** Adds a dependency, cases aren't human-readable in the file system, overkill for ~10-20 cases in a hackathon demo. JSON files are git-friendly and can be inspected directly.

### 5.3 Separate Verdict Service

- **Description:** Run the Verdict View as a separate FastAPI app
- **Pros:** Clean separation of concerns, independent deployment
- **Cons:** Adds another terminal/port to the demo setup (already 4 terminals). The Verdict View is one endpoint — it belongs in the existing `visual_control_plane.py` which is already the demo's frontend surface.

## 7. Performance Considerations

- Scenario profile loading: cached in memory after first load. The JSON files are <5KB each.
- Case library listing: glob-based, O(n) where n = number of case files. With <100 cases total, this is instant.
- Verdict HTML rendering: f-string interpolation, <1ms.
- The only potential bottleneck is the baseline LLM call (~1-2s). This is unchanged from week 1.

## 8. Milestones

| Milestone | Scope | Dependencies | Verification Criteria |
| --------- | ----- | ------------ | --------------------- |
| **M1: Scenario Profiles** | Create `scenarios/` module with loader + 3 JSON profiles. Refactor `simulator.py` to load outcomes from scenario instead of hardcoded `SIMULATED_OUTCOMES`. Refactor `incident_classifier.py` to accept scenario evidence overlay. | None — foundational | `python -c "from scenarios import load_scenario; print(load_scenario('retry_death_spiral')['ground_truth'])"` prints the correct ground truth. `simulator.simulate_action()` uses scenario outcomes. All 3 scenario files pass validation. |
| **M2: Evaluation Agent** | Create `agents/evaluator.py` with `evaluate()` function. Produces structured verdict with narrative. Wire into `run.py` verify phase. | M1 (needs scenario profiles for ground truth and labels) | `python run.py alerts/latest.json --phase auto` produces `outputs/alert_latest_verdict.json` with all verdict fields populated. Narrative text is coherent and matches the scenario. |
| **M3: Baseline Toggle + Case Library** | Add `--mode` and `--scenario` flags to `run.py`. Create `tools/case_library.py`. Wire case writeback into pipeline. Deprecate `run_baseline.py`. | M1 + M2 | `python run.py --scenario retry_death_spiral --mode baseline --phase auto` runs baseline as primary, produces verdict showing baseline chose wrong. `cases/` directory contains the saved case. `python run.py --scenario retry_death_spiral --mode stratus --phase auto` runs Stratus as primary, produces verdict showing stratus chose correct. |
| **M4: Verdict View** | Add `/verdict` and `/verdict/{scenario_id}` endpoints to `visual_control_plane.py`. Render verdict JSON as polished HTML. | M2 + M3 (needs verdict JSON to render) | Open `http://127.0.0.1:8010/verdict` in browser after a run → shows the four verdict panels with correct data. Works for all 3 scenarios. |

## 9. Open Questions

- [ ] Charlie's scenario specs: Are the Payment Gateway Flap and Seat Hold Clog scenarios finalized? I need the canonical evidence profiles and ground truth actions from Charlie before I can finalize those two scenario JSON files.
- [ ] Hansen's console shell: What URL structure and navigation pattern does Hansen want for the Verdict View? Should `/verdict` be a standalone page or embedded in the console shell iframe/tab?
- [ ] Case library retrieval for demo: Should the Verdict View show "similar prior cases" retrieved from the case library, or is that a stretch goal?
- [ ] Three-action sequence extension: The week2 plan mentions optional 3-step planned sequences. If Tony implements this, the evaluator needs to handle multi-step verdicts (evaluate each step). Should I design for this now or treat it as a post-M4 extension?
- [ ] Scenario selector in UI: Should the `/verdict` page include a scenario dropdown, or will Hansen own that in the console shell's Incident View?

---

*Generated with /design*
