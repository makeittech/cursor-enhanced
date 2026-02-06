import asyncio
import unittest
from unittest.mock import patch, MagicMock

from openclaw_delegate_tool import DelegateTool, DEFAULT_PERSONAS, AgentPersona


class DelegateToolTests(unittest.TestCase):
    def test_default_personas_loaded(self):
        tool = DelegateTool(config={})
        personas = tool.list_personas()
        ids = {p["id"] for p in personas}
        self.assertIn("researcher", ids)
        self.assertIn("coder", ids)
        self.assertIn("reviewer", ids)
        self.assertIn("writer", ids)
        self.assertGreaterEqual(len(personas), 4)

    def test_custom_personas_override_defaults(self):
        tool = DelegateTool(config={
            "agent_personas": [
                {
                    "id": "researcher",
                    "name": "Custom Researcher",
                    "system_prompt": "Custom prompt.",
                },
            ],
        })
        personas = tool.list_personas()
        researcher = next(p for p in personas if p["id"] == "researcher")
        self.assertEqual(researcher["name"], "Custom Researcher")

    def test_execute_requires_persona_and_task(self):
        async def run():
            tool = DelegateTool(config={})
            out = await tool.execute(persona_id="", task="do something")
            self.assertFalse(out.get("success"))
            self.assertIn("required", out.get("error", "").lower())
            out2 = await tool.execute(persona_id="researcher", task="")
            self.assertFalse(out2.get("success"))

        asyncio.run(run())

    def test_execute_unknown_persona_returns_error_and_list(self):
        async def run():
            tool = DelegateTool(config={})
            out = await tool.execute(persona_id="nonexistent", task="hello")
            self.assertFalse(out.get("success"))
            self.assertIn("Unknown persona", out.get("error", ""))
            self.assertIn("personas", out)

        asyncio.run(run())

    @patch("subprocess.run")
    def test_execute_success_returns_response(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Sub-agent reply here.",
            stderr="",
        )

        async def run():
            tool = DelegateTool(config={})
            out = await tool.execute(persona_id="researcher", task="Summarize X.")
            self.assertTrue(out.get("success"), out)
            self.assertEqual(out.get("response"), "Sub-agent reply here.")
            self.assertEqual(out.get("persona_id"), "researcher")
            self.assertEqual(out.get("persona_name"), "Researcher")

        asyncio.run(run())
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        self.assertIn("-p", call_args[0][0])
        self.assertIn("Summarize X.", str(call_args[0][0]))

    @patch("subprocess.run")
    def test_execute_nonzero_exit_returns_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Something failed",
        )

        async def run():
            tool = DelegateTool(config={})
            out = await tool.execute(persona_id="coder", task="Write code.")
            self.assertFalse(out.get("success"))
            self.assertIn("error", out)
            self.assertEqual(out.get("persona_id"), "coder")

        asyncio.run(run())

    def test_agent_persona_to_dict(self):
        p = AgentPersona(id="x", name="X", system_prompt="Be X.", model=None)
        d = p.to_dict()
        self.assertEqual(d["id"], "x")
        self.assertEqual(d["name"], "X")
        self.assertEqual(d["system_prompt"], "Be X.")
