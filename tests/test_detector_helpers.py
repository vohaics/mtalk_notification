"""Tests for the pure-Python helpers in src.detector.

Run with:  python -m unittest tests.test_detector_helpers
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.detector import (  # noqa: E402
    _extract_count_from_text,
    _is_small_integer,
)


class ExtractCountFromTextTest(unittest.TestCase):
    def test_parenthesised_suffix(self) -> None:
        self.assertEqual(_extract_count_from_text("NOC Internal (Handover) (3)"), 3)
        self.assertEqual(_extract_count_from_text("GNMC (12)"), 12)

    def test_bare_trailing_number(self) -> None:
        self.assertEqual(_extract_count_from_text("GNMC 5"), 5)
        self.assertEqual(_extract_count_from_text("Room name 99+"), 99)

    def test_no_count(self) -> None:
        self.assertIsNone(_extract_count_from_text("NOC Internal (Handover)"))
        self.assertIsNone(_extract_count_from_text("Just a plain room"))

    def test_embedded_year_or_time_not_matched_as_count(self) -> None:
        # Trailing "10:30" should not be misread as a count suffix.
        self.assertIsNone(_extract_count_from_text("GNMC last seen 10:30"))


class IsSmallIntegerTest(unittest.TestCase):
    def test_ok(self) -> None:
        for s in ("1", "9", "42", "999", "1234", " 7 ", "9+"):
            self.assertTrue(_is_small_integer(s), s)

    def test_bad(self) -> None:
        for s in ("", "abc", "12a", "12345", "1.5", "-1"):
            self.assertFalse(_is_small_integer(s), s)


if __name__ == "__main__":
    unittest.main()
