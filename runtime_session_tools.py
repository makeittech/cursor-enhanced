"""
Session Tools - Ported from Runtime agents/tools/sessions-*.ts

Provides session management tools for agent-to-agent communication.
"""

import uuid
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger("cursor_enhanced.runtime_sessions")

class SessionsListTool:
    """Sessions list tool (ported from sessions-list-tool.ts)"""
    
    def __init__(self, gateway_client=None):
        self.gateway_client = gateway_client
    
    async def execute(self, kinds: Optional[List[str]] = None, limit: Optional[int] = None,
                     active_minutes: Optional[int] = None, message_limit: Optional[int] = None) -> Dict[str, Any]:
        """List sessions"""
        if not self.gateway_client:
            raise RuntimeError("Gateway client required for sessions_list tool")
        
        params = {}
        if kinds:
            params["kinds"] = kinds
        if limit:
            params["limit"] = limit
        if active_minutes:
            params["activeMinutes"] = active_minutes
        if message_limit is not None:
            params["messageLimit"] = message_limit
        
        return await self.gateway_client.call("sessions.list", params)

class SessionsSendTool:
    """Sessions send tool (ported from sessions-send-tool.ts)"""
    
    def __init__(self, gateway_client=None, agent_session_key: Optional[str] = None):
        self.gateway_client = gateway_client
        self.agent_session_key = agent_session_key
    
    async def execute(self, message: str, session_key: Optional[str] = None,
                     label: Optional[str] = None, agent_id: Optional[str] = None,
                     timeout_seconds: Optional[int] = None) -> Dict[str, Any]:
        """Send message to another session"""
        if not self.gateway_client:
            raise RuntimeError("Gateway client required for sessions_send tool")
        
        if not message:
            raise ValueError("message parameter required")
        
        if session_key and label:
            return {
                "runId": str(uuid.uuid4()),
                "status": "error",
                "error": "Provide either sessionKey or label (not both)."
            }
        
        params = {
            "message": message,
            "idempotencyKey": str(uuid.uuid4())
        }
        
        if session_key:
            params["sessionKey"] = session_key
        if label:
            params["label"] = label
        if agent_id:
            params["agentId"] = agent_id
        if timeout_seconds:
            params["timeoutSeconds"] = timeout_seconds
        
        return await self.gateway_client.call("sessions.send", params)

class SessionsHistoryTool:
    """Sessions history tool (ported from sessions-history-tool.ts)"""
    
    def __init__(self, gateway_client=None):
        self.gateway_client = gateway_client
    
    async def execute(self, session_key: str, limit: Optional[int] = None,
                     from_seq: Optional[int] = None) -> Dict[str, Any]:
        """Get session history"""
        if not self.gateway_client:
            raise RuntimeError("Gateway client required for sessions_history tool")
        
        if not session_key:
            raise ValueError("sessionKey parameter required")
        
        params = {"sessionKey": session_key}
        if limit:
            params["limit"] = limit
        if from_seq is not None:
            params["fromSeq"] = from_seq
        
        return await self.gateway_client.call("chat.history", params)

class MessageTool:
    """Message tool (ported from message-tool.ts)"""
    
    def __init__(self, gateway_client=None):
        self.gateway_client = gateway_client
    
    async def execute(self, message: Optional[str] = None, channel: Optional[str] = None,
                     target: Optional[str] = None, targets: Optional[List[str]] = None,
                     account_id: Optional[str] = None, media: Optional[str] = None,
                     path: Optional[str] = None, reply_to: Optional[str] = None,
                     dry_run: Optional[bool] = None) -> Dict[str, Any]:
        """Send message to channel"""
        if not self.gateway_client:
            raise RuntimeError("Gateway client required for message tool")
        
        if not message and not media and not path:
            raise ValueError("message, media, or path parameter required")
        
        params = {
            "idempotencyKey": str(uuid.uuid4())
        }
        
        if message:
            params["message"] = message
        if channel:
            params["channel"] = channel
        if target:
            params["target"] = target
        if targets:
            params["targets"] = targets
        if account_id:
            params["accountId"] = account_id
        if media:
            params["mediaUrl"] = media
        if path:
            params["path"] = path
        if reply_to:
            params["replyTo"] = reply_to
        if dry_run is not None:
            params["dryRun"] = dry_run
        
        return await self.gateway_client.call("message.send", params)

class AgentsListTool:
    """Agents list tool (ported from agents-list-tool.ts)"""
    
    def __init__(self, gateway_client=None, config: Optional[Dict[str, Any]] = None):
        self.gateway_client = gateway_client
        self.config = config or {}
    
    async def execute(self) -> Dict[str, Any]:
        """List available agents"""
        # Simplified implementation - full version would check allowlists
        agents = []
        agent_list = self.config.get("agents", {}).get("list", [])
        
        for agent in agent_list:
            if isinstance(agent, dict):
                agents.append({
                    "id": agent.get("id", "unknown"),
                    "name": agent.get("name"),
                    "configured": True
                })
        
        # Always include main agent
        agents.insert(0, {
            "id": "main",
            "name": "Main Agent",
            "configured": True
        })
        
        return {
            "requester": "main",
            "allowAny": True,
            "agents": agents
        }

class SessionStatusTool:
    """Session status tool (ported from session-status-tool.ts)"""
    
    def __init__(self, gateway_client=None):
        self.gateway_client = gateway_client
    
    async def execute(self, session_key: Optional[str] = None, model: Optional[str] = None) -> Dict[str, Any]:
        """Get session status"""
        if not self.gateway_client:
            raise RuntimeError("Gateway client required for session_status tool")
        
        params = {}
        if session_key:
            params["sessionKey"] = session_key
        if model:
            params["model"] = model
        
        # Try to get session info from gateway
        try:
            if session_key:
                session_info = await self.gateway_client.call("sessions.get", {"sessionKey": session_key})
                return {
                    "sessionKey": session_key,
                    "status": "active" if session_info else "not_found",
                    "info": session_info
                }
            else:
                return {"status": "error", "error": "sessionKey parameter required"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

class SessionsSpawnTool:
    """Sessions spawn tool (ported from sessions-spawn-tool.ts)"""
    
    def __init__(self, gateway_client=None, agent_session_key: Optional[str] = None):
        self.gateway_client = gateway_client
        self.agent_session_key = agent_session_key
    
    async def execute(self, task: str, label: Optional[str] = None, agent_id: Optional[str] = None,
                     model: Optional[str] = None, thinking: Optional[str] = None,
                     run_timeout_seconds: Optional[int] = None, timeout_seconds: Optional[int] = None,
                     cleanup: Optional[str] = None) -> Dict[str, Any]:
        """Spawn a background sub-agent session"""
        if not self.gateway_client:
            raise RuntimeError("Gateway client required for sessions_spawn tool")
        
        if not task:
            raise ValueError("task parameter required")
        
        params = {
            "task": task,
            "idempotencyKey": str(uuid.uuid4())
        }
        
        if label:
            params["label"] = label
        if agent_id:
            params["agentId"] = agent_id
        if model:
            params["model"] = model
        if thinking:
            params["thinking"] = thinking
        if run_timeout_seconds:
            params["runTimeoutSeconds"] = run_timeout_seconds
        elif timeout_seconds:
            params["timeoutSeconds"] = timeout_seconds
        if cleanup:
            params["cleanup"] = cleanup
        
        return await self.gateway_client.call("sessions.spawn", params)
