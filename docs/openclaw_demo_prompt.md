# OpenClaw Demo Prompt

## Current Step-By-Step Test Flow

1. Start the local services in separate terminals from the workspace root:

```bash
.venv/bin/python -m uvicorn tools.alert_receiver:app --host 127.0.0.1 --port 8000
.venv/bin/python -m uvicorn tools.metrics_target:app --host 127.0.0.1 --port 9101
.venv/bin/python -m uvicorn tools.visual_control_plane:app --host 127.0.0.1 --port 8010
docker compose up -d
openclaw gateway
```

2. Open `http://127.0.0.1:8010/operations`, click `Reset to Healthy Baseline`, and confirm the watcher reads `Armed and waiting`.
3. Open the OpenClaw dashboard at `http://127.0.0.1:18789/` in a fresh chat and send the primary prompt below.
4. Stay on `http://127.0.0.1:8010/operations` as the presenter screen and click the business trigger for the active scenario, such as `Open Flu Surge Telehealth Window`.
5. Let OpenClaw wake up from `--phase await-demo`, stay on the single `/openclaw-execution` page, execute the planned remediation, and either run `verify` or `fallback`.
6. Confirm `/openclaw-execution` shows `Verification complete. The execution surface is now showing the final report.` and that the final verdict plus live operations board both reflect the same recovered state.

## Current Status

- Current behavior: the retry-spiral demo now stabilizes on the single execution surface with `Throttle Booking Retries` and reaches roughly `1680 ms` latency, `0.10` retry rate, `low` risk, and `low` blast radius.
- Hansen workflow state: the armed watch/wake flow, business-event trigger, single-page execution surface, soak-based verify/fallback, and live verdict rendering are working, while managed browser clicking is still the least reliable piece and may still fall back.
- Charlie expected improvement: better scenario calibration, stronger healthcare realism, and cleaner final demo metrics should make the incident narratives and board signals more believable.
- Tony expected improvement: shortlist refinement plus `1-3` action sequence planning, checkpoints, and replan logic should make the decision layer meaningfully smarter and less single-step.
- Eason expected improvement: case-library retrieval, baseline-vs-guardrail comparison, and over-time recovery stats should make the final verdict show why we beat the baseline on speed, safety, and efficiency.

Primary Week 2 / final-demo prompt for the OpenClaw dashboard chat:

```text
Use the incident_guardrail skill in this workspace and stay in the same task as a persistent responder. Start by running `.venv/bin/python run.py alerts/latest.json --phase await-demo` and remain idle while it blocks. Only after that command returns should you continue on the single `/openclaw-execution` page, execute the planned remediation, run verification or fallback, inspect the live verdict on that same page, summarize, and then return to `.venv/bin/python run.py alerts/latest.json --phase await-demo` again. Use the browser tool only for localhost surfaces. Never use web_fetch or url-fetch on 127.0.0.1 URLs. If the browser tool fails once, run fallback immediately instead of attempting any localhost fetch. Do not stop after `await-demo` returns, and do not resume old artifacts before it returns.
```

The workspace skill now carries the full behavior:

- run `--phase await-demo`
- wait for a new webhook incident
- auto-acknowledge it and prepare demo artifacts
- execute one guarded action through `/openclaw-execution`
- run `verify` or `fallback`
- summarize the verdict
- return to await-demo mode

Current scope note:

- this is a single-action workflow today
- OpenClaw executes one chosen remediation per incident
- multi-step or three-action sequencing is not active in the live path yet

## How To Test Right Now

1. Start or restart the local services in separate terminals:

```bash
.venv/bin/python -m uvicorn tools.alert_receiver:app --host 127.0.0.1 --port 8000
```

```bash
.venv/bin/python -m uvicorn tools.metrics_target:app --host 127.0.0.1 --port 9101
```

```bash
.venv/bin/python -m uvicorn tools.visual_control_plane:app --host 127.0.0.1 --port 8010
```

```bash
docker compose up -d
```

```bash
openclaw gateway
```

2. Reset the repo to a clean waiting state before arming OpenClaw:

```bash
curl -X POST http://127.0.0.1:8010/api/reset
```

```bash
.venv/bin/python run.py alerts/latest.json --phase idle
```

3. Open the presenter and operator surfaces:

- `http://127.0.0.1:8010/operations`
- `http://127.0.0.1:8010/openclaw-execution`
- `http://127.0.0.1:18789/` for the OpenClaw dashboard

4. In OpenClaw chat, paste the primary prompt from this file.

5. Wait until OpenClaw is blocked in `await-demo`, then trigger the incident from `/operations`:

- choose `Scheduling Retry Spiral`
- click `Apply Scenario`
- click `Open Flu Surge Telehealth Window`

6. Let OpenClaw take over:

- it should wake up automatically
- inspect `/openclaw-execution`
- try the managed browser click
- run `verify` or `fallback`
- stay on `/openclaw-execution` until the page says `Verification complete. The execution surface is now showing the final report.`

7. Treat the run as successful if all of these are true:

- `Executed remediation` matches the plan choice
- `Dangerous reflex` is shown and rejected
- the verdict is rendered on the same `/openclaw-execution` page
- the live result shows improved access metrics, e.g. retry near `0.10`, lower latency, and low or medium risk depending on scenario
- OpenClaw returns to `await-demo` after summarizing

8. If browser control fails once, do not retry browser actions manually. The correct expected behavior is:

- OpenClaw runs `.venv/bin/python run.py alerts/latest.json --phase fallback`
- the same `/openclaw-execution` page updates with the final verdict
- the summary explicitly says fallback was used because browser control was unavailable

## Expected Current Demo Behavior

- `Scheduling Retry Spiral` should usually choose `Throttle Booking Retries`
- a healthy-looking result is now in the range of roughly `After latency 1500-1700`, `Retry rate 0.07-0.10`, `Blast radius low`, and `Recovery strong`
- the presenter board at `/operations` should keep updating live while the execution surface shows the final report

## Explicit Long Prompt

```text
Use the incident_guardrail skill in this workspace and stay in the same task until the workflow is complete. Run `.venv/bin/python run.py alerts/latest.json --phase await-demo` and wait until the next webhook incident is latched and the demo artifacts are prepared. Then read `outputs/alert_latest_openclaw_demo.json` and `outputs/alert_latest_browser_playbook.json`, use the browser only on `/openclaw-execution`, inspect the before-action incident card, click the single remediation button chosen by the guardrail, run verify, stay on `/openclaw-execution` to inspect the live verdict that appears on that same page, and do not read `outputs/alert_latest_report.json` until the page says `Verification complete. The execution surface is now showing the final report.` Never use web_fetch or url-fetch on `http://127.0.0.1:*` targets. If the browser tool fails or times out, do not stop and do not replan. Instead run `.venv/bin/python run.py alerts/latest.json --phase fallback`, stay on `/openclaw-execution`, wait for the same verification-complete message, then read `outputs/alert_latest_report.json` and summarize the final verdict while explicitly noting that execution used the saved-plan fallback because browser control was unavailable. After summarizing, return to `.venv/bin/python run.py alerts/latest.json --phase await-demo`. Do not stop after `await-demo` returns.
```

## Shorter Rehearsal Prompt

```text
Use the incident_guardrail skill in this workspace and stay in the same task until the workflow is complete. Run `.venv/bin/python run.py alerts/latest.json --phase await-demo` and wait for the next incident to be prepared for execution. Then execute the chosen remediation in `/openclaw-execution`, run verify, inspect the live verdict on that same page, summarize why the dangerous reflex was rejected, and return to await-demo mode. If browser control fails, run `.venv/bin/python run.py alerts/latest.json --phase fallback` and stay on `/openclaw-execution` before summarizing. Do not stop after `await-demo` returns.
```

## Resume Active Incident Prompt

```text
Use the incident_guardrail skill in this workspace. An incident is already acknowledged and the demo artifacts are prepared. Resume now by reading `outputs/alert_latest_openclaw_demo.json` and `outputs/alert_latest_browser_playbook.json`, executing the browser step in `/openclaw-execution`, then running verify or fallback. Stay on `/openclaw-execution` and inspect the live verdict there before summarizing the verdict and returning to await-demo mode.
```

## Operator Notes

- `--phase await-demo` is the preferred arm mode.
- `--phase idle` resets the watcher back to a clean idle state.
- If the watcher already shows `Investigating active incident`, use the `Resume Active Incident Prompt` instead of restarting services.
- If the browser bridge is healthy, OpenClaw should follow the playbook end to end after a pending incident is latched.
- If the browser bridge is down, OpenClaw should run `.venv/bin/python run.py alerts/latest.json --phase fallback` and still finish the report.
- `/openclaw-execution` is the primary browser surface for the final demo.
- OpenClaw should stay on that one execution page during a run; the verdict is now rendered there live after verify or fallback.
- Keep `/operations` as the presenter screen for humans. The OpenClaw browser path should stay on `/openclaw-execution` unless you are manually inspecting the business board.
- `web_fetch` / `url-fetch` against `127.0.0.1` is expected to be blocked by OpenClaw security. Do not use it for the demo path.
- `Console Rehearsal: Run Full Flow` is a local backup, not the primary final-demo launch path.
- The intended demo rhythm is:
  - arm OpenClaw once
  - trigger the incident through the webhook / Alertmanager path
  - let OpenClaw wake up and handle it
- Always keep the explanation focused on:
  - dangerous local reflex rejected
  - safer action chosen before action
  - browser execution
  - verified outcome
