"""Tests for Telegram LLM response formatting."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram_integration import (
    format_llm_response_for_telegram,
    chunk_message_for_telegram,
    _telegram_html_balanced,
    _chunk_send_args,
)


class TestFormatLlmResponseForTelegram(unittest.TestCase):
    def test_empty_returns_unchanged(self):
        out, use_html = format_llm_response_for_telegram("")
        self.assertEqual(out, "")
        self.assertFalse(use_html)

    def test_bold_converted(self):
        out, use_html = format_llm_response_for_telegram("Hello **world**")
        self.assertTrue(use_html)
        self.assertIn("<b>world</b>", out)
        self.assertNotIn("**", out)

    def test_inline_code_converted(self):
        out, use_html = format_llm_response_for_telegram("Run `cursor-enhanced --help`")
        self.assertTrue(use_html)
        self.assertIn("<code>", out)
        self.assertIn("</code>", out)

    def test_fenced_block_converted(self):
        text = "Example:\n```\nfoo\nbar\n```\nDone."
        out, use_html = format_llm_response_for_telegram(text)
        self.assertTrue(use_html)
        self.assertIn("<pre>", out)
        self.assertIn("</pre>", out)
        self.assertNotIn("```", out)

    def test_html_escaped(self):
        out, use_html = format_llm_response_for_telegram("a <b> b & c")
        self.assertTrue(use_html)
        self.assertIn("&lt;", out)
        self.assertIn("&amp;", out)

    def test_plain_text_no_markdown_returns_plain(self):
        out, use_html = format_llm_response_for_telegram("Just plain text.")
        self.assertTrue(use_html)
        self.assertEqual(out, "Just plain text.")

    def test_smilies_converted_to_emoji(self):
        out, use_html = format_llm_response_for_telegram("Done :) and :( ok")
        self.assertTrue(use_html)
        self.assertIn("😊", out)
        self.assertIn("😞", out)
        self.assertNotIn(":)", out)
        self.assertNotIn(":(", out)

    def test_smilies_in_code_block_preserved(self):
        out, use_html = format_llm_response_for_telegram("Use flag `:)` in code.")
        self.assertTrue(use_html)
        # :) in inline code must stay as text (inside <code>)
        self.assertIn("<code>", out)
        self.assertIn(":)", out)  # inside code, not replaced
        # Optional: prose part might have no smiley; we just ensure code content is preserved
        self.assertIn("</code>", out)

    def test_italic_underscore_converted(self):
        out, use_html = format_llm_response_for_telegram("Say _hello_ there.")
        self.assertTrue(use_html)
        self.assertIn("<i>hello</i>", out)
        self.assertNotIn("_hello_", out)

    def test_strikethrough_converted(self):
        out, use_html = format_llm_response_for_telegram("Not ~~deprecated~~ anymore.")
        self.assertTrue(use_html)
        self.assertIn("<s>deprecated</s>", out)
        self.assertNotIn("~~", out)

    def test_markdown_link_converted(self):
        out, use_html = format_llm_response_for_telegram("See [documentation](https://example.com).")
        self.assertTrue(use_html)
        self.assertIn('<a href="https://example.com">documentation</a>', out)
        self.assertNotIn("[documentation]", out)

    def test_leftover_markdown_stripped(self):
        out, use_html = format_llm_response_for_telegram("Bold **foo** and stray ** here.")
        self.assertTrue(use_html)
        self.assertIn("<b>foo</b>", out)
        self.assertNotIn("**", out)
        out2, _ = format_llm_response_for_telegram("Underscore __bar__ and __ left.")
        self.assertNotIn("__", out2)

    def test_header_converted_to_bold(self):
        out, use_html = format_llm_response_for_telegram("## Section title\n\nContent here.")
        self.assertTrue(use_html)
        self.assertIn("<b>Section title</b>", out)
        self.assertNotIn("#", out)

    def test_fallback_sanitize_no_raw_symbols(self):
        # When conversion fails (e.g. unbalanced tags), fallback still escapes and strips
        out, use_html = format_llm_response_for_telegram("a <b> b & c")
        self.assertTrue(use_html)
        self.assertIn("&lt;", out)
        self.assertIn("&amp;", out)
        self.assertNotIn("**", out)

    def test_llm_literal_html_tags_rendered_as_formatting(self):
        # When LLM outputs literal <b>...</b> instead of **...**, un-escape so Telegram shows bold
        out, use_html = format_llm_response_for_telegram("Answer: <b>yes</b>.")
        self.assertTrue(use_html)
        self.assertIn("<b>yes</b>", out)
        self.assertNotIn("&lt;b&gt;", out)

    def test_bold_colon_star_not_smiley(self):
        # ":**" must not become 😘* (smiley :* should not replace when followed by *)
        out, use_html = format_llm_response_for_telegram("Fix **foo**:** bar")
        self.assertTrue(use_html)
        self.assertIn("<b>foo</b>", out)
        self.assertNotIn("😘", out)

    def test_url_colon_slash_not_smiley(self):
        # "://" in URL must not become 😕/
        out, use_html = format_llm_response_for_telegram("See https://example.com/ path.")
        self.assertTrue(use_html)
        self.assertIn("https://example.com/", out)
        self.assertNotIn("😕", out)

    def test_underscore_in_identifier_not_italic(self):
        # web_fetch, tool_name must not be split into italic
        out, use_html = format_llm_response_for_telegram("Run **1. Web_fetch** and tool_executor.")
        self.assertTrue(use_html)
        self.assertIn("<b>1. Web_fetch</b>", out)
        self.assertNotIn("<i>fetch</i>", out)
        self.assertNotIn("<i>executor</i>", out)

    def test_empty_header_not_converted_to_empty_bold(self):
        # "## " or "##" alone must not become ** ** (which would show as </b><b> in Telegram)
        out, use_html = format_llm_response_for_telegram("## \n## Фаза 3: Title\n\nContent")
        self.assertTrue(use_html)
        self.assertIn("<b>Фаза 3: Title</b>", out)
        self.assertNotIn("</b><b>", out)
        self.assertNotIn("<b> </b>", out)

    def test_redundant_bold_tags_collapsed(self):
        # Adjacent </b><b> or empty <b></b> must be removed so Telegram doesn't show raw tags
        out, use_html = format_llm_response_for_telegram("**A**\n**B**")
        self.assertTrue(use_html)
        self.assertIn("<b>A</b>", out)
        self.assertIn("<b>B</b>", out)
        self.assertNotIn("</b><b>", out)


class TestChunkMessageForTelegram(unittest.TestCase):
    def test_short_text_one_chunk(self):
        chunks = chunk_message_for_telegram("Hi", max_length=4090)
        self.assertEqual(chunks, ["Hi"])

    def test_long_text_split(self):
        long = "x" * 5000
        chunks = chunk_message_for_telegram(long, max_length=1000)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))

    def test_chunk_splits_after_closing_tag(self):
        # Long HTML: split should occur after </b> (or newline), not in the middle of a tag
        part1 = "a" * 400 + "<b>bold</b>"
        part2 = "b" * 600
        text = part1 + part2
        chunks = chunk_message_for_telegram(text, max_length=500)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 520 for c in chunks))  # + "[Continued...]\n"
        # First chunk should end at a safe boundary (after </b> or at newline)
        self.assertTrue(chunks[0].endswith("</b>") or "\n" in chunks[0])

    def test_telegram_html_balanced(self):
        self.assertTrue(_telegram_html_balanced("<b>x</b>"))
        self.assertTrue(_telegram_html_balanced("<b>a</b> <code>b</code>"))
        self.assertFalse(_telegram_html_balanced("<b>unclosed"))
        self.assertFalse(_telegram_html_balanced("</code> stray"))

    def test_chunk_send_args_sanitizes_unbalanced(self):
        balanced = "<b>ok</b>"
        text, mode = _chunk_send_args(balanced, use_html=True)
        self.assertEqual(text, balanced)
        self.assertEqual(mode, "HTML")
        unbalanced = "<b>broken</code>"
        text2, mode2 = _chunk_send_args(unbalanced, use_html=True)
        self.assertNotIn("<b>", text2)
        self.assertNotIn("</code>", text2)
        self.assertEqual(mode2, None)


if __name__ == "__main__":
    unittest.main()
