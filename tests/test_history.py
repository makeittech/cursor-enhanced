import os
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
            history, max_tokens=1000, system_prompt_tokens=0, user_prompt_tokens=0
        )
        self.assertTrue(selected)
        self.assertEqual(selected[0]["role"], "system")
