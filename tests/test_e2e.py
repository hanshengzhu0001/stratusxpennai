"""
End-to-end test for the fully integrated incident response pipeline.

Tests:
  1. Simulator determinism
  2. Baseline module output shape matches stratus_guardrail.rank_actions()
  3. Full run.py --phase plan (Stratus + baseline both run)
  4. Full run_baseline.py standalone pipeline

Set BASELINE_LLM_API_KEY for real LLM E2E testing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_dotenv() -> None:
    """Load .env into os.environ so subprocesses inherit the keys."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

from config import DEFAULT_EVIDENCE, GROUND_TRUTH_ACTION_ID

RUN_PY = ROOT / "run.py"
RUN_BASELINE = ROOT / "run_baseline.py"
ALERT_FILE = ROOT / "alerts" / "latest.json"


def run_script(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script)] + list(args),
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


def test_simulator_determinism():
    print("=" * 60)
    print("TEST: Simulator determinism")
    print("=" * 60)

    from agents.simulator import simulate_action

    state = {"metrics": {"latency_p95_ms": 2300, "error_rate": 0.18, "retry_rate": 0.31}}
    for action_id in ("restart_payment", "disable_flag", "rate_limit_retries", "shift_traffic"):
        r1 = simulate_action(state, {"id": action_id})
        r2 = simulate_action(state, {"id": action_id})
        assert r1 == r2, f"NOT deterministic for {action_id}"
        print(f"  {action_id}: blast={r1['blast_radius']}, risk={r1['risk_level']} — PASS")

    print("  PASSED\n")


def test_baseline_output_shape():
    print("=" * 60)
    print("TEST: Baseline output matches stratus_guardrail shape")
    print("=" * 60)

    from agents.baseline import choose_action
    from agents.candidate_actions import generate_candidate_actions
    from agents.incident_classifier import classify_incident

    alert = json.loads(ALERT_FILE.read_text())
    evidence = DEFAULT_EVIDENCE
    state = classify_incident(alert, evidence)
    actions = generate_candidate_actions(state)
    result = choose_action(state, actions)

    # Must match stratus_guardrail.rank_actions() output shape exactly
    required_keys = ["ranked_actions", "best_action", "predicted_effects_by_action", "overall_confidence", "notes"]
    for key in required_keys:
        assert key in result, f"Missing key '{key}' — shape mismatch with stratus_guardrail"

    assert isinstance(result["ranked_actions"], list)
    assert len(result["ranked_actions"]) == len(actions)
    assert result["ranked_actions"][0]["rank"] == 1

    best = result["best_action"]
    assert "id" in best, "best_action missing 'id'"
    assert "description" in best, "best_action missing 'description'"
    assert best["id"] in [a["id"] for a in actions]

    print(f"  Shape:       PASS (all {len(required_keys)} required keys present)")
    print(f"  Chose:       {best['id']}")
    print(f"  Confidence:  {result['overall_confidence']}")
    print(f"  Rankings:    {[r['id'] for r in result['ranked_actions']]}")
    print("  PASSED\n")


def test_run_py_plan_phase():
    using_llm = bool(os.environ.get("BASELINE_LLM_API_KEY"))
    print("=" * 60)
    print(f"TEST: run.py --phase plan (LLM={'YES' if using_llm else 'NO'})")
    print("=" * 60)

    if not using_llm:
        print("  *** Set BASELINE_LLM_API_KEY for real LLM E2E ***")

    result = run_script(RUN_PY, str(ALERT_FILE), "--phase", "plan")
    if result.stderr:
        # Filter out expected warnings
        for line in result.stderr.strip().split("\n"):
            if "WARNING" in line:
                print(f"  [warn] {line.strip()}")
            else:
                print(f"  [stderr] {line.strip()}")

    assert result.returncode == 0, (
        f"run.py --phase plan failed (exit {result.returncode}).\n"
        f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
    )

    # Verify plan output file
    plan_path = ROOT / "outputs" / "alert_latest_plan.json"
    assert plan_path.exists(), "Plan output not created"
    plan = json.loads(plan_path.read_text())

    # Verify Stratus fields
    assert "stratus_ranking" in plan, "Plan missing stratus_ranking"
    assert "best_action" in plan, "Plan missing best_action"
    assert "overall_confidence" in plan, "Plan missing overall_confidence"

    # Verify BASELINE fields (our integration)
    assert "baseline_ranking" in plan, "Plan missing baseline_ranking"
    assert "baseline_best_action" in plan, "Plan missing baseline_best_action"
    assert "baseline_confidence" in plan, "Plan missing baseline_confidence"
    assert "baseline_notes" in plan, "Plan missing baseline_notes"

    stratus_chose = plan["best_action"]["id"]
    baseline_chose = plan["baseline_best_action"]["id"]

    print(f"  Stratus chose:     {stratus_chose} (confidence: {plan['overall_confidence']})")
    print(f"  Baseline chose:    {baseline_chose} (confidence: {plan['baseline_confidence']})")
    print(f"  Same choice:       {stratus_chose == baseline_chose}")

    # Verify markdown output
    md_path = ROOT / "outputs" / "alert_latest_plan.md"
    assert md_path.exists(), "Plan markdown not created"

    # Verify browser playbook
    playbook_path = ROOT / "outputs" / "alert_latest_browser_playbook.json"
    assert playbook_path.exists(), "Browser playbook not created"

    print("  Plan JSON:         PASS")
    print("  Plan MD:           PASS")
    print("  Browser playbook:  PASS")
    print("  PASSED\n")


def test_run_baseline_standalone():
    print("=" * 60)
    print("TEST: run_baseline.py standalone")
    print("=" * 60)

    result = run_script(RUN_BASELINE, str(ALERT_FILE))
    if result.stderr:
        for line in result.stderr.strip().split("\n"):
            if "WARNING" in line:
                print(f"  [warn] {line.strip()}")

    assert result.returncode == 0, (
        f"run_baseline.py failed (exit {result.returncode}).\nstderr: {result.stderr[:500]}"
    )

    report_path = ROOT / "outputs" / "baseline_report.json"
    assert report_path.exists(), "baseline_report.json not created"
    report = json.loads(report_path.read_text())

    assert report["baseline"]["chosen_action"]["id"] in [
        "restart_payment", "disable_flag", "rate_limit_retries", "shift_traffic"
    ]
    assert report["ground_truth"]["action"]["id"] == GROUND_TRUTH_ACTION_ID

    # Verify determinism
    result2 = run_script(RUN_BASELINE, str(ALERT_FILE))
    assert result2.returncode == 0
    report2 = json.loads(report_path.read_text())
    assert report["baseline"]["outcome"] == report2["baseline"]["outcome"], "Simulator not deterministic"

    print(f"  Baseline chose:    {report['baseline']['chosen_action']['id']}")
    print(f"  Correct:           {report['baseline']['chose_correct']}")
    print(f"  Determinism:       PASS")
    print("  PASSED\n")


def test_stratus_guardrail_shape():
    """Verify stratus_guardrail returns the expected shape (live or mock)."""
    print("=" * 60)
    print("TEST: stratus_guardrail output shape")
    print("=" * 60)

    from tools.stratus_guardrail import rank_actions
    from agents.candidate_actions import generate_candidate_actions

    state = {"services": ["payment"], "metrics": {}, "summary": "test"}
    actions = generate_candidate_actions(state)
    result = rank_actions(state, actions)

    required_keys = ["ranked_actions", "best_action", "predicted_effects_by_action", "overall_confidence", "notes"]
    for key in required_keys:
        assert key in result, f"stratus_guardrail missing '{key}'"

    assert isinstance(result["ranked_actions"], list)
    assert len(result["ranked_actions"]) > 0, "ranked_actions is empty"

    best = result["best_action"]
    valid_ids = [a["id"] for a in actions]
    # best_action can be a dict or string depending on live vs mock
    best_id = best["id"] if isinstance(best, dict) else best
    assert best_id in valid_ids, f"best_action '{best_id}' not in valid actions: {valid_ids}"

    is_live = any("live" in n.lower() or "stratus" in n.lower() for n in result.get("notes", []))
    print(f"  Mode:          {'LIVE API' if is_live else 'MOCK'}")
    print(f"  Shape:         PASS")
    print(f"  Best action:   {best_id}")
    print(f"  Confidence:    {result['overall_confidence']}")
    print(f"  Rankings:      {[r['id'] if isinstance(r, dict) else r for r in result['ranked_actions']]}")
    print("  PASSED\n")


def test_real_llm_baseline():
    """Test baseline with real LLM API call. Skips if no API key."""
    from tools.llm_client import is_configured
    if not is_configured():
        print("=" * 60)
        print("SKIP: test_real_llm_baseline (no BASELINE_LLM_API_KEY)")
        print("=" * 60 + "\n")
        return

    print("=" * 60)
    print("TEST: Real LLM baseline call")
    print("=" * 60)

    from agents.baseline import choose_action
    from agents.candidate_actions import generate_candidate_actions
    from agents.incident_classifier import classify_incident

    alert = json.loads(ALERT_FILE.read_text())
    state = classify_incident(alert, DEFAULT_EVIDENCE)
    actions = generate_candidate_actions(state)
    result = choose_action(state, actions)

    # Verify shape
    assert "ranked_actions" in result
    assert "best_action" in result
    best = result["best_action"]
    assert best["id"] in [a["id"] for a in actions], f"LLM returned invalid action: {best['id']}"

    # Verify it used LLM (not keyword fallback)
    assert any("llm" in n.lower() for n in result["notes"]), (
        f"Expected LLM path but got: {result['notes']}"
    )

    print(f"  LLM chose:     {best['id']}")
    print(f"  Reason:        {result['ranked_actions'][0]['rationale']}")
    print(f"  Notes:         {result['notes']}")
    print("  PASSED\n")


def test_run_py_plan_with_llm():
    """Test full run.py --phase plan with real API. Skips if no key."""
    using_llm = bool(os.environ.get("BASELINE_LLM_API_KEY"))
    if not using_llm:
        print("=" * 60)
        print("SKIP: test_run_py_plan_with_llm (no BASELINE_LLM_API_KEY)")
        print("=" * 60 + "\n")
        return

    print("=" * 60)
    print("TEST: run.py --phase plan WITH real LLM")
    print("=" * 60)

    result = run_script(RUN_PY, str(ALERT_FILE), "--phase", "plan")
    assert result.returncode == 0, (
        f"run.py failed (exit {result.returncode}).\nstderr: {result.stderr[:500]}"
    )

    plan_path = ROOT / "outputs" / "alert_latest_plan.json"
    plan = json.loads(plan_path.read_text())

    stratus_chose = plan["best_action"]["id"]
    baseline_chose = plan["baseline_best_action"]["id"]

    # Both must be valid actions
    valid = ["restart_payment", "disable_flag", "rate_limit_retries", "shift_traffic"]
    assert stratus_chose in valid, f"Stratus invalid: {stratus_chose}"
    assert baseline_chose in valid, f"Baseline invalid: {baseline_chose}"

    # Baseline should NOT have keyword fallback warning
    assert "keyword" not in result.stderr.lower() or "WARNING" not in result.stderr, (
        "Expected real LLM but got keyword fallback"
    )

    print(f"  Stratus chose:     {stratus_chose} (confidence: {plan['overall_confidence']})")
    print(f"  Baseline chose:    {baseline_chose} (confidence: {plan['baseline_confidence']})")
    print(f"  Same choice:       {stratus_chose == baseline_chose}")
    print(f"  Baseline notes:    {plan['baseline_notes']}")
    print("  PASSED\n")


if __name__ == "__main__":
    test_simulator_determinism()
    test_baseline_output_shape()
    test_stratus_guardrail_shape()
    test_real_llm_baseline()
    test_run_baseline_standalone()
    test_run_py_plan_phase()
    test_run_py_plan_with_llm()

    print("=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)
