"""
Centralized configuration for all configurable values.

Every threshold, magic number, and adjustable parameter lives here.
No module should hardcode values that might need to change.
"""

# --- Classification thresholds ---
RETRY_RATE_THRESHOLD = 0.2       # triggers retry_amplification pattern
LATENCY_P95_THRESHOLD_MS = 2000  # triggers high_checkout_latency pattern

# --- Retry risk buckets (used by simulator and run.py) ---
RETRY_RISK_LOW_MAX = 0.12
RETRY_RISK_MEDIUM_MAX = 0.25

# --- Simulator: prediction normalization ---
ERROR_RATE_DOWN_THRESHOLD = 0.18  # below this → error_direction = "down"

# --- Baseline confidence scores ---
BASELINE_CHOSEN_CONFIDENCE = 0.50
BASELINE_OTHER_CONFIDENCE = 0.10
BASELINE_LLM_TEMPERATURE = 0.0

# --- Ground truth (for evaluation) ---
GROUND_TRUTH_ACTION_ID = "rate_limit_retries"

# --- Output paths ---
BASELINE_REPORT_PATH = "outputs/baseline_report.json"

# --- Default evidence (mock, when Prometheus is not available) ---
DEFAULT_EVIDENCE = {
    "services": ["frontend", "checkout", "payment"],
    "metrics": {
        "latency_p95_ms": 2300,
        "error_rate": 0.18,
        "retry_rate": 0.31,
    },
    "logs": [
        "payment timeout spikes observed",
        "checkout retries exceeding normal threshold",
    ],
    "traces": [
        "checkout -> payment span dominates p95",
        "retry fan-out visible across checkout service",
    ],
}
