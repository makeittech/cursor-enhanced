"""
Runtime Integration Module for Cursor Enhanced

This module integrates Runtime's architecture and features into cursor-enhanced,
providing:
- MCP tools connection
- Tool system (browser, canvas, nodes, skills)
- Session management
- Multi-agent routing
- Presence and typing indicators
- Usage tracking
- Workspace and skills platform
"""

import os
import json
import subprocess
import asyncio
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, asdict
from enum import Enum
import logging

# Import Runtime core features
try:
    from runtime_core import (
        SessionStore, SessionEntry,
        SkillsManager, SkillSnapshot,
        GatewayClient,
        ToolRegistry, BrowserTool, CanvasTool, NodesTool, CronTool, MemoryTool,
        ThinkLevel, VerboseLevel, normalize_think_level, normalize_verbose_level,
        format_token_count, format_usd, estimate_usage_cost, ModelCostConfig, UsageTotals
    )
    RUNTIME_CORE_AVAILABLE = True
except ImportError as e:
    RUNTIME_CORE_AVAILABLE = False
    logger = logging.getLogger("cursor_enhanced.runtime")
    logger.warning(f"Runtime core not available: {e}")

# Optional websockets import
try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

logger = logging.getLogger("cursor_enhanced.runtime")

# MCP Tools Integration
class MCPTool:
    """Represents an MCP tool that can be called"""
    def __init__(self, name: str, description: str, parameters: Dict[str, Any]):
        self.name = name
        self.description = description
        self.parameters = parameters
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters
        }

class ToolRegistry:
    """Registry for managing available tools (inspired by Runtime's tool system)"""
    
    def __init__(self):
        self.tools: Dict[str, MCPTool] = {}
        self.tool_handlers: Dict[str, Callable] = {}
    
    def register_tool(self, tool: MCPTool, handler: Optional[Callable] = None):
        """Register a tool with optional handler"""
        self.tools[tool.name] = tool
        if handler:
            self.tool_handlers[tool.name] = handler
        logger.info(f"Registered tool: {tool.name}")
    
    def get_tool(self, name: str) -> Optional[MCPTool]:
        """Get a tool by name"""
        return self.tools.get(name)
    
    def list_tools(self) -> List[Dict[str, Any]]:
        """List all registered tools"""
        return [tool.to_dict() for tool in self.tools.values()]
    
    async def execute_tool(self, name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool by name with parameters"""
        if name not in self.tools:
            raise ValueError(f"Tool '{name}' not found")
        
        if name in self.tool_handlers:
            handler = self.tool_handlers[name]
            if asyncio.iscoroutinefunction(handler):
                result = await handler(params)
            else:
                result = handler(params)
            return {"success": True, "result": result}
        else:
            # Default: try to call via MCP
            return await self._call_mcp_tool(name, params)
    
    async def _call_mcp_tool(self, name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Call an MCP tool via cursor-agent's MCP connection"""
        # This will be implemented to interface with Cursor's MCP tools
        # For now, return a placeholder
        return {"success": False, "error": "MCP tool execution not yet implemented"}

# Session Management (Runtime-style)
@dataclass
class SessionEntry:
    """Represents a session entry (inspired by Runtime's session model)"""
    session_id: str
    session_key: str
    updated_at: Optional[int] = None
    agent_id: Optional[str] = None
    channel: Optional[str] = None
    to: Optional[str] = None
    account_id: Optional[str] = None
    thinking_level: Optional[str] = None
    verbose_level: Optional[str] = None
    model: Optional[str] = None
    send_policy: Optional[str] = None
    group_activation: Optional[str] = None
    skills_snapshot: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

# Use Runtime core SessionStore if available, otherwise fallback
if RUNTIME_CORE_AVAILABLE:
    # Use the core SessionStore directly
    pass
else:
    # Fallback implementation
    class SessionStore:
        """Manages session storage (inspired by Runtime's session store)"""
        
        def __init__(self, store_path: Optional[str] = None):
            if store_path is None:
                store_path = os.path.expanduser("~/.cursor-enhanced/sessions.json")
            self.store_path = store_path
            self.sessions: Dict[str, SessionEntry] = {}
            self._ensure_store_dir()
            self.load()
        
        def _ensure_store_dir(self):
            """Ensure the store directory exists"""
            os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        
        def load(self):
            """Load sessions from disk"""
            if os.path.exists(self.store_path):
                try:
                    with open(self.store_path, 'r') as f:
                        data = json.load(f)
                        self.sessions = {
                            k: SessionEntry(**v) for k, v in data.items()
                        }
                    logger.info(f"Loaded {len(self.sessions)} sessions")
                except Exception as e:
                    logger.error(f"Failed to load sessions: {e}")
                    self.sessions = {}
            else:
                self.sessions = {}
        
        def save(self):
            """Save sessions to disk"""
            try:
                data = {
                    k: v.to_dict() for k, v in self.sessions.items()
                }
                with open(self.store_path, 'w') as f:
                    json.dump(data, f, indent=2)
                logger.debug(f"Saved {len(self.sessions)} sessions")
            except Exception as e:
                logger.error(f"Failed to save sessions: {e}")
        
        def get(self, session_key: str) -> Optional[SessionEntry]:
            """Get a session by key"""
            return self.sessions.get(session_key)
        
        def set(self, session_key: str, entry: SessionEntry):
            """Set a session entry"""
            self.sessions[session_key] = entry
            self.save()
        
        def delete(self, session_key: str):
            """Delete a session"""
            if session_key in self.sessions:
                del self.sessions[session_key]
                self.save()

# Gateway Client - use from runtime_core if available
if RUNTIME_CORE_AVAILABLE:
    # Use the core GatewayClient
    pass
else:
    # Fallback GatewayClient implementation
    class GatewayClient:
        """Client for communicating with a Gateway WebSocket server (Runtime-style)"""
        
        def __init__(self, gateway_url: str = "ws://127.0.0.1:18789", gateway_token: Optional[str] = None):
            self.gateway_url = gateway_url
            self.gateway_token = gateway_token
            self.websocket = None
            self.connected = False
        
        async def connect(self):
            """Connect to the gateway"""
            if not WEBSOCKETS_AVAILABLE:
                logger.error("websockets library not available. Install with: pip install websockets")
                self.connected = False
                return
            
            try:
                headers = {}
                if self.gateway_token:
                    headers["Authorization"] = f"Bearer {self.gateway_token}"
                
                self.websocket = await websockets.connect(self.gateway_url, extra_headers=headers)
                self.connected = True
                logger.info(f"Connected to gateway at {self.gateway_url}")
            except Exception as e:
                logger.error(f"Failed to connect to gateway: {e}")
                self.connected = False
        
        async def disconnect(self):
            """Disconnect from the gateway"""
            if self.websocket:
                await self.websocket.close()
                self.connected = False
        
        async def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            """Call a gateway method"""
            if not self.connected:
                await self.connect()
            
            if not self.connected:
                return {"error": "Not connected to gateway"}
            
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params or {}
            }
            
            try:
                await self.websocket.send(json.dumps(request))
                response = await self.websocket.recv()
                return json.loads(response)
            except Exception as e:
                logger.error(f"Gateway call failed: {e}")
                return {"error": str(e)}

# Skills Platform (Runtime-style)
    # Use Runtime core SkillsManager if available, otherwise fallback
if RUNTIME_CORE_AVAILABLE:
    # Use the core SkillsManager directly - it has list_skills() method
    pass
else:
    # Fallback implementation
    class SkillsManager:
        """Manages skills (inspired by Runtime's skills platform)"""
        
        def __init__(self, workspace_dir: Optional[str] = None):
            if workspace_dir is None:
                workspace_dir = os.path.expanduser("~/.cursor-enhanced/workspace")
            self.workspace_dir = workspace_dir
            self.skills_dir = os.path.join(workspace_dir, "skills")
            self._ensure_dirs()
        
        def _ensure_dirs(self):
            """Ensure workspace directories exist"""
            os.makedirs(self.workspace_dir, exist_ok=True)
            os.makedirs(self.skills_dir, exist_ok=True)
        
        def list_skills(self) -> List[str]:
            """List available skills"""
            # Fallback: simple directory listing
            skills = []
            
            # Check workspace skills
            if os.path.exists(self.skills_dir):
                for item in os.listdir(self.skills_dir):
                    skill_path = os.path.join(self.skills_dir, item)
                    if os.path.isdir(skill_path):
                        skill_md = os.path.join(skill_path, "SKILL.md")
                        if os.path.exists(skill_md):
                            skills.append(item)
            
            return skills
        
        def get_skill_info(self, skill_name: str) -> Optional[Dict[str, Any]]:
            """Get information about a skill"""
            skill_path = os.path.join(self.skills_dir, skill_name)
            skill_md = os.path.join(skill_path, "SKILL.md")
            
            if not os.path.exists(skill_md):
                return None
            
            try:
                with open(skill_md, 'r') as f:
                    content = f.read()
                    # Parse basic info from SKILL.md
                    return {
                        "name": skill_name,
                        "path": skill_path,
                        "description": content[:200] if content else ""
                    }
            except Exception as e:
                logger.error(f"Failed to read skill info: {e}")
                return None

# Presence and Usage Tracking (Runtime-style)
@dataclass
class UsageStats:
    """Usage statistics for a session"""
    tokens_used: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    cost: Optional[float] = None
    model: Optional[str] = None
    timestamp: Optional[float] = None

class PresenceManager:
    """Manages presence and typing indicators (Runtime-style)"""
    
    def __init__(self):
        self.active_sessions: Dict[str, bool] = {}
        self.typing_sessions: Dict[str, bool] = {}
    
    def set_presence(self, session_key: str, active: bool):
        """Set presence for a session"""
        self.active_sessions[session_key] = active
    
    def set_typing(self, session_key: str, typing: bool):
        """Set typing indicator for a session"""
        self.typing_sessions[session_key] = typing
    
    def is_typing(self, session_key: str) -> bool:
        """Check if a session is typing"""
        return self.typing_sessions.get(session_key, False)

def _load_integration_config() -> Dict[str, Any]:
    """Load config from ~/.cursor-enhanced-config.json for tool registry (e.g. delegate personas)."""
    config_path = os.path.expanduser("~/.cursor-enhanced-config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}


# Main Runtime Integration Class
class RuntimeIntegration:
    """Main integration class that brings Runtime features to cursor-enhanced"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config if config is not None else _load_integration_config()
        if RUNTIME_CORE_AVAILABLE:
            # Use core implementations
            from runtime_core import ToolRegistry
            self.session_store = SessionStore()
            self.skills_manager = SkillsManager()
            self.gateway_client: Optional[GatewayClient] = None
            # Initialize tool registry with config (e.g. agent_personas, cursor_agent_path for delegate)
            self.tool_registry = ToolRegistry(gateway_client=None, config=self.config)
        else:
            # Fallback to basic implementations
            self.tool_registry = ToolRegistry()
            self.session_store = SessionStore()
            self.skills_manager = SkillsManager()
            self.gateway_client: Optional[GatewayClient] = None
        
        self.presence_manager = PresenceManager()
        
        if not RUNTIME_CORE_AVAILABLE:
            self._register_default_tools()
    
    def _register_default_tools(self):
        """Register default tools (browser, canvas, nodes, etc.)"""
        # Browser tool
        browser_tool = MCPTool(
            name="browser",
            description="Control a browser instance for web automation and scraping",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["navigate", "click", "type", "screenshot", "evaluate"]},
                    "url": {"type": "string"},
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                    "script": {"type": "string"}
                }
            }
        )
        self.tool_registry.register_tool(browser_tool)
        
        # Canvas tool
        canvas_tool = MCPTool(
            name="canvas",
            description="Control a visual canvas workspace (A2UI)",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["push", "reset", "snapshot", "eval"]},
                    "content": {"type": "string"},
                    "format": {"type": "string"}
                }
            }
        )
        self.tool_registry.register_tool(canvas_tool)
        
        # Node tool
        node_tool = MCPTool(
            name="node",
            description="Control device nodes (camera, screen, location, notifications)",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "invoke", "describe"]},
                    "node_id": {"type": "string"},
                    "method": {"type": "string"},
                    "params": {"type": "object"}
                }
            }
        )
        self.tool_registry.register_tool(node_tool)
        
        # Gateway tool
        gateway_tool = MCPTool(
            name="gateway",
            description="Control the gateway (restart, config, update)",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["restart", "config.get", "config.apply", "config.patch"]},
                    "config": {"type": "object"},
                    "delay_ms": {"type": "number"}
                }
            }
        )
        self.tool_registry.register_tool(gateway_tool)
    
    async def connect_gateway(self, gateway_url: str = "ws://127.0.0.1:18789", token: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        """Connect to a gateway WebSocket server"""
        if RUNTIME_CORE_AVAILABLE:
            from runtime_core import GatewayClient, ToolRegistry
            self.gateway_client = GatewayClient(gateway_url, token=token)
            await self.gateway_client.connect()
            # Re-initialize tool registry with gateway client and config
            # This will add gateway-dependent tools (browser, canvas, nodes, cron, sessions)
            self.tool_registry = ToolRegistry(self.gateway_client, config=config or {})
        else:
            self.gateway_client = GatewayClient(gateway_url, token)
            await self.gateway_client.connect()
    
    def get_session(self, session_key: str) -> Optional[SessionEntry]:
        """Get a session by key"""
        return self.session_store.get(session_key)
    
    def create_session(self, session_id: str, agent_id: Optional[str] = None, 
                      channel: Optional[str] = None, to: Optional[str] = None) -> SessionEntry:
        """Create a new session"""
        import time
        session_key = f"{agent_id or 'main'}:{session_id}"
        entry = SessionEntry(
            session_id=session_id,
            session_key=session_key,
            agent_id=agent_id,
            channel=channel,
            to=to,
            updated_at=int(time.time() * 1000)
        )
        self.session_store.set(session_key, entry)
        return entry
    
    async def execute_tool(self, tool_name: str, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool"""
        if RUNTIME_CORE_AVAILABLE and self.tool_registry:
            return await self.tool_registry.execute(tool_name, action, params)
        elif not RUNTIME_CORE_AVAILABLE:
            return await self.tool_registry.execute_tool(tool_name, params)
        else:
            raise RuntimeError("Tool registry not initialized")
    
    def list_tools(self) -> List[Dict[str, Any]]:
        """List all available tools"""
        if RUNTIME_CORE_AVAILABLE and self.tool_registry:
            # Return tool information from registry
            tools = []
            for name, tool in self.tool_registry.tools.items():
                tool_info = {"name": name}
                if hasattr(tool, '__class__'):
                    tool_info["type"] = tool.__class__.__name__
                if hasattr(tool, 'description'):
                    tool_info["description"] = tool.description
                elif hasattr(tool, '__doc__') and tool.__doc__:
                    tool_info["description"] = tool.__doc__.strip().split('\n')[0]
                tools.append(tool_info)
            return tools
        elif not RUNTIME_CORE_AVAILABLE:
            return self.tool_registry.list_tools()
        else:
            return []
    
    def list_skills(self) -> List[str]:
        """List available skills"""
        return self.skills_manager.list_skills()

# Global instance
_runtime_integration: Optional[RuntimeIntegration] = None

def get_runtime_integration(config: Optional[Dict[str, Any]] = None) -> RuntimeIntegration:
    """Get or create the global Runtime integration instance. Optionally pass config (e.g. from load_config()); if omitted, config is loaded from ~/.cursor-enhanced-config.json."""
    global _runtime_integration
    if _runtime_integration is None:
        _runtime_integration = RuntimeIntegration(config=config)
    return _runtime_integration
