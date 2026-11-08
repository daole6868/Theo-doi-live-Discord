"""Kiểm thử đọc giờ người dùng nhập và cắt theo ngày."""
import unittest

import tests  # noqa: F401

from helpers.time_utils import parse_hhmm


class ParseHHMMTests(unittest.TestCase):
    def test_accepted(self):
        cases = {
            "20:00": (20, 0), "20.00": (20, 0), "20h00": (20, 0), "20h30": (20, 30),
            "20h": (20, 0), "20H": (20, 0), "20": (20, 0), "8:5": (8, 5), "8h": (8, 0),
            " 08:30 ": (8, 30), "2030": (20, 30), "0005": (0, 5),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_hhmm(text), expected)

    def test_rejected(self):
        for text in ["", "abc", "24:00", "20:60", "20:00:00", "-1", "123"]:
            with self.subTest(text=text):
                self.assertIsNone(parse_hhmm(text))


if __name__ == "__main__":
    unittest.main()
