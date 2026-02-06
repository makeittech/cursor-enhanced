import unittest

import main


class MemoryFlushTests(unittest.TestCase):
    def test_resolve_memory_flush_disabled(self):
        cfg = {"agents": {"defaults": {"compaction": {"memoryFlush": {"enabled": False}}}}}
        self.assertIsNone(main.resolve_memory_flush_settings(cfg))

    def test_resolve_memory_flush_defaults(self):
        cfg = {}
        settings = main.resolve_memory_flush_settings(cfg)
        self.assertIsNotNone(settings)
        self.assertIn(main.MEMORY_FLUSH_NO_REPLY, settings["prompt"])

    def test_should_run_memory_flush_threshold(self):
        settings = {
            "soft_threshold_tokens": 4000,
            "reserve_tokens_floor": 20000,
        }
        meta = {"compaction_count": 0}
        total_tokens = 80000
        self.assertTrue(main.should_run_memory_flush(total_tokens, settings, meta))

    def test_should_run_memory_flush_skip_on_repeat(self):
        settings = {
            "soft_threshold_tokens": 4000,
            "reserve_tokens_floor": 20000,
        }
        meta = {"compaction_count": 0, "memory_flush_compaction_count": 1}
        total_tokens = 80000
        self.assertFalse(main.should_run_memory_flush(total_tokens, settings, meta))
