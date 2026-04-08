# OpenClaw + Stratus Counterfactual Access Guardrail

This repo is the hackathon demo for a narrow healthcare-access decision module:

- OpenClaw is the outer orchestrator
- Stratus is only used at the guarded decision step
- local Python handles evidence intake, action generation, execution glue, and reporting

The current demo supports:

- alert intake through a local webhook
- live Prometheus-backed evidence collection
- a healthcare-access remediation library with scenario-specific shortlist selection
- Stratus ranking and predicted downstream effects
- one-action guarded remediation execution
- a dedicated telehealth-style operations board at `/operations`
- a dedicated OpenClaw execution surface at `/openclaw-execution`
- a browser playbook for OpenClaw to execute
- post-action verification and predicted-vs-actual comparison

What it does not do yet:

- no multi-step or multi-action execution in the live workflow
- no automatic replanning chain after action 1
- three-action sequencing remains an extension, not part of the current shipped path

## Recommended Demo Path

1. Start the local services and OpenClaw gateway.
2. In OpenClaw chat, paste the prompt from `docs/openclaw_demo_prompt.md` to arm the watcher once.
3. OpenClaw runs `run.py --phase await-demo` and waits for a new webhook incident while preparing the demo artifacts automatically when one arrives.
4. Trigger a business event from the Telehealth Scheduling Stability Board, such as `Open Flu Surge Telehealth Window` or one of the prepared case buttons.
5. When the incident is latched, the blocking `await-demo` command returns with the generated artifacts, and OpenClaw opens `/openclaw-execution`, clicks the single remediation button, runs verify, then opens `/openclaw-execution?stage=verdict`.
6. If browser control fails, OpenClaw runs `run.py --phase fallback` and still finishes the report.

This is a single-action loop today:

1. observe the incident
2. shortlist candidate actions
3. let Stratus choose one guarded action
4. execute that one action
5. verify the outcome

There is no live multi-step sequence execution in the current demo.

## Architecture

Core workflow:

1. Alertmanager posts an incident payload to `tools/alert_receiver.py`.
2. `run.py --phase await-demo` waits for the next incident, latches it from `alerts/latest.json`, acknowledges it, then writes both a dedicated OpenClaw demo-mode artifact and the browser handoff artifact.
3. `run.py --phase plan` loads evidence, has the Planner Agent select a shortlist from the healthcare-access action library, and calls Stratus to rank only that shortlist.
4. OpenClaw reads the prepared artifacts and continues directly into the browser step.
5. OpenClaw browser follows those artifacts:
   - open `/openclaw-execution`
   - inspect the before-action incident card
   - click one stable execution button
   - run verify
   - open `/openclaw-execution?stage=verdict`
6. `run.py --phase verify` re-queries Prometheus and compares predicted vs actual.
7. Final outputs are written into `outputs/`, and the watcher returns to armed mode for the next incident.

## Repo Layout

- `run.py`: main pipeline entrypoint
- `agents/candidate_actions.py`: candidate remediation generation
- `agents/incident_classifier.py`: incident pattern recognition
- `agents/simulator.py`: normalization and predicted-vs-actual comparison
- `tools/stratus_guardrail.py`: Stratus API integration
- `tools/prometheus_client.py`: evidence collection from Prometheus
- `tools/alert_receiver.py`: local Alertmanager webhook receiver
- `tools/metrics_target.py`: synthetic Prometheus scrape target
- `tools/visual_control_plane.py`: Guardrail Console shell, dedicated OpenClaw execution surface, and manual ops fallback surface
- `tools/runtime_state.py`: shared state across UI and metrics
- `tools/scenario_catalog.py`: healthcare scheduling scenario metadata and business-metric overlays
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

Note:

- the underlying Prometheus metric names still use legacy `checkout/payment` identifiers for demo speed
- the operator-facing product surfaces and documentation map those internals to healthcare scheduling language

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

### 4. Start OpenClaw

In a separate terminal:

```bash
openclaw gateway
```

Then open the dashboard at `http://127.0.0.1:18789/` and invoke:

```text
Use the incident_guardrail skill in this workspace. Start by running `.venv/bin/python run.py alerts/latest.json --phase await-demo`, let that command block until the next incident is latched and the demo artifacts are prepared, then continue through browser execution, verification or fallback, summary, and return to await-demo mode.
```

The skill is defined in `skills/incident_guardrail/SKILL.md`.

## Running the Demo

The default story is a **high-demand telehealth / specialist scheduling release**:

- patient portal demand spikes
- eligibility verification degrades
- booking retries amplify the issue
- slot holds clog access
- the dangerous reflex is restarting eligibility too early
- the safer first move is usually throttling retries or enabling an eligibility circuit breaker

Reality anchor:

- the product story mirrors real healthcare scheduling concepts such as appointment slots, holds, and eligibility checks
- if we want a more literal surface later, we can mirror open-source scheduling systems such as OpenEMR while keeping the same guardrail flow
- for Week 2, the architecture stays the same and only the operator-facing semantics change

### Reset the incident state

```bash
curl -X POST http://127.0.0.1:8010/api/reset
```

Optional watcher reset only:

```bash
.venv/bin/python run.py alerts/latest.json --phase idle
```

### Arm OpenClaw first

```bash
.venv/bin/python run.py alerts/latest.json --phase await-demo
```

This blocks until a new webhook incident is latched, acknowledged, and converted into demo artifacts for OpenClaw.

Recommended OpenClaw chat prompt:

```text
Use the incident_guardrail skill in this workspace and stay in the same task until the workflow is complete. Start by running `.venv/bin/python run.py alerts/latest.json --phase await-demo`, let that command block until the next incident is latched and the demo artifacts are prepared, then continue immediately through browser execution, verification or fallback, summary, and return to await-demo mode. Do not stop after `await-demo` returns.
```

### Trigger the incident

Primary demo path:

- open `http://127.0.0.1:8010/operations`
- click a business-event trigger such as `Open Flu Surge Telehealth Window` or one of the prepared incident-case buttons

CLI fallback:

```bash
curl -X POST http://127.0.0.1:8000/alerts \
  -H 'Content-Type: application/json' \
  --data @alerts/latest.json
```

### OpenClaw handles the rest

Once the incident is latched, the blocking `await-demo` command should return and the following files should already exist:

- `outputs/alert_latest_plan.json`
- `outputs/alert_latest_plan.md`
- `outputs/alert_latest_openclaw_demo.json`
- `outputs/alert_latest_openclaw_demo.md`
- `outputs/alert_latest_browser_playbook.json`
- `outputs/alert_latest_browser_playbook.md`

Primary path:

- let OpenClaw browser follow:
  - `outputs/alert_latest_openclaw_demo.json`
  - `outputs/alert_latest_browser_playbook.json`
- OpenClaw should use:
  - `http://127.0.0.1:8010/openclaw-execution`
  - `#openclaw-demo-run`
  - `http://127.0.0.1:8010/openclaw-execution?stage=verdict`

Expected runtime behavior:

1. `await-demo` blocks
2. you trigger an incident from `/operations`
3. OpenClaw receives prepared artifacts
4. OpenClaw opens `/openclaw-execution`
5. OpenClaw attempts one managed browser click for the chosen action
6. if that click succeeds, OpenClaw runs `verify`
7. if that click fails, OpenClaw runs `fallback`
8. either way, OpenClaw should end on `/openclaw-execution?stage=verdict`
9. OpenClaw summarizes the verdict and returns to `await-demo`

Fallback path:

1. if browser control fails, do not replan
2. run:

```bash
.venv/bin/python run.py alerts/latest.json --phase fallback
```

3. read:
   - `outputs/alert_latest_report.json`
   - `outputs/alert_latest_report.md`
4. summarize that execution used the saved-plan fallback because browser control was unavailable

Manual console backup:

1. open `http://127.0.0.1:8010/`
2. use the Guardrail Console to inspect the incident, shortlist, and execution flow
3. open `http://127.0.0.1:8010/openclaw-execution`
4. click the chosen remediation button there
5. run verify or refresh the verdict page

### Resume an already active incident

If the UI already shows `Investigating active incident`, do not restart all services. Use this in OpenClaw chat:

```text
Use the incident_guardrail skill in this workspace. An incident is already acknowledged and the demo artifacts are prepared. Resume now by reading `outputs/alert_latest_openclaw_demo.json` and `outputs/alert_latest_browser_playbook.json`, executing the browser step in `/openclaw-execution`, then running verify or fallback. If fallback is used, still open `/openclaw-execution?stage=verdict` before summarizing the verdict and returning to await-demo mode.
```

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

- dedicated OpenClaw execution URL
- dedicated verdict URL
- exact remediation selector
- execution steps
- verify command

The OpenClaw demo-mode artifact includes:

- starter OpenClaw prompt
- OpenClaw-first execution contract
- browser sequence for the managed browser session
- fallback command for browser outages
- final summary requirements

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

OpenClaw demo-mode handoff:

```bash
.venv/bin/python run.py alerts/latest.json --phase demo
```

Verification:

```bash
.venv/bin/python run.py alerts/latest.json --phase verify
```

One-shot local test:

```bash
.venv/bin/python run.py alerts/latest.json --phase auto
```
