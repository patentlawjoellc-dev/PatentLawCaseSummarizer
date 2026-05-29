"""Unit tests for the pure shared helpers. Run: python -m unittest scripts.common.test_common
(or from scripts/: python -m unittest common.test_common)."""
import unittest

from common.jsonutil import strip_code_fences, parse_llm_json
from common.tagutil import normalize_tags


class TestJsonUtil(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(parse_llm_json('{"a": 1}'), {"a": 1})

    def test_fenced_json_lang(self):
        raw = '```json\n{"a": 1, "b": [2, 3]}\n```'
        self.assertEqual(parse_llm_json(raw), {"a": 1, "b": [2, 3]})

    def test_fenced_json_bare(self):
        raw = '```\n{"x": "y"}\n```'
        self.assertEqual(parse_llm_json(raw), {"x": "y"})

    def test_strip_only(self):
        self.assertEqual(strip_code_fences('```json\n{}\n```'), '{}')

    def test_whitespace_padding(self):
        self.assertEqual(parse_llm_json('   {"a": 1}   '), {"a": 1})


class TestTagUtil(unittest.TestCase):
    def test_dedup_case_insensitive(self):
        self.assertEqual(normalize_tags(["§ 101", "§ 101", "§ 101 "]), ["§ 101"])

    def test_collapse_whitespace(self):
        self.assertEqual(normalize_tags(["claim   construction"]), ["claim construction"])

    def test_drop_empties_and_none(self):
        self.assertEqual(normalize_tags(["", "  ", "x"]), ["x"])
        self.assertEqual(normalize_tags(None), [])

    def test_order_preserved(self):
        self.assertEqual(normalize_tags(["b", "a", "B"]), ["b", "a"])


if __name__ == "__main__":
    unittest.main()
