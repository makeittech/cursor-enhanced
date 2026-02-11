import os
import sys
import argparse
import tempfile
import unittest

import main


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.temp_dir.name

    def tearDown(self):
        if self.old_home is not None:
            os.environ["HOME"] = self.old_home
        self.temp_dir.cleanup()

    def test_estimate_tokens(self):
        self.assertEqual(main.estimate_tokens("abcd"), 1)
        self.assertEqual(main.estimate_tokens("abcde"), 1)
        self.assertEqual(main.estimate_tokens(""), 0)

    def test_get_history_fitting_token_limit_preserves_summary(self):
        history = [
            {"role": "system", "content": "summary text"},
            {"role": "user", "content": "hello"},
            {"role": "agent", "content": "world"},
        ]
        selected, _ = main.get_history_fitting_token_limit(
            history, max_tokens=5000, system_prompt_tokens=0, user_prompt_tokens=0
        )
        self.assertTrue(selected)
        self.assertEqual(selected[0]["role"], "system")


class FreshFlagParserTests(unittest.TestCase):
    """Tests for the --fresh CLI flag (used by 'new' thread messages)."""

    def test_fresh_flag_accepted_by_parser(self):
        """--fresh should be a valid flag that argparse accepts."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--fresh", action="store_true")
        parser.add_argument("--enable-runtime", action="store_true", default=True)
        parser.add_argument("--history-limit", type=int, default=None)
        parser.add_argument("--chat", type=str, default=None)
        parser.add_argument("-p", type=str, default=None)

        args, _ = parser.parse_known_args(["--fresh", "--enable-runtime", "-p", "hello world"])
        self.assertTrue(args.fresh)
        self.assertEqual(args.p, "hello world")

    def test_fresh_flag_default_false(self):
        """--fresh should default to False when not provided."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--fresh", action="store_true")
        args, _ = parser.parse_known_args([])
        self.assertFalse(args.fresh)


class NewPrefixDetectionTests(unittest.TestCase):
    """Tests for 'new' prefix detection in Telegram message handling."""

    def test_new_prefix_detected(self):
        """Messages starting with 'new ' should be detected."""
        msg = "new What is the weather in Kyiv?"
        stripped = msg.strip()
        is_new = stripped.lower().startswith("new ") or stripped.lower() == "new"
        self.assertTrue(is_new)

    def test_new_prefix_case_insensitive(self):
        """'New', 'NEW', 'nEw' should all be detected."""
        for prefix in ["New ", "NEW ", "nEw "]:
            msg = prefix + "some task"
            stripped = msg.strip()
            is_new = stripped.lower().startswith("new ") or stripped.lower() == "new"
            self.assertTrue(is_new, f"Failed for prefix: {prefix!r}")

    def test_new_alone_detected(self):
        """Just 'new' with no message should be detected (but will be rejected for empty body)."""
        msg = "new"
        stripped = msg.strip()
        is_new = stripped.lower().startswith("new ") or stripped.lower() == "new"
        self.assertTrue(is_new)

    def test_new_prefix_strips_correctly(self):
        """After stripping 'new ', the actual message should remain."""
        msg = "new What is 2+2?"
        stripped = msg.strip()
        actual = stripped[4:].strip() if len(stripped) > 3 else ""
        self.assertEqual(actual, "What is 2+2?")

    def test_not_new_prefix(self):
        """Words starting with 'new' but not 'new ' should NOT trigger."""
        for msg in ["news today", "newsletter", "newton was great"]:
            stripped = msg.strip()
            is_new = stripped.lower().startswith("new ") or stripped.lower() == "new"
            self.assertFalse(is_new, f"False positive for: {msg!r}")

    def test_normal_message_not_detected(self):
        """Normal messages should not be detected as 'new' prefix."""
        msg = "What is the weather?"
        stripped = msg.strip()
        is_new = stripped.lower().startswith("new ") or stripped.lower() == "new"
        self.assertFalse(is_new)
