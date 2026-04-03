# OpenClaw + Stratus Counterfactual Remediation Guardrail

This repo is the hackathon demo for a narrow incident-response decision module:

- OpenClaw is the outer orchestrator
- Stratus is only used at the guarded decision step
- local Python handles evidence intake, action generation, execution glue, and reporting

The current demo supports:

- alert intake through a local webhook
- live Prometheus-backed evidence collection
- a broader retail remediation library with scenario-specific shortlist selection
- Stratus ranking and predicted downstream effects
- a browser playbook for OpenClaw to execute
- post-action verification and predicted-vs-actual comparison

## Architecture

Core workflow:

1. Alertmanager posts an incident payload to `tools/alert_receiver.py`.
2. `run.py --phase plan` loads evidence, has the Planner Agent select a shortlist from the retail action library, and calls Stratus to rank only that shortlist.
3. The plan writes a browser handoff artifact at `outputs/alert_latest_browser_playbook.json`.
4. OpenClaw browser follows that artifact:
   - open dashboard
   - inspect incident
   - open feature flags
   - click the chosen remediation
   - refresh dashboard
5. `run.py --phase verify` re-queries Prometheus and compares predicted vs actual.
6. Final outputs are written into `outputs/`.

## Repo Layout

- `run.py`: main pipeline entrypoint
- `agents/candidate_actions.py`: candidate remediation generation
- `agents/incident_classifier.py`: incident pattern recognition
- `agents/simulator.py`: normalization and predicted-vs-actual comparison
- `tools/stratus_guardrail.py`: Stratus API integration
- `tools/prometheus_client.py`: evidence collection from Prometheus
- `tools/alert_receiver.py`: local Alertmanager webhook receiver
- `tools/metrics_target.py`: synthetic Prometheus scrape target
- `tools/visual_control_plane.py`: Guardrail Console shell, execution view, and feature-flag UI
- `tools/runtime_state.py`: shared state across UI and metrics
- `tools/scenario_catalog.py`: concert ticket scenario metadata and business-metric overlays
- `outputs/`: generated plan, browser playbook, and report artifacts
- `k8s/`: Level 3 Kubernetes and Chaos Mesh scaffolding

## Setup

### 1. Create the local virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Configure environment variables

Create a local `.env` file with:

```bash
STRATUS_API_KEY=...
STRATUS_BASE_URL=https://api.stratus.run/v1
STRATUS_MODEL=stratus-x1ac-base-claude-sonnet-4-5
PROMETHEUS_BASE_URL=http://127.0.0.1:9091
PROMQL_CHECKOUT_LATENCY_P95_MS=checkout_latency_p95_ms
PROMQL_CHECKOUT_ERROR_RATE=checkout_error_rate
PROMQL_CHECKOUT_RETRY_RATE=checkout_retry_rate
CONTROL_PLANE_BASE_URL=http://127.0.0.1:8010
```

### 3. Start the local services

Terminal 1:

```bash
.venv/bin/python -m uvicorn tools.alert_receiver:app --host 127.0.0.1 --port 8000
```

Terminal 2:

```bash
.venv/bin/python -m uvicorn tools.metrics_target:app --host 127.0.0.1 --port 9101
```

Terminal 3:

```bash
.venv/bin/python -m uvicorn tools.visual_control_plane:app --host 127.0.0.1 --port 8010
```

Terminal 4:

```bash
docker compose up -d
```

### 4. Optional: OpenClaw workspace usage

Use this repo as an OpenClaw workspace and invoke:

```text
Use the incident_guardrail skill on the latest alert.
```

The skill is defined in `skills/incident_guardrail/SKILL.md`.

## Running the Demo

### Reset the incident state

```bash
curl -X POST http://127.0.0.1:8010/api/reset
```

### Post the alert payload

```bash
curl -X POST http://127.0.0.1:8000/alerts \
  -H 'Content-Type: application/json' \
  --data @alerts/latest.json
```

### Generate the Stratus-backed plan

```bash
.venv/bin/python run.py alerts/latest.json --phase plan
```

This writes:

- `outputs/alert_latest_plan.json`
- `outputs/alert_latest_plan.md`
- `outputs/alert_latest_browser_playbook.json`
- `outputs/alert_latest_browser_playbook.md`

### Execute through the browser

Either:

- let OpenClaw browser follow `outputs/alert_latest_browser_playbook.json`

or manually:

1. open `http://127.0.0.1:8010/`
2. use the Guardrail Console to inspect the incident, shortlist, and execution flow
3. open `http://127.0.0.1:8010/feature-flags`
4. click the chosen remediation button from the playbook
5. return to the console and refresh the verdict

### Verify after execution

```bash
.venv/bin/python run.py alerts/latest.json --phase verify
```

This writes:

- `outputs/alert_latest_report.json`
- `outputs/alert_latest_report.md`

## Demo Artifacts

- `docs/final_demo_script.md`
- `docs/openclaw_demo_prompt.md`

## Output Artifacts

The final report includes:

- incident summary
- action library and planner shortlist metadata
- candidate actions
- Stratus ranking
- chosen action
- predicted downstream effects
- actual outcome
- predicted-vs-actual drift

The browser playbook includes:

- dashboard URL
- feature-flag URL
- exact remediation selector
- execution steps
- verify command

## Current Demo Status

Week 1 deliverable is complete:

- one end-to-end pipeline runs a scenario from start to finish
- Stratus is live for guarded action ranking
- OpenClaw can orchestrate the browser handoff
- Prometheus verifies after action
- outputs are consistent and demo-ready

Level 3 Kubernetes/Chaos execution is scaffolded but not yet the active production path:

- `k8s/` contains OTel Demo / Chaos Mesh-related manifests
- the current execution surface used in the demo is still the local visual control plane

## Main Commands

Planning:

```bash
.venv/bin/python run.py alerts/latest.json --phase plan
```

Verification:

```bash
.venv/bin/python run.py alerts/latest.json --phase verify
```

One-shot local test:

```bash
.venv/bin/python run.py alerts/latest.json --phase auto
```
