"""
Delegate Tool - Spawn agents with predefined personalities and roles

Lets the main agent delegate tasks to sub-agents with fixed personas (system prompts).
Runs cursor-agent in a subprocess with the persona's system prompt and the task;
returns the sub-agent's response. No gateway required.
"""

import os
import subprocess
import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger("cursor_enhanced.openclaw_delegate")

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
        timeout = max(60, int(timeout_seconds or 120))
        use_model = model or persona.model
        prompt = f"System: {persona.system_prompt}\n\nTask: {task.strip()}"
        cmd = ["bash", self._cursor_agent_path, "--force", "-p", prompt]
        if use_model:
            cmd = ["bash", self._cursor_agent_path, "--force", "--model", use_model, "-p", prompt]
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=os.path.expanduser("~"),
                ),
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"Sub-agent timed out after {timeout}s",
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
