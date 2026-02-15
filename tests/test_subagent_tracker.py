"""Tests for subagent tracker: Execution, SubagentTracker, completion callback, get_result, Telegram result flow."""
import asyncio
import tempfile
import time
import os

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Import after potential path setup
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime_subagent_tracker import (
    Execution,
    ProgressUpdate,
    SubagentStatus,
    SubagentTracker,
)


class TestExecution(unittest.TestCase):
    def test_elapsed_seconds_none_when_no_start(self):
        e = Execution(execution_id="e1", tool_name="delegate", started_at=None, completed_at=None)
        self.assertIsNone(e.elapsed_seconds)

    def test_elapsed_seconds_completed(self):
        e = Execution(
            execution_id="e1",
            tool_name="delegate",
            started_at=100.0,
            completed_at=110.0,
        )
        self.assertEqual(e.elapsed_seconds, 10.0)

    def test_elapsed_seconds_running_positive(self):
        e = Execution(
            execution_id="e1",
            tool_name="delegate",
            started_at=time.time() - 5.0,
            completed_at=None,
        )
        self.assertIsNotNone(e.elapsed_seconds)
        self.assertGreaterEqual(e.elapsed_seconds, 4.9)


class TestSubagentTracker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state_file = os.path.join(self.tmp, "tracker-state.json")

    def tearDown(self):
        import shutil
        if os.path.exists(self.tmp):
            shutil.rmtree(self.tmp, ignore_errors=True)

    async def _run(self, coro):
        return await coro

    def test_start_and_get_result(self):
        async def run():
            tracker = SubagentTracker(state_file=self.state_file)
            eid = await tracker.start_execution(
                tool_name="delegate",
                agent_id="researcher",
                agent_name="Researcher",
                task="Summarize X",
            )
            self.assertIsNotNone(eid)
            execution = await tracker.get_execution(eid)
            self.assertIsNotNone(execution)
            self.assertEqual(execution.tool_name, "delegate")
            self.assertEqual(execution.agent_id, "researcher")
            self.assertEqual(execution.status, SubagentStatus.STARTING)
            return eid, tracker

        eid, tracker = asyncio.run(run())

        async def run2():
            await tracker.set_response_preview(eid, "Here is the summary.")
            await tracker.update_status(eid, SubagentStatus.COMPLETED)
            result = await tracker.get_result(eid)
            self.assertIsNotNone(result)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["response"], "Here is the summary.")
            self.assertIsNotNone(result.get("elapsed_seconds"))
            self.assertIsNone(result.get("error"))

        asyncio.run(run2())

    def test_completion_callback_invoked(self):
        async def run():
            tracker = SubagentTracker(state_file=self.state_file)
            seen = []
            def on_complete(execution):
                seen.append(execution)
            tracker.register_completion_callback(on_complete)

            eid = await tracker.start_execution(tool_name="delegate", agent_id="coder", agent_name="Coder", task="Code")
            await tracker.set_response_preview(eid, "Done.")
            await tracker.update_status(eid, SubagentStatus.COMPLETED)

            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0].execution_id, eid)
            self.assertEqual(seen[0].status, SubagentStatus.COMPLETED)
            self.assertEqual(seen[0].response_preview, "Done.")
            self.assertIsNotNone(seen[0].elapsed_seconds)

        asyncio.run(run())

    def test_update_execution_meta_persists(self):
        async def run():
            tracker = SubagentTracker(state_file=self.state_file)
            eid = await tracker.start_execution(tool_name="cursor_agent", task="Launch", agent_name="Unnamed")
            await tracker.update_execution_meta(eid, agent_id="ag-123", agent_name="My Agent")
            execution = await tracker.get_execution(eid)
            self.assertEqual(execution.agent_id, "ag-123")
            self.assertEqual(execution.agent_name, "My Agent")
            # Reload from file and check persistence
            tracker2 = SubagentTracker(state_file=self.state_file)
            tracker2._load_state()
            exec2 = tracker2.executions.get(eid)
            self.assertIsNotNone(exec2)
            self.assertEqual(exec2.agent_id, "ag-123")
            self.assertEqual(exec2.agent_name, "My Agent")

        asyncio.run(run())

    def test_get_result_uses_elapsed_seconds_and_response(self):
        async def run():
            tracker = SubagentTracker(state_file=self.state_file)
            eid = await tracker.start_execution(
                tool_name="smart_delegate",
                agent_name="Sonnet",
                task="Analyze",
            )
            await tracker.set_response_preview(eid, "Analysis result here.")
            await tracker.update_status(eid, SubagentStatus.COMPLETED)

            result = await tracker.get_result(eid)
            self.assertEqual(result["response"], "Analysis result here.")
            self.assertIn("elapsed_seconds", result)
            self.assertIsNotNone(result["elapsed_seconds"])

        asyncio.run(run())


class TestTelegramNotificationExecution(unittest.TestCase):
    """Ensure Execution has the attributes the Telegram completion callback uses (no AttributeError)."""
    def test_execution_has_attributes_used_by_telegram_callback(self):
        # _notify_subagent_completion uses: execution.status, response_preview, error,
        # started_at, completed_at, elapsed_seconds (property), task, execution_id, tool_name,
        # agent_name, agent_id
        e = Execution(
            execution_id="abc12345-xxxx",
            tool_name="delegate",
            agent_id="researcher",
            agent_name="Researcher",
            task="Do research",
            started_at=time.time() - 10.0,
            completed_at=time.time(),
            response_preview="Result text",
            status=SubagentStatus.COMPLETED,
        )
        self.assertIsNotNone(e.elapsed_seconds)
        self.assertEqual(e.response_preview, "Result text")
        self.assertEqual(e.status, SubagentStatus.COMPLETED)
        # Callback uses response_preview (no response_full on Execution)
        self.assertIsNone(getattr(e, "response_full", None))


if __name__ == "__main__":
    unittest.main()
