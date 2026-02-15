#!/usr/bin/env python3
"""
Background runner for delegate tasks with tracking.
"""
import asyncio
import sys
import json
import os
import traceback

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from runtime_delegate_tool import DelegateTool
from runtime_subagent_tracker import SubagentTracker, SubagentStatus, get_tracker


async def run_delegate_task(execution_id: str, persona_id: str, task: str, config_path: str):
    """Run delegate task and update tracker."""
    # Load config
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
        except:
            pass
    
    tracker = get_tracker()
    delegate_tool = DelegateTool(config=config)
    
    try:
        # Update status to running
        await tracker.update_status(execution_id, SubagentStatus.RUNNING)
        await tracker.add_progress_update(execution_id, f"Starting delegate subagent: {persona_id}")
        await tracker.add_progress_update(execution_id, "Subprocess starting...")
        
        # Execute delegate
        result = await delegate_tool.execute(persona_id=persona_id, task=task)
        
        if result.get("success"):
            # Update tracker with success
            await tracker.set_response_preview(execution_id, result.get("response", ""))
            await tracker.update_status(execution_id, SubagentStatus.COMPLETED)
        else:
            # Update tracker with failure
            error = result.get("error", "Unknown error")
            await tracker.update_status(execution_id, SubagentStatus.FAILED, error=error)
    except Exception as e:
        # Update tracker with exception
        error_msg = f"{str(e)}\n{traceback.format_exc()}"
        await tracker.update_status(execution_id, SubagentStatus.FAILED, error=error_msg)


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: delegate_runner.py <execution_id> <persona_id> <task> <config_path>")
        sys.exit(1)
    
    execution_id = sys.argv[1]
    persona_id = sys.argv[2]
    task = sys.argv[3]
    config_path = sys.argv[4]
    
    asyncio.run(run_delegate_task(execution_id, persona_id, task, config_path))
