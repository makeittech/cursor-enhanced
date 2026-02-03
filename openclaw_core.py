"""
OpenClaw Core Features - Direct Ports from OpenClaw Repository

This module contains direct Python ports of OpenClaw's core features:
- Session store with locking and caching
- Skills system with workspace management
- Gateway protocol client
- Tool implementations (browser, canvas, nodes, web, cron, memory)
- Thinking levels and verbose modes
- Usage tracking and cost calculation
"""

import os
import json
import time
import uuid
import asyncio
import hashlib
import fcntl
import tempfile
from typing import Dict, List, Optional, Any, Callable, Union
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path
import logging

logger = logging.getLogger("cursor_enhanced.openclaw_core")

# ============================================================================
# Thinking Levels (from auto-reply/thinking.ts)
# ============================================================================

class ThinkLevel(str, Enum):
    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"

class VerboseLevel(str, Enum):
    OFF = "off"
    ON = "on"
    FULL = "full"

class UsageDisplayLevel(str, Enum):
    OFF = "off"
    TOKENS = "tokens"
    FULL = "full"

XHIGH_MODEL_REFS = [
    "openai/gpt-5.2",
    "openai-codex/gpt-5.2-codex",
    "openai-codex/gpt-5.1-codex",
]

def normalize_think_level(raw: Optional[str]) -> Optional[ThinkLevel]:
    """Normalize thinking level string to enum"""
    if not raw:
        return None
    key = raw.lower()
    mapping = {
        "off": ThinkLevel.OFF,
        "on": ThinkLevel.LOW,
        "enable": ThinkLevel.LOW,
        "enabled": ThinkLevel.LOW,
        "min": ThinkLevel.MINIMAL,
        "minimal": ThinkLevel.MINIMAL,
        "think": ThinkLevel.MINIMAL,
        "low": ThinkLevel.LOW,
        "thinkhard": ThinkLevel.LOW,
        "think-hard": ThinkLevel.LOW,
        "think_hard": ThinkLevel.LOW,
        "mid": ThinkLevel.MEDIUM,
        "med": ThinkLevel.MEDIUM,
        "medium": ThinkLevel.MEDIUM,
        "thinkharder": ThinkLevel.MEDIUM,
        "think-harder": ThinkLevel.MEDIUM,
        "harder": ThinkLevel.MEDIUM,
        "high": ThinkLevel.HIGH,
        "ultra": ThinkLevel.HIGH,
        "ultrathink": ThinkLevel.HIGH,
        "thinkhardest": ThinkLevel.HIGH,
        "highest": ThinkLevel.HIGH,
        "max": ThinkLevel.HIGH,
        "xhigh": ThinkLevel.XHIGH,
        "x-high": ThinkLevel.XHIGH,
        "x_high": ThinkLevel.XHIGH,
    }
    return mapping.get(key)

def supports_xhigh_thinking(provider: Optional[str], model: Optional[str]) -> bool:
    """Check if model supports xhigh thinking"""
    if not model:
        return False
    model_key = model.lower()
    if provider:
        provider_key = provider.lower()
        return f"{provider_key}/{model_key}" in [ref.lower() for ref in XHIGH_MODEL_REFS]
    return model_key in [ref.split("/")[1].lower() for ref in XHIGH_MODEL_REFS if "/" in ref]

def normalize_verbose_level(raw: Optional[str]) -> Optional[VerboseLevel]:
    """Normalize verbose level string to enum"""
    if not raw:
        return None
    key = raw.lower()
    if key in ["off", "false", "no", "0"]:
        return VerboseLevel.OFF
    if key in ["full", "all", "everything"]:
        return VerboseLevel.FULL
    if key in ["on", "minimal", "true", "yes", "1"]:
        return VerboseLevel.ON
    return None

# ============================================================================
# Usage Tracking (from utils/usage-format.ts)
# ============================================================================

@dataclass
class ModelCostConfig:
    """Model cost configuration"""
    input: float  # per million tokens
    output: float  # per million tokens
    cache_read: float = 0.0  # per million tokens
    cache_write: float = 0.0  # per million tokens

@dataclass
class UsageTotals:
    """Usage totals"""
    input: Optional[int] = None
    output: Optional[int] = None
    cache_read: Optional[int] = None
    cache_write: Optional[int] = None
    total: Optional[int] = None

def format_token_count(value: Optional[int]) -> str:
    """Format token count with K/M suffixes"""
    if value is None or not isinstance(value, (int, float)):
        return "0"
    safe = max(0, int(value))
    if safe >= 1_000_000:
        return f"{safe / 1_000_000:.1f}m"
    if safe >= 1_000:
        return f"{safe / 1_000:.1f}k" if safe < 10_000 else f"{safe // 1_000}k"
    return str(safe)

def format_usd(value: Optional[float]) -> Optional[str]:
    """Format USD value"""
    if value is None or not isinstance(value, (int, float)):
        return None
    if value >= 1:
        return f"${value:.2f}"
    if value >= 0.01:
        return f"${value:.2f}"
    return f"${value:.4f}"

def estimate_usage_cost(usage: Optional[UsageTotals], cost: Optional[ModelCostConfig]) -> Optional[float]:
    """Estimate usage cost in USD"""
    if not usage or not cost:
        return None
    
    input_tokens = usage.input or 0
    output_tokens = usage.output or 0
    cache_read_tokens = usage.cache_read or 0
    cache_write_tokens = usage.cache_write or 0
    
    total = (
        input_tokens * cost.input +
        output_tokens * cost.output +
        cache_read_tokens * cost.cache_read +
        cache_write_tokens * cost.cache_write
    ) / 1_000_000
    
    if not isinstance(total, (int, float)) or not (isinstance(total, float) and total.is_integer() or True):
        return None
    
    return total

# ============================================================================
# Session Store (from config/sessions/store.ts)
# ============================================================================

@dataclass
class SessionEntry:
    """Session entry (simplified from OpenClaw)"""
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
    session_file: Optional[str] = None
    delivery_context: Optional[Dict[str, Any]] = None
    last_channel: Optional[str] = None
    last_to: Optional[str] = None
    last_account_id: Optional[str] = None
    last_thread_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        result = {}
        for key, value in asdict(self).items():
            if value is not None:
                result[key] = value
        return result

class SessionStoreCache:
    """Session store cache with TTL"""
    
    def __init__(self, ttl_ms: int = 45_000):
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.loaded_at: Dict[str, float] = {}
        self.mtime_ms: Dict[str, Optional[float]] = {}
        self.ttl_ms = ttl_ms
    
    def is_valid(self, store_path: str) -> bool:
        """Check if cache entry is valid"""
        if store_path not in self.cache:
            return False
        now = time.time() * 1000
        return (now - self.loaded_at.get(store_path, 0)) <= self.ttl_ms
    
    def get(self, store_path: str) -> Optional[Dict[str, SessionEntry]]:
        """Get cached store"""
        if self.is_valid(store_path):
            return self.cache.get(store_path)
        return None
    
    def set(self, store_path: str, store: Dict[str, SessionEntry], mtime_ms: Optional[float] = None):
        """Set cache entry"""
        self.cache[store_path] = store
        self.loaded_at[store_path] = time.time() * 1000
        self.mtime_ms[store_path] = mtime_ms
    
    def invalidate(self, store_path: str):
        """Invalidate cache entry"""
        self.cache.pop(store_path, None)
        self.loaded_at.pop(store_path, None)
        self.mtime_ms.pop(store_path, None)

# Global cache instance
_session_store_cache = SessionStoreCache()

class SessionStore:
    """Session store with locking and caching (ported from OpenClaw)"""
    
    def __init__(self, store_path: Optional[str] = None):
        if store_path is None:
            store_path = os.path.expanduser("~/.cursor-enhanced/sessions.json")
        self.store_path = store_path
        self.lock_path = f"{store_path}.lock"
        self._ensure_dir()
    
    def _ensure_dir(self):
        """Ensure store directory exists"""
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
    
    def _get_mtime_ms(self) -> Optional[float]:
        """Get file modification time in milliseconds"""
        try:
            if os.path.exists(self.store_path):
                return os.path.getmtime(self.store_path) * 1000
        except:
            pass
        return None
    
    def load(self, skip_cache: bool = False) -> Dict[str, SessionEntry]:
        """Load session store from disk with caching"""
        # Check cache first
        if not skip_cache:
            cached = _session_store_cache.get(self.store_path)
            if cached:
                current_mtime = self._get_mtime_ms()
                cached_mtime = _session_store_cache.mtime_ms.get(self.store_path)
                if current_mtime == cached_mtime:
                    return cached.copy()
                _session_store_cache.invalidate(self.store_path)
        
        # Load from disk
        store: Dict[str, SessionEntry] = {}
        mtime_ms = self._get_mtime_ms()
        
        if os.path.exists(self.store_path):
            try:
                with open(self.store_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        for key, value in data.items():
                            if isinstance(value, dict):
                                store[key] = SessionEntry(**value)
            except Exception as e:
                logger.warning(f"Failed to load session store: {e}")
        
        # Cache the result
        if not skip_cache:
            _session_store_cache.set(self.store_path, store, mtime_ms)
        
        return store.copy()
    
    def save(self, store: Dict[str, SessionEntry]):
        """Save session store to disk with locking"""
        # Invalidate cache
        _session_store_cache.invalidate(self.store_path)
        
        # Save with atomic write
        self._ensure_dir()
        tmp_path = f"{self.store_path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        
        try:
            data = {k: v.to_dict() for k, v in store.items()}
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self.store_path)
            os.chmod(self.store_path, 0o600)
        except Exception as e:
            logger.error(f"Failed to save session store: {e}")
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except:
                    pass
            raise
    
    def with_lock(self, fn: Callable[[], Any], timeout_ms: int = 10_000, poll_interval_ms: int = 25) -> Any:
        """Execute function with file lock"""
        started_at = time.time() * 1000
        self._ensure_dir()
        
        while True:
            try:
                # Try to acquire lock
                lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    # Write PID to lock file
                    os.write(lock_fd, str(os.getpid()).encode())
                    os.fsync(lock_fd)
                    
                    # Execute function
                    result = fn()
                    
                    # Release lock
                    os.close(lock_fd)
                    try:
                        os.remove(self.lock_path)
                    except:
                        pass
                    
                    return result
                except:
                    os.close(lock_fd)
                    try:
                        os.remove(self.lock_path)
                    except:
                        pass
                    raise
            except FileExistsError:
                # Lock exists, wait and retry
                elapsed = time.time() * 1000 - started_at
                if elapsed >= timeout_ms:
                    raise TimeoutError(f"Session store lock timeout after {timeout_ms}ms")
                time.sleep(poll_interval_ms / 1000.0)
            except Exception as e:
                logger.error(f"Lock acquisition failed: {e}")
                raise
    
    def update(self, mutator: Callable[[Dict[str, SessionEntry]], Any]) -> Any:
        """Update store with lock"""
        def _update():
            store = self.load(skip_cache=True)
            result = mutator(store)
            self.save(store)
            return result
        return self.with_lock(_update)
    
    def get(self, session_key: str) -> Optional[SessionEntry]:
        """Get session entry"""
        store = self.load()
        return store.get(session_key)
    
    def set(self, session_key: str, entry: SessionEntry):
        """Set session entry"""
        def _set(store: Dict[str, SessionEntry]):
            store[session_key] = entry
        self.update(_set)
    
    def delete(self, session_key: str):
        """Delete session entry"""
        def _delete(store: Dict[str, SessionEntry]):
            store.pop(session_key, None)
        self.update(_delete)

# ============================================================================
# Skills System (from agents/skills/workspace.ts)
# ============================================================================

@dataclass
class SkillEntry:
    """Skill entry"""
    name: str
    file_path: str
    description: Optional[str] = None
    frontmatter: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    invocation: Optional[Dict[str, Any]] = None

@dataclass
class SkillSnapshot:
    """Skill snapshot"""
    prompt: str
    skills: List[Dict[str, Any]]
    resolved_skills: List[Any]
    version: Optional[int] = None

class SkillsManager:
    """Skills manager (ported from OpenClaw)"""
    
    def __init__(self, workspace_dir: Optional[str] = None):
        if workspace_dir is None:
            workspace_dir = os.path.expanduser("~/.cursor-enhanced/workspace")
        self.workspace_dir = workspace_dir
        self.skills_dir = os.path.join(workspace_dir, "skills")
        self.managed_skills_dir = os.path.expanduser("~/.cursor-enhanced/skills")
        self._ensure_dirs()
    
    def _ensure_dirs(self):
        """Ensure directories exist"""
        os.makedirs(self.workspace_dir, exist_ok=True)
        os.makedirs(self.skills_dir, exist_ok=True)
        os.makedirs(self.managed_skills_dir, exist_ok=True)
    
    def load_skill_entries(self, config: Optional[Dict[str, Any]] = None) -> List[SkillEntry]:
        """Load skill entries from all directories"""
        entries: List[SkillEntry] = []
        skill_map: Dict[str, SkillEntry] = {}
        
        # Load from bundled, managed, and workspace directories
        dirs_to_check = [
            (self.managed_skills_dir, "managed"),
            (self.skills_dir, "workspace"),
        ]
        
        for skill_dir, source in dirs_to_check:
            if not os.path.exists(skill_dir):
                continue
            
            for item in os.listdir(skill_dir):
                skill_path = os.path.join(skill_dir, item)
                if not os.path.isdir(skill_path):
                    continue
                
                skill_md = os.path.join(skill_path, "SKILL.md")
                if not os.path.exists(skill_md):
                    continue
                
                try:
                    with open(skill_md, 'r', encoding='utf-8') as f:
                        content = f.read()
                        # Parse frontmatter if present
                        frontmatter = {}
                        if content.startswith("---"):
                            parts = content.split("---", 2)
                            if len(parts) >= 3:
                                try:
                                    import yaml
                                    frontmatter = yaml.safe_load(parts[1]) or {}
                                except:
                                    pass
                        
                        entry = SkillEntry(
                            name=item,
                            file_path=skill_md,
                            description=content[:200] if content else None,
                            frontmatter=frontmatter,
                            metadata=frontmatter.get("openclaw", {}),
                            invocation=frontmatter.get("invocation", {})
                        )
                        
                        # Precedence: workspace > managed
                        if item not in skill_map or source == "workspace":
                            skill_map[item] = entry
                except Exception as e:
                    logger.warning(f"Failed to load skill {item}: {e}")
        
        return list(skill_map.values())
    
    def build_skill_snapshot(self, config: Optional[Dict[str, Any]] = None, 
                           skill_filter: Optional[List[str]] = None) -> SkillSnapshot:
        """Build skill snapshot"""
        entries = self.load_skill_entries(config)
        
        # Filter skills if filter provided
        if skill_filter:
            entries = [e for e in entries if e.name in skill_filter]
        
        # Filter by invocation policy
        prompt_entries = [
            e for e in entries 
            if e.invocation and e.invocation.get("disableModelInvocation") != True
        ]
        
        # Build prompt
        skill_descriptions = []
        for entry in prompt_entries:
            desc = entry.description or entry.name
            skill_descriptions.append(f"- {entry.name}: {desc[:100]}")
        
        prompt = "\n".join(skill_descriptions) if skill_descriptions else ""
        
        return SkillSnapshot(
            prompt=prompt,
            skills=[{"name": e.name} for e in entries],
            resolved_skills=[e.name for e in prompt_entries],
            version=int(time.time())
        )

# ============================================================================
# Gateway Client (from gateway/call.ts)
# ============================================================================

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

class GatewayClient:
    """Gateway WebSocket client (ported from OpenClaw)"""
    
    def __init__(self, url: str, token: Optional[str] = None, password: Optional[str] = None,
                 timeout_ms: int = 10_000):
        self.url = url
        self.token = token
        self.password = password
        self.timeout_ms = timeout_ms
        self.websocket = None
        self.connected = False
    
    async def connect(self):
        """Connect to gateway"""
        if not WEBSOCKETS_AVAILABLE:
            raise RuntimeError("websockets library required. Install with: pip install websockets")
        
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif self.password:
            # Password auth would be handled differently in real implementation
            pass
        
        try:
            self.websocket = await websockets.connect(
                self.url,
                extra_headers=headers,
                ping_interval=None
            )
            self.connected = True
            logger.info(f"Connected to gateway at {self.url}")
        except Exception as e:
            logger.error(f"Failed to connect to gateway: {e}")
            self.connected = False
            raise
    
    async def disconnect(self):
        """Disconnect from gateway"""
        if self.websocket:
            await self.websocket.close()
            self.connected = False
    
    async def call(self, method: str, params: Optional[Dict[str, Any]] = None,
                  expect_final: bool = True) -> Dict[str, Any]:
        """Call gateway method"""
        if not self.connected:
            await self.connect()
        
        if not self.connected:
            raise RuntimeError("Not connected to gateway")
        
        request_id = str(uuid.uuid4())
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {}
        }
        
        try:
            await asyncio.wait_for(
                self.websocket.send(json.dumps(request)),
                timeout=self.timeout_ms / 1000.0
            )
            
            response = await asyncio.wait_for(
                self.websocket.recv(),
                timeout=self.timeout_ms / 1000.0
            )
            
            result = json.loads(response)
            
            if "error" in result:
                raise RuntimeError(f"Gateway error: {result['error']}")
            
            return result.get("result", {})
        except asyncio.TimeoutError:
            raise TimeoutError(f"Gateway call timeout after {self.timeout_ms}ms")
        except Exception as e:
            logger.error(f"Gateway call failed: {e}")
            raise

# ============================================================================
# Tool Implementations (from agents/tools/*.ts)
# ============================================================================

class BrowserTool:
    """Browser tool (ported from browser-tool.ts)"""
    
    def __init__(self, gateway_client: Optional[GatewayClient] = None):
        self.gateway_client = gateway_client
    
    async def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute browser action"""
        if not self.gateway_client:
            raise RuntimeError("Gateway client required for browser tool")
        
        # Map actions to gateway methods
        action_map = {
            "status": "browser.status",
            "start": "browser.start",
            "stop": "browser.stop",
            "profiles": "browser.profiles",
            "tabs": "browser.tabs",
            "open": "browser.open",
            "snapshot": "browser.snapshot",
            "screenshot": "browser.screenshot",
            "act": "browser.act",
        }
        
        gateway_method = action_map.get(action)
        if not gateway_method:
            raise ValueError(f"Unknown browser action: {action}")
        
        return await self.gateway_client.call(gateway_method, params)

class CanvasTool:
    """Canvas tool (ported from canvas-tool.ts)"""
    
    def __init__(self, gateway_client: Optional[GatewayClient] = None):
        self.gateway_client = gateway_client
    
    async def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute canvas action"""
        if not self.gateway_client:
            raise RuntimeError("Gateway client required for canvas tool")
        
        node_id = params.get("node")
        if not node_id:
            raise ValueError("node parameter required")
        
        action_map = {
            "present": "canvas.present",
            "hide": "canvas.hide",
            "navigate": "canvas.navigate",
            "eval": "canvas.eval",
            "snapshot": "canvas.snapshot",
            "a2ui_push": "canvas.a2ui.pushJSONL",
            "a2ui_reset": "canvas.a2ui.reset",
        }
        
        gateway_command = action_map.get(action)
        if not gateway_command:
            raise ValueError(f"Unknown canvas action: {action}")
        
        invoke_params = {k: v for k, v in params.items() if k != "node"}
        
        return await self.gateway_client.call("node.invoke", {
            "nodeId": node_id,
            "command": gateway_command,
            "params": invoke_params,
            "idempotencyKey": str(uuid.uuid4())
        })

class NodesTool:
    """Nodes tool (ported from nodes-tool.ts)"""
    
    def __init__(self, gateway_client: Optional[GatewayClient] = None):
        self.gateway_client = gateway_client
    
    async def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute nodes action"""
        if not self.gateway_client:
            raise RuntimeError("Gateway client required for nodes tool")
        
        action_map = {
            "status": "node.list",
            "describe": "node.describe",
            "pending": "node.pair.list",
            "approve": "node.pair.approve",
            "reject": "node.pair.reject",
            "notify": "system.notify",
            "camera_snap": "camera.snap",
            "camera_clip": "camera.clip",
            "screen_record": "screen.record",
            "location_get": "location.get",
            "run": "system.run",
        }
        
        if action in ["status", "pending"]:
            return await self.gateway_client.call(action_map[action], {})
        
        node_id = params.get("node")
        if not node_id:
            raise ValueError("node parameter required for this action")
        
        if action == "describe":
            return await self.gateway_client.call(action_map[action], {"nodeId": node_id})
        
        # For invoke actions
        command = action_map.get(action)
        if not command:
            raise ValueError(f"Unknown nodes action: {action}")
        
        invoke_params = {k: v for k, v in params.items() if k not in ["node", "action"]}
        
        return await self.gateway_client.call("node.invoke", {
            "nodeId": node_id,
            "command": command,
            "params": invoke_params,
            "idempotencyKey": str(uuid.uuid4())
        })

class CronTool:
    """Cron tool (ported from cron-tool.ts)"""
    
    def __init__(self, gateway_client: Optional[GatewayClient] = None):
        self.gateway_client = gateway_client
    
    async def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute cron action"""
        if not self.gateway_client:
            raise RuntimeError("Gateway client required for cron tool")
        
        action_map = {
            "status": "cron.status",
            "list": "cron.list",
            "add": "cron.add",
            "update": "cron.update",
            "remove": "cron.remove",
            "run": "cron.run",
            "runs": "cron.runs",
            "wake": "cron.wake",
        }
        
        gateway_method = action_map.get(action)
        if not gateway_method:
            raise ValueError(f"Unknown cron action: {action}")
        
        return await self.gateway_client.call(gateway_method, params)

class MemoryTool:
    """Memory tool (ported from memory-tool.ts)"""
    
    def __init__(self, workspace_dir: Optional[str] = None):
        self.workspace_dir = workspace_dir or os.path.expanduser("~/.cursor-enhanced/workspace")
        self.memory_dir = os.path.join(self.workspace_dir, "memory")
        os.makedirs(self.memory_dir, exist_ok=True)
    
    async def search(self, query: str, max_results: Optional[int] = None,
                   min_score: Optional[float] = None) -> Dict[str, Any]:
        """Search memory"""
        # Simplified implementation - full version would use vector search
        results = []
        
        memory_file = os.path.join(self.workspace_dir, "MEMORY.md")
        if os.path.exists(memory_file):
            with open(memory_file, 'r', encoding='utf-8') as f:
                content = f.read()
                if query.lower() in content.lower():
                    results.append({
                        "path": "MEMORY.md",
                        "text": content[:500],
                        "score": 0.8
                    })
        
        # Search memory/*.md files
        if os.path.exists(self.memory_dir):
            for file in os.listdir(self.memory_dir):
                if file.endswith('.md'):
                    file_path = os.path.join(self.memory_dir, file)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            if query.lower() in content.lower():
                                results.append({
                                    "path": f"memory/{file}",
                                    "text": content[:500],
                                    "score": 0.7
                                })
                    except:
                        pass
        
        if max_results:
            results = results[:max_results]
        
        if min_score:
            results = [r for r in results if r.get("score", 0) >= min_score]
        
        return {"results": results}
    
    async def get(self, path: str, from_line: Optional[int] = None,
                lines: Optional[int] = None) -> Dict[str, Any]:
        """Get memory file content"""
        if path.startswith("memory/"):
            file_path = os.path.join(self.workspace_dir, path)
        else:
            file_path = os.path.join(self.workspace_dir, path)
        
        if not os.path.exists(file_path):
            return {"path": path, "text": "", "error": "File not found"}
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content_lines = f.readlines()
            
            if from_line is not None:
                start = max(0, from_line - 1)
                end = start + (lines or len(content_lines))
                content_lines = content_lines[start:end]
            
            text = "".join(content_lines)
            return {"path": path, "text": text}
        except Exception as e:
            return {"path": path, "text": "", "error": str(e)}

# ============================================================================
# Tool Registry
# ============================================================================

class ToolRegistry:
    """Registry for all tools"""
    
    def __init__(self, gateway_client: Optional[GatewayClient] = None):
        self.gateway_client = gateway_client
        self.tools: Dict[str, Any] = {}
        self._register_default_tools()
    
    def _register_default_tools(self):
        """Register default tools"""
        if self.gateway_client:
            self.tools["browser"] = BrowserTool(self.gateway_client)
            self.tools["canvas"] = CanvasTool(self.gateway_client)
            self.tools["nodes"] = NodesTool(self.gateway_client)
            self.tools["cron"] = CronTool(self.gateway_client)
        
        self.tools["memory_search"] = MemoryTool()
        self.tools["memory_get"] = MemoryTool()
    
    async def execute(self, tool_name: str, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute tool"""
        tool = self.tools.get(tool_name)
        if not tool:
            raise ValueError(f"Tool '{tool_name}' not found")
        
        if hasattr(tool, 'execute'):
            return await tool.execute(action, params)
        else:
            raise ValueError(f"Tool '{tool_name}' does not support execution")

# Export main classes
__all__ = [
    "ThinkLevel", "VerboseLevel", "UsageDisplayLevel",
    "normalize_think_level", "normalize_verbose_level", "supports_xhigh_thinking",
    "ModelCostConfig", "UsageTotals", "format_token_count", "format_usd", "estimate_usage_cost",
    "SessionEntry", "SessionStore",
    "SkillEntry", "SkillSnapshot", "SkillsManager",
    "GatewayClient",
    "BrowserTool", "CanvasTool", "NodesTool", "CronTool", "MemoryTool",
    "ToolRegistry",
]

