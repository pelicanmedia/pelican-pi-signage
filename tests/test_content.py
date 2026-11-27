"""Run with:  python3 -m unittest discover -s tests"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from content import OptionsError, dump_options, normalize_options, parse_options  # noqa: E402


class OptionsTests(unittest.TestCase):
    def test_parse_bad_data_is_empty(self):
        self.assertEqual(parse_options(None), {})
        self.assertEqual(parse_options("not json"), {})
        self.assertEqual(parse_options("[1, 2]"), {})

    def test_dump_drops_empty(self):
        self.assertIsNone(dump_options({}))
        self.assertIsNone(dump_options({"refresh_seconds": None}))
        self.assertEqual(dump_options({"refresh_seconds": 60}), '{"refresh_seconds": 60}')

    def test_missing_options_keep_current(self):
        current = '{"refresh_seconds": 60, "source_url": "https://x"}'
        self.assertEqual(parse_options(normalize_options({"name": "x"}, current)),
                         {"refresh_seconds": 60, "source_url": "https://x"})

    def test_set_and_clear_refresh(self):
        self.assertEqual(parse_options(normalize_options({"options": {"refresh_seconds": "300"}})),
                         {"refresh_seconds": 300})
        current = '{"refresh_seconds": 60, "source_url": "https://x"}'
        for blank in (None, "", 0):
            with self.subTest(blank=blank):
                out = parse_options(normalize_options({"options": {"refresh_seconds": blank}}, current))
                self.assertEqual(out, {"source_url": "https://x"})

    def test_source_url_not_user_editable(self):
        out = normalize_options({"options": {"source_url": "https://evil"}}, None)
        self.assertIsNone(out)

    def test_rejects_bad_refresh(self):
        for bad in ({"refresh_seconds": 5}, {"refresh_seconds": 999999}, {"refresh_seconds": "soon"}):
            with self.subTest(bad=bad), self.assertRaises(OptionsError):
                normalize_options({"options": bad})
        with self.assertRaises(OptionsError):
            normalize_options({"options": "nope"})


if __name__ == "__main__":
    unittest.main()
