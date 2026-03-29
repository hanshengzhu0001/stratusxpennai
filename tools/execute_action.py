from __future__ import annotations

import os

from tools.runtime_state import apply_action, control_plane_urls


def execute_action(action: dict) -> dict:
    base_url = os.environ.get("CONTROL_PLANE_BASE_URL", "http://127.0.0.1:8010")
    updated_state = apply_action(action["id"])
    return {
        "executor": "visual_control_plane",
        "mode": "visual_state_applied",
        "action_id": action["id"],
        "notes": f"Applied {action['id']} to the shared control-plane state. OpenClaw browser can capture before/after evidence from the local UI.",
        "control_plane_urls": control_plane_urls(base_url),
        "state_after_action": updated_state,
    }
