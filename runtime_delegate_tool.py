"""
Delegate Tool - Spawn agents with predefined personalities and roles

Lets the main agent delegate tasks to sub-agents with fixed personas (system prompts).
Runs cursor-agent in a subprocess with the persona's system prompt and the task;
returns the sub-agent's response. No gateway required.
"""

import json
import os
import subprocess
import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger("cursor_enhanced.runtime_delegate")


def _ha_token_from_mcp_config(mcp_config_path: Optional[str]) -> Optional[str]:
    """Read Home Assistant access token from MCP config file (HOME_ASSISTANT_ACCESS_TOKEN)."""
    if not mcp_config_path:
        return None
    path = os.path.expanduser(str(mcp_config_path))
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        servers = data.get("mcpServers") or data.get("mcp_servers") or {}
        ha = servers.get("home-assistant") or servers.get("home_assistant") or {}
        env = ha.get("env") or {}
        return env.get("HOME_ASSISTANT_ACCESS_TOKEN") or env.get("HOME_ASSISTANT_TOKEN")
    except Exception:
        return None


# Default personas (id -> persona dict)
DEFAULT_PERSONAS: List[Dict[str, Any]] = [
    {
        "id": "researcher",
        "name": "Researcher",
        "system_prompt": (
            "You are a thorough researcher. Your role is to gather and summarize information, "
            "cite sources when possible, and present clear, structured answers. Stay factual and concise."
        ),
        "model": None,
    },
    {
        "id": "coder",
        "name": "Coder",
        "system_prompt": (
            "You are a pragmatic software engineer. Write clean, working code. Prefer standard libraries "
            "and clear logic. Include minimal comments only where necessary. Output code first, brief explanation after."
        ),
        "model": None,
    },
    {
        "id": "reviewer",
        "name": "Reviewer",
        "system_prompt": (
            "You are a critical reviewer. Analyze the given content for correctness, style, security, "
            "and maintainability. List concrete issues and short suggestions. Be concise and actionable."
        ),
        "model": None,
    },
    {
        "id": "writer",
        "name": "Writer",
        "system_prompt": (
            "You are a clear technical writer. Explain concepts in plain language, use structure (headers, lists), "
            "and avoid jargon unless necessary. Keep answers focused and readable."
        ),
        "model": None,
    },
    {
        "id": "home_assistant",
        "name": "Home Assistant",
        "system_prompt": (
            "Home Assistant specialist. Use MCP to list/control entities, call services, check states; suggest automations. "
            "Be concise and precise with entity IDs and service names.\n\n"
            "**IMPORTANT: Home Assistant Location**\n"
            "- Home Assistant runs on Proxmox VM 100, hostname 'homeassistant'\n"
            "- All configuration files are located at: /mnt/data/supervisor/homeassistant/\n"
            "- Main config: /mnt/data/supervisor/homeassistant/configuration.yaml\n"
            "- Automations: /mnt/data/supervisor/homeassistant/automations.yaml\n"
            "- Scripts: /mnt/data/supervisor/homeassistant/scripts.yaml\n"
            "- Do NOT use paths like /home/ubuntu/swarm/hass-config/ - that is incorrect.\n\n"
            "**Adding input_boolean helpers (virtual switches):**\n"
            "- Option 1 (YAML): SSH to host 'homeassistant' (Proxmox VM 100), edit /mnt/data/supervisor/homeassistant/configuration.yaml, "
            "add under 'input_boolean:' section (e.g., 'blackout: name: BLACKOUT initial: off'). Restart HA or reload config.\n"
            "- Option 2 (UI): Settings → Devices & Services → Helpers → Create Helper → Toggle → configure → Create.\n"
            "- Entity ID format: input_boolean.<helper_id>\n\n"
            "**Adding automations:** (1) SSH to host 'homeassistant' (Proxmox VM 100). "
            "(2) Edit /mnt/data/supervisor/homeassistant/automations.yaml and append new automation(s) as YAML list items "
            "(each - alias: ... with trigger/condition/action). (3) Reload: automation.reload (Bearer token from "
            "HOME_ASSISTANT_TOKEN).\n\n"
            "**After adding or changing scripts/automations/helpers:** verify via MCP (e.g. get state or list the entity) and report "
            "clearly: \"Success: <entity_id> available\" or \"Failed: <entity_id> not found\".\n\n"
            "**MCP server name:** Use server \"home-assistant\" (with hyphen) when calling MCP tools or list_mcp_resources; do not use \"home_assistant\" (underscore) as the server name."
        ),
        "model": None,
    },
]


@dataclass
class AgentPersona:
    """Predefined agent personality/role for delegation."""
    id: str
    name: str
    system_prompt: str
    model: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DelegateTool:
    """
    Delegate a task to a sub-agent with a predefined personality/role.
    Runs cursor-agent with the persona's system prompt and returns the response.
    Use delegate to offload work (research, code, review, writing) and get a single response back.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, cursor_agent_path: Optional[str] = None):
        self.config = config or {}
        self._cursor_agent_path = (
            cursor_agent_path
            or self.config.get("cursor_agent_path")
            or (self.config.get("delegate") or {}).get("cursor_agent_path")
            or os.path.expanduser("~/.local/bin/cursor-agent")
        )
        self._personas: Dict[str, AgentPersona] = {}
        self._load_personas()

    def _load_personas(self) -> None:
        """Load personas from config, then merge with defaults (config overrides)."""
        for p in DEFAULT_PERSONAS:
            self._personas[p["id"]] = AgentPersona(
                id=p["id"],
                name=p.get("name", p["id"]),
                system_prompt=p.get("system_prompt", ""),
                model=p.get("model"),
            )
        custom = self.config.get("agent_personas") or []
        for p in custom:
            if isinstance(p, dict) and p.get("id"):
                self._personas[p["id"]] = AgentPersona(
                    id=p["id"],
                    name=p.get("name", p["id"]),
                    system_prompt=p.get("system_prompt", ""),
                    model=p.get("model"),
                )

    def list_personas(self) -> List[Dict[str, Any]]:
        """List available persona ids and names (for tool discovery)."""
        return [{"id": p.id, "name": p.name} for p in self._personas.values()]

    async def execute(
        self,
        persona_id: str,
        task: str,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Run the given task with the specified persona and return the sub-agent response.
        """
        if not persona_id or not task or not task.strip():
            return {
                "success": False,
                "error": "persona_id and task are required",
                "response": None,
            }
        persona = self._personas.get(persona_id)
        if not persona:
            return {
                "success": False,
                "error": f"Unknown persona '{persona_id}'. Available: {list(self._personas.keys())}",
                "response": None,
                "personas": self.list_personas(),
            }
        default_timeout = (self.config.get("delegate") or {}).get("timeout_seconds", 3600)
        timeout = max(60, int(timeout_seconds or default_timeout))
        use_model = model or persona.model
        prompt = f"System: {persona.system_prompt}\n\nTask: {task.strip()}"
        cmd = ["bash", self._cursor_agent_path, "--force", "-p", prompt]
        if use_model:
            cmd = ["bash", self._cursor_agent_path, "--force", "--model", use_model, "-p", prompt]
        env = os.environ.copy()
        mcp_by_persona = (self.config.get("delegate") or {}).get("mcp_config_by_persona") or {}
        mcp_config_path = mcp_by_persona.get(persona_id)
        if mcp_config_path:
            path = os.path.expanduser(str(mcp_config_path))
            if os.path.isfile(path):
                env["CURSOR_MCP_CONFIG_PATH"] = path
        if persona_id == "home_assistant":
            delegate_cfg = self.config.get("delegate") or {}
            mcp_path = mcp_config_path or self.config.get("mcp_config_path")
            ha_token = (
                delegate_cfg.get("home_assistant_token")
                or os.environ.get("HOME_ASSISTANT_TOKEN")
                or _ha_token_from_mcp_config(mcp_path)
            )
            if ha_token:
                env["HOME_ASSISTANT_TOKEN"] = ha_token
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=os.path.expanduser("~"),
                    env=env,
                ),
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": (
                    f"Sub-agent timed out after {timeout}s. No response was returned. "
                    "For long tasks (e.g. HA analysis) increase delegate.timeout_seconds in config or request a shorter task."
                ),
                "response": None,
                "persona_id": persona_id,
            }
        except FileNotFoundError:
            return {
                "success": False,
                "error": f"cursor-agent not found at {self._cursor_agent_path}",
                "response": None,
                "persona_id": persona_id,
            }
        except Exception as e:
            logger.exception("Delegate tool run failed")
            return {
                "success": False,
                "error": str(e),
                "response": None,
                "persona_id": persona_id,
            }
        response_text = (result.stdout or "").strip()
        if result.returncode != 0:
            return {
                "success": False,
                "error": result.stderr or f"Exit code {result.returncode}",
                "response": response_text or None,
                "persona_id": persona_id,
            }
        return {
            "success": True,
            "response": response_text,
            "persona_id": persona_id,
            "persona_name": persona.name,
        }
