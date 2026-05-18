"""Tests for photo_lib.timezone_detection: per-file TZ detection from EXIF metadata.

Covers the priority order detect_file_tz uses (CreationDate offset > OffsetTimeOriginal
> OffsetTime/Digitized > NZ fallback) and the fixed-offset parser.
"""

import os
import sys
import unittest
from datetime import timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

from photo_lib import timezone_detection  # noqa: E402


class TestDetectFileTz(unittest.TestCase):
    """detect_file_tz picks the best available TZ from a file's existing metadata.

    Priority order: Apple Keys CreationDate offset (most trustworthy for video),
    then image OffsetTime* tags, then DateTimeOriginal-embedded offset, then NZ fallback.
    """

    def test_uses_creation_date_offset_for_video(self):
        tz_detected = timezone_detection.detect_file_tz(
            {"CreationDate": "2026:04:09 19:52:51+10:00"}
        )
        self.assertEqual(tz_detected.utcoffset(None), timedelta(hours=10))

    def test_uses_offset_time_original_for_image(self):
        tz_detected = timezone_detection.detect_file_tz(
            {"OffsetTimeOriginal": "+02:00"}
        )
        self.assertEqual(tz_detected.utcoffset(None), timedelta(hours=2))

    def test_falls_back_to_pacific_auckland(self):
        # Empty metadata → NZ default. Comparing by identity is safe because
        # tz.gettz caches the same object for repeat calls with the same name.
        tz_detected = timezone_detection.detect_file_tz({})
        self.assertIs(tz_detected, timezone_detection.LOCAL_TIMEZONE)

    def test_prefers_creation_date_over_offset_time(self):
        # Both present and disagree → CreationDate wins (it's the more trustworthy
        # field on a video; OffsetTimeOriginal can be a garbage default).
        metadata = {
            "CreationDate": "2023:10:01 11:59:18+02:00",  # Paris
            "OffsetTimeOriginal": "+13:00",                # Some garbage NZ value
        }
        tz_detected = timezone_detection.detect_file_tz(metadata)
        self.assertEqual(tz_detected.utcoffset(None), timedelta(hours=2))

    def test_negative_offset_parsed(self):
        # US East coast EDT
        tz_detected = timezone_detection.detect_file_tz(
            {"OffsetTimeOriginal": "-04:00"}
        )
        self.assertEqual(tz_detected.utcoffset(None), timedelta(hours=-4))


class TestParseTzOffset(unittest.TestCase):
    """parse_tz_offset turns exiftool's +HH:MM / -HH:MM suffix into a fixed-offset
    datetime.timezone, or returns None on garbage input."""

    def test_positive_offset(self):
        offset = timezone_detection.parse_tz_offset("+05:30")
        self.assertEqual(offset.utcoffset(None), timedelta(hours=5, minutes=30))

    def test_negative_offset(self):
        offset = timezone_detection.parse_tz_offset("-08:00")
        self.assertEqual(offset.utcoffset(None), timedelta(hours=-8))

    def test_offset_embedded_in_datetime_string(self):
        # The function should find the offset suffix in a full datetime string
        offset = timezone_detection.parse_tz_offset("2026:04:09 19:52:51+10:00")
        self.assertEqual(offset.utcoffset(None), timedelta(hours=10))

    def test_no_offset_returns_none(self):
        self.assertIsNone(timezone_detection.parse_tz_offset(""))
        self.assertIsNone(timezone_detection.parse_tz_offset(None))
        self.assertIsNone(timezone_detection.parse_tz_offset("2026:04:09 19:52:51"))


if __name__ == '__main__':
    unittest.main()
