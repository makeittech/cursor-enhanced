import asyncio
import os
import tempfile
import unittest

from runtime_core import MemoryTool


class MemoryToolTests(unittest.TestCase):
    def test_search_returns_snippet_and_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_dir = os.path.join(temp_dir, "memory")
            os.makedirs(memory_dir, exist_ok=True)
            memory_file = os.path.join(temp_dir, "MEMORY.md")
            with open(memory_file, "w", encoding="utf-8") as f:
                f.write("Alpha\nBeta\nGamma query\nDelta\n")

            tool = MemoryTool(workspace_dir=temp_dir)
            result = asyncio.run(tool.search("query"))
            results = result.get("results", [])

            self.assertTrue(results)
            entry = results[0]
            self.assertIn("snippet", entry)
            self.assertIn("startLine", entry)
            self.assertIn("endLine", entry)
