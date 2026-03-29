from __future__ import annotations

from fastapi import FastAPI

from tools.prometheus_client import save_alert_payload

app = FastAPI(title="OpenClaw Incident Alert Receiver")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/alerts")
def receive_alert(payload: dict) -> dict:
    path = save_alert_payload(payload)
    return {"status": "stored", "path": str(path)}
