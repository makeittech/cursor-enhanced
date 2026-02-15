#!/usr/bin/env python3
"""
Delegate a task with tracking integration.
"""
import asyncio
import sys
import json
import os
import subprocess

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from runtime_delegate_tool import DelegateTool
from runtime_subagent_tracker import SubagentTracker, SubagentStatus, get_tracker


async def _run_delegate_task_subprocess(execution_id: str, persona_id: str, task: str, config_path: str):
    """Run delegate task in a separate subprocess."""
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "delegate_runner.py")
    cmd = [
        sys.executable,
        script_path,
        execution_id,
        persona_id,
        task,
        config_path,
    ]
    # Start subprocess in background (detached)
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


async def delegate_with_tracking(persona_id: str, task: str, config: dict = None):
    """Delegate a task with tracking. Returns immediately with execution_id."""
    # Load config if available
    if config is None:
        config_path = os.path.expanduser("~/.cursor-enhanced-config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
            except:
                config = {}
        else:
            config = {}
    
    # Initialize tracker and delegate tool
    tracker = get_tracker()
    delegate_tool = DelegateTool(config=config)
    
    # Get persona info
    personas = delegate_tool.list_personas()
    persona = next((p for p in personas if p["id"] == persona_id), None)
    if not persona:
        return {
            "success": False,
            "error": f"Unknown persona '{persona_id}'. Available: {[p['id'] for p in personas]}",
        }
    
    # Start tracking
    execution_id = await tracker.start_execution(
        tool_name="delegate",
        agent_id=persona_id,
        agent_name=persona["name"],
        task=task,
    )
    
    # Update initial status
    await tracker.update_status(execution_id, SubagentStatus.RUNNING)
    await tracker.add_progress_update(execution_id, f"Starting delegate subagent: {persona['name']} ({persona_id})")
    
    # Start background subprocess to run delegate
    config_path = os.path.expanduser("~/.cursor-enhanced-config.json")
    await _run_delegate_task_subprocess(execution_id, persona_id, task, config_path)
    
    await tracker.add_progress_update(execution_id, "Subprocess starting...")
    
    # Return immediately with execution_id
    return {
        "success": True,
        "execution_id": execution_id,
        "status": "started",
        "message": f"Delegation started. Execution ID: {execution_id}",
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: delegate_with_tracking.py <persona_id> <task>")
        sys.exit(1)
    
    persona_id = sys.argv[1]
    task = " ".join(sys.argv[2:])
    
    result = asyncio.run(delegate_with_tracking(persona_id, task))
    print(json.dumps(result, indent=2))
