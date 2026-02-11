"""Tests for the Cursor Cloud Agent tool (runtime_cursor_agent.py)."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

from runtime_cursor_agent import CursorAgentTool, API_BASE


def run(coro):
    """Helper to run async coroutines in sync tests."""
    return asyncio.run(coro)


class PreflightTests(unittest.TestCase):
    """Tests for pre-flight checks."""

    def test_disabled_returns_error(self):
        tool = CursorAgentTool({"tools": {"cursor_agent": {"enabled": False}}})
        result = run(tool.execute("list"))
        self.assertIn("error", result)
        self.assertIn("disabled", result["error"])

    def test_missing_api_key_returns_error(self):
        tool = CursorAgentTool({"tools": {"cursor_agent": {"api_key": ""}}})
        result = run(tool.execute("list"))
        self.assertIn("error", result)
        self.assertIn("API key", result["error"])

    def test_unknown_action_returns_error(self):
        tool = CursorAgentTool({"tools": {"cursor_agent": {"api_key": "test123"}}})
        result = run(tool.execute("nonexistent_action"))
        self.assertIn("error", result)
        self.assertIn("Unknown action", result["error"])


class ActionRoutingTests(unittest.TestCase):
    """Tests that execute dispatches to the correct handler."""

    def setUp(self):
        self.tool = CursorAgentTool({"tools": {"cursor_agent": {"api_key": "test_key"}}})

    def test_launch_requires_prompt(self):
        result = run(self.tool.execute("launch"))
        self.assertIn("error", result)
        self.assertIn("prompt", result["error"])

    def test_launch_requires_repository_or_pr_url(self):
        result = run(self.tool.execute("launch", prompt="Fix bug"))
        self.assertIn("error", result)
        self.assertIn("repository", result["error"])

    def test_status_requires_agent_id(self):
        result = run(self.tool.execute("status"))
        self.assertIn("error", result)
        self.assertIn("agent_id", result["error"])

    def test_followup_requires_agent_id(self):
        result = run(self.tool.execute("followup"))
        self.assertIn("error", result)
        self.assertIn("agent_id", result["error"])

    def test_followup_requires_prompt(self):
        result = run(self.tool.execute("followup", agent_id="bc_test"))
        self.assertIn("error", result)
        self.assertIn("prompt", result["error"])

    def test_stop_requires_agent_id(self):
        result = run(self.tool.execute("stop"))
        self.assertIn("error", result)
        self.assertIn("agent_id", result["error"])

    def test_delete_requires_agent_id(self):
        result = run(self.tool.execute("delete"))
        self.assertIn("error", result)
        self.assertIn("agent_id", result["error"])

    def test_conversation_requires_agent_id(self):
        result = run(self.tool.execute("conversation"))
        self.assertIn("error", result)
        self.assertIn("agent_id", result["error"])

    def test_action_aliases(self):
        """Test that alias actions route correctly."""
        # create -> launch
        result = run(self.tool.execute("create"))
        self.assertIn("prompt", result.get("error", ""))
        # get -> status
        result = run(self.tool.execute("get"))
        self.assertIn("agent_id", result.get("error", ""))
        # follow_up -> followup
        result = run(self.tool.execute("follow_up"))
        self.assertIn("agent_id", result.get("error", ""))
        # info -> me (will try to call API, but we check routing)
        # repositories -> repos
        # These will fail at API level, not routing level


class MockHTTPTests(unittest.TestCase):
    """Tests with mocked HTTP responses."""

    def setUp(self):
        self.tool = CursorAgentTool({"tools": {"cursor_agent": {"api_key": "test_key_123"}}})

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_launch_success(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "bc_test123",
            "name": "Test Agent",
            "status": "CREATING",
            "source": {"repository": "https://github.com/test/repo", "ref": "main"},
            "target": {
                "branchName": "cursor/test-123",
                "url": "https://cursor.com/agents?id=bc_test123",
            },
            "createdAt": "2024-01-15T10:30:00Z",
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = run(self.tool.launch(
            prompt="Fix the bug in main.py",
            repository="https://github.com/test/repo",
            ref="main",
        ))

        self.assertNotIn("error", result)
        self.assertEqual(result["id"], "bc_test123")
        self.assertEqual(result["status"], "CREATING")
        self.assertIn("_summary", result)
        self.assertIn("launched", result["_summary"])

        # Verify the POST body
        call_args = mock_client.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        self.assertEqual(body["prompt"]["text"], "Fix the bug in main.py")
        self.assertEqual(body["source"]["repository"], "https://github.com/test/repo")

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_launch_with_pr_url(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "bc_pr123",
            "name": "PR Agent",
            "status": "CREATING",
            "source": {"repository": "https://github.com/test/repo"},
            "target": {"url": "https://cursor.com/agents?id=bc_pr123"},
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = run(self.tool.launch(
            prompt="Review this PR",
            pr_url="https://github.com/test/repo/pull/42",
        ))

        self.assertNotIn("error", result)
        call_args = mock_client.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        self.assertEqual(body["source"]["prUrl"], "https://github.com/test/repo/pull/42")
        self.assertNotIn("repository", body["source"])

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_list_agents(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "agents": [
                {"id": "bc_1", "name": "Agent 1", "status": "FINISHED"},
                {"id": "bc_2", "name": "Agent 2", "status": "RUNNING"},
            ],
            "nextCursor": None,
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = run(self.tool.list_agents())

        self.assertNotIn("error", result)
        self.assertEqual(len(result["agents"]), 2)
        self.assertIn("_summary", result)
        self.assertIn("2 agent(s)", result["_summary"])

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_status(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "bc_test",
            "name": "Test Agent",
            "status": "FINISHED",
            "summary": "Successfully added README.md",
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = run(self.tool.status(agent_id="bc_test"))

        self.assertNotIn("error", result)
        self.assertEqual(result["status"], "FINISHED")
        self.assertIn("FINISHED", result["_summary"])

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_conversation(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "bc_test",
            "messages": [
                {"id": "msg_1", "type": "user_message", "text": "Fix the bug"},
                {"id": "msg_2", "type": "assistant_message", "text": "I'll fix it now..."},
            ],
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = run(self.tool.conversation(agent_id="bc_test"))

        self.assertNotIn("error", result)
        self.assertEqual(len(result["messages"]), 2)
        self.assertIn("2 message(s)", result["_summary"])

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_followup(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "bc_test"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = run(self.tool.followup(agent_id="bc_test", prompt="Also add tests"))

        self.assertNotIn("error", result)
        self.assertIn("Follow-up sent", result["_summary"])

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_stop(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "bc_test"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = run(self.tool.stop(agent_id="bc_test"))

        self.assertNotIn("error", result)
        self.assertIn("stopped", result["_summary"])

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_delete(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "bc_test"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.delete.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = run(self.tool.delete(agent_id="bc_test"))

        self.assertNotIn("error", result)
        self.assertIn("deleted", result["_summary"])

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_list_models(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": ["claude-4-sonnet-thinking", "gpt-5.2", "claude-4.5-sonnet-thinking"],
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = run(self.tool.list_models())

        self.assertNotIn("error", result)
        self.assertIn("claude-4-sonnet-thinking", result["_summary"])

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_me(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "apiKeyName": "My Key",
            "userEmail": "user@example.com",
            "createdAt": "2024-01-01T00:00:00Z",
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = run(self.tool.me())

        self.assertNotIn("error", result)
        self.assertIn("My Key", result["_summary"])
        self.assertIn("user@example.com", result["_summary"])

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_list_repos(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "repositories": [
                {"owner": "org", "name": "repo1", "repository": "https://github.com/org/repo1"},
                {"owner": "org", "name": "repo2", "repository": "https://github.com/org/repo2"},
            ],
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = run(self.tool.list_repos())

        self.assertNotIn("error", result)
        self.assertIn("2 repo(s)", result["_summary"])


class HTTPErrorTests(unittest.TestCase):
    """Tests for HTTP error handling."""

    def setUp(self):
        self.tool = CursorAgentTool({"tools": {"cursor_agent": {"api_key": "test_key"}}})

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_http_error_returns_error_dict(self, mock_client_cls):
        import httpx as real_httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_resp.raise_for_status.side_effect = real_httpx.HTTPStatusError(
            "401", request=MagicMock(), response=mock_resp
        )

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = run(self.tool.me())

        self.assertIn("error", result)
        self.assertIn("401", result["error"])

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_network_error_returns_error_dict(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = run(self.tool.me())

        self.assertIn("error", result)
        self.assertIn("Connection refused", result["error"])


class LaunchBodyConstructionTests(unittest.TestCase):
    """Tests that launch constructs the correct API request body."""

    def setUp(self):
        self.tool = CursorAgentTool({"tools": {"cursor_agent": {"api_key": "k"}}})

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_launch_with_all_options(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "bc_x", "name": "X", "status": "CREATING", "target": {"url": "u"}}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = run(self.tool.launch(
            prompt="Do the thing",
            repository="https://github.com/org/repo",
            ref="develop",
            model="claude-4-sonnet",
            user_confirmed_model=True,  # User explicitly requested this model
            auto_create_pr=True,
            branch_name="my-branch",
            open_as_cursor_app=True,
            skip_reviewer_request=True,
        ))

        call_args = mock_client.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")

        self.assertEqual(body["prompt"]["text"], "Do the thing")
        self.assertEqual(body["source"]["repository"], "https://github.com/org/repo")
        self.assertEqual(body["source"]["ref"], "develop")
        self.assertEqual(body["model"], "claude-4-sonnet")
        self.assertTrue(body["target"]["autoCreatePr"])
        self.assertEqual(body["target"]["branchName"], "my-branch")
        self.assertTrue(body["target"]["openAsCursorGithubApp"])
        self.assertTrue(body["target"]["skipReviewerRequest"])

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_launch_minimal(self, mock_client_cls):
        """Minimal launch: only prompt and repository. Default model 'default' is sent to Cursor."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "bc_y", "name": "Y", "status": "CREATING", "target": {"url": "u"}}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = run(self.tool.launch(
            prompt="Add tests",
            repository="https://github.com/org/repo",
        ))

        call_args = mock_client.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")

        self.assertEqual(body["prompt"]["text"], "Add tests")
        # default_model is "default" — sent as "model": "default" so Cursor auto-selects
        self.assertEqual(body["model"], "default")
        self.assertNotIn("target", body)

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_api_key_used_as_basic_auth(self, mock_client_cls):
        """Verify auth uses BasicAuth with API key as username."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"agents": []}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        run(self.tool.list_agents())

        call_args = mock_client.get.call_args
        auth = call_args.kwargs.get("auth")
        self.assertIsNotNone(auth)
        # Verify it's an httpx.BasicAuth instance
        import httpx as real_httpx
        self.assertIsInstance(auth, real_httpx.BasicAuth)


class DefaultModelTests(unittest.TestCase):
    """Tests for default_model configuration."""

    def test_default_model_is_default(self):
        """Default model should be 'default' when not configured."""
        tool = CursorAgentTool({"tools": {"cursor_agent": {"api_key": "k"}}})
        self.assertEqual(tool.default_model, "default")

    def test_default_model_from_config(self):
        """default_model should be read from config."""
        tool = CursorAgentTool({"tools": {"cursor_agent": {"api_key": "k", "default_model": "gpt-5.2"}}})
        self.assertEqual(tool.default_model, "gpt-5.2")

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_default_model_sent_in_body(self, mock_client_cls):
        """When default_model is 'default', model field should be 'default' in the POST body."""
        tool = CursorAgentTool({"tools": {"cursor_agent": {"api_key": "k", "default_model": "default"}}})

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "bc_a", "name": "A", "status": "CREATING", "target": {"url": "u"}}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        run(tool.launch(prompt="Fix bug", repository="https://github.com/org/repo"))

        body = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1].get("json")
        self.assertEqual(body["model"], "default")

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_non_auto_default_model_sent_in_body(self, mock_client_cls):
        """When default_model is a real model name and user confirmed, it is sent in the POST body."""
        tool = CursorAgentTool({"tools": {"cursor_agent": {"api_key": "k", "default_model": "gpt-5.2"}}})

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "bc_b", "name": "B", "status": "CREATING", "target": {"url": "u"}}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        run(tool.launch(prompt="Fix bug", repository="https://github.com/org/repo", user_confirmed_model=True))

        body = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1].get("json")
        self.assertEqual(body["model"], "gpt-5.2")

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_explicit_model_overrides_default(self, mock_client_cls):
        """Explicit model param overrides default_model when user_confirmed_model=True."""
        tool = CursorAgentTool({"tools": {"cursor_agent": {"api_key": "k", "default_model": "gpt-5.2"}}})

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "bc_c", "name": "C", "status": "CREATING", "target": {"url": "u"}}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        run(tool.launch(prompt="Fix bug", repository="https://github.com/org/repo", model="claude-4-sonnet", user_confirmed_model=True))

        body = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1].get("json")
        self.assertEqual(body["model"], "claude-4-sonnet")

    @patch("runtime_cursor_agent.httpx.AsyncClient")
    def test_explicit_default_model_sent(self, mock_client_cls):
        """Explicitly passing model='default' should send 'default' as model field."""
        tool = CursorAgentTool({"tools": {"cursor_agent": {"api_key": "k", "default_model": "gpt-5.2"}}})

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "bc_d", "name": "D", "status": "CREATING", "target": {"url": "u"}}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        run(tool.launch(prompt="Fix bug", repository="https://github.com/org/repo", model="default"))

        body = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1].get("json")
        self.assertEqual(body["model"], "default")


class ConfigTests(unittest.TestCase):
    """Tests for configuration loading."""

    def test_default_config(self):
        tool = CursorAgentTool()
        self.assertTrue(tool.enabled)
        self.assertEqual(tool.api_key, "")
        self.assertEqual(tool.default_model, "default")

    def test_config_from_dict(self):
        tool = CursorAgentTool({"tools": {"cursor_agent": {"api_key": "my_key", "enabled": True}}})
        self.assertEqual(tool.api_key, "my_key")
        self.assertTrue(tool.enabled)

    @patch.dict("os.environ", {"CURSOR_API_KEY": "env_key_123"})
    def test_env_var_fallback(self):
        tool = CursorAgentTool()  # No config api_key
        self.assertEqual(tool.api_key, "env_key_123")

    def test_config_api_key_overrides_env(self):
        with patch.dict("os.environ", {"CURSOR_API_KEY": "env_key"}):
            tool = CursorAgentTool({"tools": {"cursor_agent": {"api_key": "cfg_key"}}})
            self.assertEqual(tool.api_key, "cfg_key")


if __name__ == "__main__":
    unittest.main()
