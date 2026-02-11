"""
Cursor Cloud Agent Tool - Manage Cursor Cloud Agents via API.

Launch agents on repositories, get status, add follow-ups, merge results, delete agents.
Uses the Cursor Cloud Agents API: https://api.cursor.com/v0/

Requires a Cursor API key (from https://cursor.com/settings).
"""

import asyncio
import json
import os
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger("cursor_enhanced.cursor_agent")

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

API_BASE = "https://api.cursor.com/v0"
TIMEOUT_SECONDS = 60  # Agents can take a while


class CursorAgentTool:
    """Cursor Cloud Agent tool — launch and manage cloud agents on your repos.

    Actions:
      - launch: Start a new cloud agent on a repository
      - status: Get agent status by ID
      - list: List all agents
      - conversation: Get agent conversation history
      - followup: Add follow-up instruction to an agent
      - stop: Stop a running agent
      - delete: Delete an agent
      - models: List available models
      - repos: List accessible GitHub repositories
      - me: Get API key info
    """

    DEFAULT_MODEL = "default"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        agent_cfg = self.config.get("tools", {}).get("cursor_agent", {})
        self.api_key = agent_cfg.get("api_key") or os.environ.get("CURSOR_API_KEY", "")
        self.enabled = agent_cfg.get("enabled", True)
        self.default_model = agent_cfg.get("default_model", self.DEFAULT_MODEL)

    def _headers(self) -> Dict[str, str]:
        return {"Content-Type": "application/json"}

    def _auth(self) -> Optional[httpx.BasicAuth]:
        """Basic auth: API key as username, empty password."""
        if not self.api_key:
            return None
        return httpx.BasicAuth(username=self.api_key, password="")

    def _check(self) -> Optional[Dict[str, Any]]:
        """Pre-flight checks. Returns error dict or None."""
        if not self.enabled:
            return {"error": "Cursor Agent tool is disabled"}
        if not HTTPX_AVAILABLE:
            return {"error": "httpx library required. Install with: pip install httpx"}
        if not self.api_key:
            return {"error": "Cursor API key not configured. Set cursor_agent.api_key in config or CURSOR_API_KEY env var."}
        return None

    # ------------------------------------------------------------------
    # Core HTTP helpers
    # ------------------------------------------------------------------
    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        check = self._check()
        if check:
            return check
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                resp = await client.get(
                    f"{API_BASE}{path}",
                    headers=self._headers(),
                    auth=self._auth(),
                    params=params,
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else ""
            logger.error("Cursor API GET %s failed: %s %s", path, e, body)
            return {"error": f"HTTP {e.response.status_code}: {body}"}
        except Exception as e:
            logger.error("Cursor API GET %s failed: %s", path, e)
            return {"error": str(e)}

    async def _post(self, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        check = self._check()
        if check:
            return check
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    f"{API_BASE}{path}",
                    headers=self._headers(),
                    auth=self._auth(),
                    json=body or {},
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            body_text = e.response.text[:500] if e.response else ""
            logger.error("Cursor API POST %s failed: %s %s", path, e, body_text)
            return {"error": f"HTTP {e.response.status_code}: {body_text}"}
        except Exception as e:
            logger.error("Cursor API POST %s failed: %s", path, e)
            return {"error": str(e)}

    async def _delete_req(self, path: str) -> Dict[str, Any]:
        check = self._check()
        if check:
            return check
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                resp = await client.delete(
                    f"{API_BASE}{path}",
                    headers=self._headers(),
                    auth=self._auth(),
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            body_text = e.response.text[:500] if e.response else ""
            logger.error("Cursor API DELETE %s failed: %s %s", path, e, body_text)
            return {"error": f"HTTP {e.response.status_code}: {body_text}"}
        except Exception as e:
            logger.error("Cursor API DELETE %s failed: %s", path, e)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(self, action: str, **kwargs) -> Dict[str, Any]:
        """Route to the appropriate action handler.

        Parameters
        ----------
        action : str
            One of: launch, status, list, conversation, followup, stop, delete,
            models, repos, me.
        **kwargs : additional parameters per action.
        """
        action = (action or "").strip().lower()
        dispatch = {
            "launch": self.launch,
            "create": self.launch,
            "status": self.status,
            "get": self.status,
            "list": self.list_agents,
            "conversation": self.conversation,
            "followup": self.followup,
            "follow_up": self.followup,
            "stop": self.stop,
            "delete": self.delete,
            "models": self.list_models,
            "repos": self.list_repos,
            "repositories": self.list_repos,
            "me": self.me,
            "info": self.me,
        }
        handler = dispatch.get(action)
        if not handler:
            return {"error": f"Unknown action '{action}'. Available: {', '.join(sorted(set(str(v.__name__) for v in dispatch.values())))}"}
        return await handler(**kwargs)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def launch(
        self,
        prompt: Optional[str] = None,
        repository: Optional[str] = None,
        ref: Optional[str] = None,
        pr_url: Optional[str] = None,
        model: Optional[str] = None,
        auto_create_pr: bool = False,
        branch_name: Optional[str] = None,
        open_as_cursor_app: bool = False,
        skip_reviewer_request: bool = False,
        user_confirmed_model: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Launch a new cloud agent.

        Parameters
        ----------
        prompt : str (required)
            The task instruction for the agent.
        repository : str
            GitHub repo URL (e.g. https://github.com/org/repo). Required unless pr_url is set.
        ref : str, optional
            Git ref (branch/tag/commit) to base from.
        pr_url : str, optional
            GitHub PR URL. If set, repository and ref are ignored.
        model : str, optional
            LLM model (e.g. claude-4-sonnet). Ignored unless user_confirmed_model=True.
            Defaults to "default" — Cursor picks the best model automatically.
        auto_create_pr : bool
            Auto-create PR when agent finishes. Default False.
        branch_name : str, optional
            Custom branch name.
        open_as_cursor_app : bool
            Open PR as Cursor GitHub App. Default False.
        skip_reviewer_request : bool
            Skip adding user as reviewer. Default False.
        user_confirmed_model : bool
            Must be True to use a non-default model. This flag prevents the AI
            from silently upgrading to expensive models without user permission.
        """
        if not prompt:
            return {"error": "prompt is required"}
        if not repository and not pr_url:
            return {"error": "Either repository or pr_url is required"}

        body: Dict[str, Any] = {
            "prompt": {"text": prompt},
        }

        source: Dict[str, Any] = {}
        if pr_url:
            source["prUrl"] = pr_url
        else:
            source["repository"] = repository
            if ref:
                source["ref"] = ref
        body["source"] = source

        # Model policy: ALWAYS use "default" unless the user explicitly confirmed
        # a specific model via user_confirmed_model=True.
        if model and model != "default" and not user_confirmed_model:
            logger.warning(
                "Non-default model '%s' requested for cursor agent WITHOUT "
                "user_confirmed_model=True. Forcing 'default'. "
                "To use a specific model, the user must explicitly request it.",
                model,
            )
            effective_model = "default"
        else:
            effective_model = model if model is not None else self.default_model
            # Even from config, if someone set a non-default model but didn't confirm, warn
            if effective_model and effective_model != "default" and not user_confirmed_model:
                logger.warning(
                    "Config default_model='%s' used without user confirmation. "
                    "Falling back to 'default'.",
                    effective_model,
                )
                effective_model = "default"

        if effective_model:
            body["model"] = effective_model

        target: Dict[str, Any] = {}
        if auto_create_pr:
            target["autoCreatePr"] = True
        if branch_name:
            target["branchName"] = branch_name
        if open_as_cursor_app:
            target["openAsCursorGithubApp"] = True
        if skip_reviewer_request:
            target["skipReviewerRequest"] = True
        if target:
            body["target"] = target

        result = await self._post("/agents", body)
        if "error" not in result:
            result["_summary"] = (
                f"Agent '{result.get('name', 'unnamed')}' launched (id={result.get('id')}). "
                f"Status: {result.get('status')}. "
                f"URL: {result.get('target', {}).get('url', 'N/A')}"
            )
        return result

    async def status(self, agent_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Get agent status by ID."""
        if not agent_id:
            return {"error": "agent_id is required"}
        result = await self._get(f"/agents/{agent_id}")
        if "error" not in result:
            result["_summary"] = (
                f"Agent '{result.get('name', '?')}': {result.get('status', '?')}. "
                f"Summary: {result.get('summary', 'N/A')}"
            )
        return result

    async def list_agents(
        self,
        limit: int = 20,
        cursor: Optional[str] = None,
        pr_url: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """List all cloud agents."""
        params: Dict[str, Any] = {"limit": min(limit, 100)}
        if cursor:
            params["cursor"] = cursor
        if pr_url:
            params["prUrl"] = pr_url
        result = await self._get("/agents", params=params)
        if "error" not in result:
            agents = result.get("agents", [])
            lines = []
            for a in agents:
                lines.append(
                    f"  {a.get('id', '?')} | {a.get('status', '?'):10s} | {a.get('name', 'unnamed')}"
                )
            result["_summary"] = f"{len(agents)} agent(s):\n" + "\n".join(lines) if lines else "No agents found."
        return result

    async def conversation(self, agent_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Get agent conversation history."""
        if not agent_id:
            return {"error": "agent_id is required"}
        result = await self._get(f"/agents/{agent_id}/conversation")
        if "error" not in result:
            messages = result.get("messages", [])
            lines = []
            for m in messages:
                role = m.get("type", "unknown").replace("_message", "")
                text = (m.get("text") or "")[:200]
                lines.append(f"  [{role}] {text}")
            result["_summary"] = f"{len(messages)} message(s):\n" + "\n".join(lines)
        return result

    async def followup(
        self,
        agent_id: Optional[str] = None,
        prompt: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Add a follow-up instruction to an agent."""
        if not agent_id:
            return {"error": "agent_id is required"}
        if not prompt:
            return {"error": "prompt is required"}
        body = {"prompt": {"text": prompt}}
        result = await self._post(f"/agents/{agent_id}/followup", body)
        if "error" not in result:
            result["_summary"] = f"Follow-up sent to agent {agent_id}."
        return result

    async def stop(self, agent_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Stop a running agent."""
        if not agent_id:
            return {"error": "agent_id is required"}
        result = await self._post(f"/agents/{agent_id}/stop")
        if "error" not in result:
            result["_summary"] = f"Agent {agent_id} stopped."
        return result

    async def delete(self, agent_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Delete an agent permanently."""
        if not agent_id:
            return {"error": "agent_id is required"}
        result = await self._delete_req(f"/agents/{agent_id}")
        if "error" not in result:
            result["_summary"] = f"Agent {agent_id} deleted."
        return result

    async def list_models(self, **kwargs) -> Dict[str, Any]:
        """List recommended models for cloud agents."""
        result = await self._get("/models")
        if "error" not in result:
            models = result.get("models", [])
            result["_summary"] = f"Available models: {', '.join(models)}" if models else "No models returned."
        return result

    async def list_repos(self, **kwargs) -> Dict[str, Any]:
        """List accessible GitHub repositories (rate-limited: 1/min, 30/hr)."""
        result = await self._get("/repositories")
        if "error" not in result:
            repos = result.get("repositories", [])
            lines = [f"  {r.get('owner', '?')}/{r.get('name', '?')} — {r.get('repository', '')}" for r in repos[:20]]
            result["_summary"] = f"{len(repos)} repo(s):\n" + "\n".join(lines) if repos else "No repositories found."
        return result

    async def me(self, **kwargs) -> Dict[str, Any]:
        """Get API key info."""
        result = await self._get("/me")
        if "error" not in result:
            result["_summary"] = (
                f"Key: {result.get('apiKeyName', '?')}, "
                f"Email: {result.get('userEmail', '?')}, "
                f"Created: {result.get('createdAt', '?')}"
            )
        return result
