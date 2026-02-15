"""
Runtime Subagent Tracker - Track subagent executions and their status

This module provides tracking for subagent executions (delegate, smart_delegate, cursor_agent)
with persistent state storage and completion callbacks.
"""

import os
import json
import asyncio
import time
import uuid
from enum import Enum
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Any, Callable
import logging

logger = logging.getLogger("cursor_enhanced.subagent_tracker")

# State file path
TRACKER_STATE_FILE = os.path.expanduser("~/.cursor-enhanced/subagent-tracker-state.json")


class SubagentStatus(Enum):
    """Status of a subagent execution."""
    STARTING = "starting"
    RUNNING = "running"
    THINKING = "thinking"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class ProgressUpdate:
    """A progress update for an execution."""
    timestamp: float
    message: str
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class Execution:
    """Represents a subagent execution."""
    execution_id: str
    tool_name: str
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    task: Optional[str] = None
    model: Optional[str] = None
    status: SubagentStatus = SubagentStatus.STARTING
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    response_preview: Optional[str] = None
    error: Optional[str] = None
    progress_updates: List[ProgressUpdate] = field(default_factory=list)
    complexity_score: Optional[float] = None
    tier: Optional[str] = None

    @property
    def elapsed_seconds(self) -> Optional[float]:
        """Elapsed time in seconds (from start to completion or now)."""
        if self.started_at is None:
            return None
        end = self.completed_at if self.completed_at is not None else time.time()
        return end - self.started_at

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = asdict(self)
        result["status"] = self.status.value
        result["progress_updates"] = [
            {
                "timestamp": pu.timestamp,
                "message": pu.message,
                "metadata": pu.metadata,
            }
            for pu in self.progress_updates
        ]
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Execution":
        """Create Execution from dictionary."""
        status = SubagentStatus(data.get("status", "starting"))
        progress_updates = [
            ProgressUpdate(
                timestamp=pu.get("timestamp", time.time()),
                message=pu.get("message", ""),
                metadata=pu.get("metadata"),
            )
            for pu in data.get("progress_updates", [])
        ]
        return cls(
            execution_id=data["execution_id"],
            tool_name=data["tool_name"],
            agent_id=data.get("agent_id"),
            agent_name=data.get("agent_name"),
            task=data.get("task"),
            model=data.get("model"),
            status=status,
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            response_preview=data.get("response_preview"),
            error=data.get("error"),
            progress_updates=progress_updates,
            complexity_score=data.get("complexity_score"),
            tier=data.get("tier"),
        )


class SubagentTracker:
    """Tracks subagent executions with persistent state."""
    
    def __init__(self, state_file: Optional[str] = None):
        self.state_file = state_file or TRACKER_STATE_FILE
        self.executions: Dict[str, Execution] = {}
        self._completion_callbacks: List[Callable] = []
        self._lock = asyncio.Lock()
        self._load_state()
    
    def _load_state(self) -> None:
        """Load state from file (synchronous, called during init)."""
        if not os.path.exists(self.state_file):
            logger.debug(f"State file does not exist: {self.state_file}")
            return
        
        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
            
            executions_data = data.get("executions", {})
            for exec_id, exec_data in executions_data.items():
                try:
                    self.executions[exec_id] = Execution.from_dict(exec_data)
                except Exception as e:
                    logger.warning(f"Failed to load execution {exec_id}: {e}")
            
            logger.info(f"Loaded {len(self.executions)} executions from state file")
        except Exception as e:
            logger.error(f"Failed to load state from {self.state_file}: {e}", exc_info=True)
    
    async def _load_state_async(self) -> None:
        """Load state from file asynchronously (for refresh)."""
        async with self._lock:
            self._load_state()
    
    def _save_state(self) -> None:
        """Save state to file (synchronous)."""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            data = {
                "executions": {
                    exec_id: exec_obj.to_dict()
                    for exec_id, exec_obj in self.executions.items()
                }
            }
            with open(self.state_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state to {self.state_file}: {e}", exc_info=True)
    
    async def _save_state_async(self) -> None:
        """Save state to file asynchronously."""
        async with self._lock:
            self._save_state()
    
    async def start_execution(
        self,
        tool_name: str,
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        task: Optional[str] = None,
        model: Optional[str] = None,
        complexity_score: Optional[float] = None,
        tier: Optional[str] = None,
    ) -> str:
        """Start tracking a new execution. Returns execution_id."""
        execution_id = str(uuid.uuid4())
        execution = Execution(
            execution_id=execution_id,
            tool_name=tool_name,
            agent_id=agent_id,
            agent_name=agent_name,
            task=task,
            model=model,
            status=SubagentStatus.STARTING,
            started_at=time.time(),
            complexity_score=complexity_score,
            tier=tier,
        )
        
        async with self._lock:
            self.executions[execution_id] = execution
            await asyncio.to_thread(self._save_state)
        
        logger.info(f"Started tracking execution {execution_id[:8]}... ({tool_name})")
        return execution_id
    
    async def update_status(
        self,
        execution_id: str,
        status: SubagentStatus,
        error: Optional[str] = None,
    ) -> None:
        """Update the status of an execution."""
        async with self._lock:
            execution = self.executions.get(execution_id)
            if not execution:
                logger.warning(f"Execution {execution_id[:8]}... not found")
                return
            
            old_status = execution.status
            execution.status = status
            
            if status in [SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.TIMEOUT, SubagentStatus.CANCELLED]:
                execution.completed_at = time.time()
            
            if error:
                execution.error = error
            
            await asyncio.to_thread(self._save_state)
            
            # Trigger completion callbacks
            if old_status != status and status in [SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.TIMEOUT]:
                for callback in self._completion_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(execution)
                        else:
                            callback(execution)
                    except Exception as e:
                        logger.error(f"Completion callback failed: {e}", exc_info=True)
    
    async def add_progress_update(
        self,
        execution_id: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a progress update to an execution."""
        async with self._lock:
            execution = self.executions.get(execution_id)
            if not execution:
                logger.warning(f"Execution {execution_id[:8]}... not found")
                return
            
            update = ProgressUpdate(
                timestamp=time.time(),
                message=message,
                metadata=metadata,
            )
            execution.progress_updates.append(update)
            await asyncio.to_thread(self._save_state)
    
    async def set_response_preview(
        self,
        execution_id: str,
        preview: str,
    ) -> None:
        """Set the response preview (or full result) for an execution."""
        async with self._lock:
            execution = self.executions.get(execution_id)
            if not execution:
                logger.warning(f"Execution {execution_id[:8]}... not found")
                return

            execution.response_preview = preview
            await asyncio.to_thread(self._save_state)

    async def update_execution_meta(
        self,
        execution_id: str,
        *,
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> None:
        """Update execution metadata and persist."""
        async with self._lock:
            execution = self.executions.get(execution_id)
            if not execution:
                logger.warning(f"Execution {execution_id[:8]}... not found")
                return
            if agent_id is not None:
                execution.agent_id = agent_id
            if agent_name is not None:
                execution.agent_name = agent_name
            await asyncio.to_thread(self._save_state)

    async def get_execution(self, execution_id: str) -> Optional[Execution]:
        """Get an execution by ID."""
        async with self._lock:
            return self.executions.get(execution_id)
    
    async def list_executions(
        self,
        tool_name: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Execution]:
        """List executions, optionally filtered by tool_name."""
        async with self._lock:
            executions = list(self.executions.values())
            
            if tool_name:
                executions = [e for e in executions if e.tool_name == tool_name]
            
            # Sort by started_at descending (most recent first)
            executions.sort(key=lambda e: e.started_at or 0, reverse=True)
            
            if limit:
                executions = executions[:limit]
            
            return executions
    
    async def get_active_executions(self) -> List[Execution]:
        """Get all active (non-completed) executions."""
        async with self._lock:
            active_statuses = {
                SubagentStatus.STARTING,
                SubagentStatus.RUNNING,
                SubagentStatus.THINKING,
            }
            executions = [
                e for e in self.executions.values()
                if e.status in active_statuses
            ]
            # Sort by started_at descending
            executions.sort(key=lambda e: e.started_at or 0, reverse=True)
            return executions
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get statistics about executions."""
        async with self._lock:
            total = len(self.executions)
            active = len([
                e for e in self.executions.values()
                if e.status in {SubagentStatus.STARTING, SubagentStatus.RUNNING, SubagentStatus.THINKING}
            ])
            completed = len([
                e for e in self.executions.values()
                if e.status == SubagentStatus.COMPLETED
            ])
            failed = len([
                e for e in self.executions.values()
                if e.status == SubagentStatus.FAILED
            ])
            timeout = len([
                e for e in self.executions.values()
                if e.status == SubagentStatus.TIMEOUT
            ])
            
            return {
                "total_executions": total,
                "active_executions": active,
                "completed_executions": completed,
                "failed_executions": failed,
                "timeout_executions": timeout,
            }
    
    async def get_result(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get full result for an execution."""
        async with self._lock:
            execution = self.executions.get(execution_id)
            if not execution:
                return None

            return {
                "execution_id": execution.execution_id,
                "tool_name": execution.tool_name,
                "agent_id": execution.agent_id,
                "agent_name": execution.agent_name,
                "task": execution.task,
                "model": execution.model,
                "status": execution.status.value,
                "started_at": execution.started_at,
                "completed_at": execution.completed_at,
                "elapsed_seconds": execution.elapsed_seconds,
                "response": execution.response_preview,
                "error": execution.error,
                "progress_updates": [
                    {
                        "timestamp": pu.timestamp,
                        "message": pu.message,
                        "metadata": pu.metadata,
                    }
                    for pu in execution.progress_updates
                ],
            }
    
    def register_completion_callback(self, callback: Callable) -> None:
        """Register a callback to be called when an execution completes."""
        self._completion_callbacks.append(callback)
        logger.info(f"Registered completion callback: {callback.__name__ if hasattr(callback, '__name__') else 'anonymous'}")


# Singleton instance
_tracker_instance: Optional[SubagentTracker] = None


def get_tracker() -> SubagentTracker:
    """Get the singleton tracker instance."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = SubagentTracker()
    return _tracker_instance
