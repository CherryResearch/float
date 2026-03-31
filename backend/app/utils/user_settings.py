import json
from pathlib import Path
from typing import Any, Dict

# Determine repo root relative to this file
REPO_ROOT = Path(__file__).resolve().parents[3]
USER_SETTINGS_PATH = REPO_ROOT / "user_settings.json"

DEFAULT_SETTINGS: Dict[str, Any] = {
    "history": [],
    "approval_level": "all",
    "theme": "light",
    "visual_theme": "spring",
    "action_history_retention_days": 7,
    # Web Push: client subscription and user-visible preferences
    "push_subscription": None,
    "push_enabled": False,
    # Lead time in minutes for calendar notifications (frontend default)
    "calendar_notify_minutes": 5,
    "tool_resolution_notifications": True,
    "user_timezone": "",
    # Conversation export defaults
    "export_default_format": "md",
    "export_default_include_chat": True,
    "export_default_include_thoughts": True,
    "export_default_include_tools": True,
    # System prompt split: immutable base + user-appended customization.
    "system_prompt_base": "",
    "system_prompt_custom": "",
    "conversation_folders": {},
    "tool_display_mode": "console",
    "tool_link_behavior": "console",
    "live_transcript_enabled": True,
    "live_camera_default_enabled": False,
    "capture_retention_days": 7,
    "capture_default_sensitivity": "personal",
    "capture_allow_model_raw_image_access": True,
    "capture_allow_summary_fallback": True,
    "default_workflow": "default",
    "enabled_workflow_modules": [],
    "device_display_name": "",
    "device_public_key": "",
    "sync_visible_on_lan": False,
    "sync_visible_online": False,
    "sync_online_url": "",
    "sync_auto_accept_push": False,
    "sync_link_to_source_device": False,
    "sync_remote_url": "",
    "sync_source_namespace": "",
    "sync_saved_peers": [],
    "workspace_profiles": [],
    "active_workspace_id": "root",
    "sync_selected_workspace_ids": ["root"],
    "local_model_registrations": [],
}


def load_settings() -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    if USER_SETTINGS_PATH.exists():
        try:
            with USER_SETTINGS_PATH.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            return DEFAULT_SETTINGS.copy()
    merged = DEFAULT_SETTINGS.copy()
    merged.update(data)
    return merged


def save_settings(data: Dict[str, Any]) -> None:
    try:
        with USER_SETTINGS_PATH.open("r", encoding="utf-8") as f:
            current = json.load(f)
    except Exception:
        current = {}
    settings = {**current, **data}
    if "updated_at" not in data:
        from datetime import datetime, timezone

        settings["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
    USER_SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2),
        encoding="utf-8",
    )
