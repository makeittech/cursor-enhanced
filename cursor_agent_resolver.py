"""Resolve cursor-agent binary path from env, config, ~/.local/bin, or PATH."""

import os
import shutil
from typing import Any, Dict, Optional

CONFIG_FILE = os.path.expanduser("~/.cursor-enhanced-config.json")


def _load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                import json
                return json.load(f)
        except Exception:
            pass
    return {}


def get_cursor_agent_path(config: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Resolve cursor-agent path: CURSOR_AGENT_PATH env, config, ~/.local/bin, then PATH. Returns None if not found."""
    if config is None:
        config = _load_config()
    path = (
        os.environ.get("CURSOR_AGENT_PATH")
        or config.get("cursor_agent_path")
        or (config.get("delegate") or {}).get("cursor_agent_path")
    )
    if path:
        path = os.path.expanduser(path)
        return path if os.path.exists(path) else None
    default = os.path.expanduser("~/.local/bin/cursor-agent")
    if os.path.exists(default):
        return default
    return shutil.which("cursor-agent")
