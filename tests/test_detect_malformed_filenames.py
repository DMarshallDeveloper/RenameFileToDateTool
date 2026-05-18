"""Tests for detect_malformed_filenames.py: regex that flags filenames not matching
the master library's ``YYYY-MM-DD HH.MM.SS_N.ext`` convention.

The script's only logic is the regex pattern; we test it directly via re.match so we
don't depend on file-system fixtures or printing.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

# The pattern itself lives in photo_lib; the script just imports and applies it.
# Test directly against the canonical pattern so behavior changes show up here.
from photo_lib.filename_pattern import CANONICAL_FILENAME_RE as PATTERN  # noqa: E402


class TestMalformedFilenamePattern(unittest.TestCase):
    """The script itself just walks a folder and prints filenames that don't match
    the canonical pattern. The interesting behaviour is the pattern, so these tests
    exercise it directly with positive (should-match) and negative (should-reject)
    examples covering the formats this codebase has produced over time."""

    def test_canonical_form_matches(self):
        self.assertIsNotNone(PATTERN.match('2026-04-09 19.52.51_1.jpg'))
        self.assertIsNotNone(PATTERN.match('2026-04-09 19.52.51_42.heic'))

    def test_four_char_extension_matches(self):
        self.assertIsNotNone(PATTERN.match('2026-04-09 19.52.51_1.heic'))
        # 5-char extension should fail (the regex limits to 3-4)
        self.assertIsNone(PATTERN.match('2026-04-09 19.52.51_1.jpeg5'))

    def test_underscore_in_time_part_rejected(self):
        # The script accepts a space between date and time, NOT an underscore
        self.assertIsNone(PATTERN.match('2026-04-09_19.52.51_1.jpg'))

    def test_hyphen_in_time_rejected(self):
        # Time uses dots, not hyphens
        self.assertIsNone(PATTERN.match('2026-04-09 19-52-51_1.jpg'))

    def test_missing_suffix_rejected(self):
        self.assertIsNone(PATTERN.match('2026-04-09 19.52.51.jpg'))

    def test_camera_style_name_rejected(self):
        self.assertIsNone(PATTERN.match('IMG_3118.JPG'))

    def test_no_extension_rejected(self):
        self.assertIsNone(PATTERN.match('2026-04-09 19.52.51_1'))


if __name__ == '__main__':
    unittest.main()
