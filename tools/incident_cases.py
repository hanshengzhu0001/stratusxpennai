from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tools.scenario_catalog import get_scenario


def build_alert_payload_for_scenario(scenario_id: str) -> dict:
    scenario = get_scenario(scenario_id)
    alert_profile = scenario.get("alert_profile", {})
    now = datetime.now(timezone.utc).replace(microsecond=0)
    starts_at = now.isoformat().replace("+00:00", "Z")
    fingerprint = uuid.uuid4().hex[:16]
    alertname = alert_profile.get("alertname", "SchedulingIncident")
    service = alert_profile.get("service", "scheduling")
    dependency = alert_profile.get("dependency", "eligibility")
    severity = alert_profile.get("severity", "critical")
    summary = alert_profile.get("summary", scenario.get("headline", "Healthcare access incident"))
    return {
        "receiver": "openclaw-webhook",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": alertname,
                    "dependency": dependency,
                    "instance": "host.docker.internal:9101",
                    "job": "synthetic_incident_target",
                    "service": service,
                    "severity": severity,
                    "scenario_id": scenario_id,
                },
                "annotations": {
                    "summary": summary,
                    "description": scenario.get("story", ""),
                },
                "startsAt": starts_at,
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://127.0.0.1:9091/alerts",
                "fingerprint": fingerprint,
            }
        ],
        "groupLabels": {
            "alertname": alertname,
            "service": service,
        },
        "commonLabels": {
            "alertname": alertname,
            "dependency": dependency,
            "instance": "host.docker.internal:9101",
            "job": "synthetic_incident_target",
            "service": service,
            "severity": severity,
            "scenario_id": scenario_id,
        },
        "commonAnnotations": {
            "summary": summary,
            "description": scenario.get("story", ""),
        },
        "externalURL": "http://127.0.0.1:9091",
        "version": "4",
        "groupKey": f'{{}}:{{alertname="{alertname}", service="{service}"}}',
        "truncatedAlerts": 0,
        "id": "alert_latest",
    }
