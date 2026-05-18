"""Tests for photo_lib.exiftool_runner.is_metadata_in_sync.

The helper drives the "skip the write if EXIF already matches the filename"
optimization in Mode 1. It's pure logic over a metadata dict — no exiftool
subprocess needed — so the tests just feed in dicts and check the boolean.
"""

import unittest
from datetime import datetime, timezone, timedelta

from photo_lib.exiftool_runner import is_metadata_in_sync
from photo_lib.tag_modes import IMAGE_TAG_MODES, VIDEO_TAG_MODES


NZ_PLUS_13 = timezone(timedelta(hours=13))


class TestIsMetadataInSync(unittest.TestCase):
    def test_image_in_sync(self):
        dt = datetime(2024, 6, 15, 14, 30, 45)
        # Image tags are all 'local' mode — naive YYYY:MM:DD HH:MM:SS
        metadata = {
            "DateTimeOriginal": "2024:06:15 14:30:45",
            "CreateDate":       "2024:06:15 14:30:45",
            "DateCreated":      "2024:06:15",            # date-only round-trip — excluded
            "ModifyDate":       "2024:06:15 14:30:45",
            "FileCreateDate":   "2024:06:15 14:30:45+13:00",  # filesystem suffix stripped
            "FileModifyDate":   "2024:06:15 14:30:45+13:00",
        }
        self.assertTrue(is_metadata_in_sync(metadata, dt, NZ_PLUS_13, IMAGE_TAG_MODES))

    def test_image_out_of_sync_one_tag(self):
        dt = datetime(2024, 6, 15, 14, 30, 45)
        metadata = {
            "DateTimeOriginal": "2024:06:15 14:30:45",
            "CreateDate":       "2024:06:15 13:00:00",  # wrong
            "ModifyDate":       "2024:06:15 14:30:45",
            "FileCreateDate":   "2024:06:15 14:30:45",
            "FileModifyDate":   "2024:06:15 14:30:45",
        }
        self.assertFalse(is_metadata_in_sync(metadata, dt, NZ_PLUS_13, IMAGE_TAG_MODES))

    def test_image_missing_tag_returns_false(self):
        # Conservative: missing tag is treated as not-in-sync so the writer fills it in
        dt = datetime(2024, 6, 15, 14, 30, 45)
        metadata = {
            "DateTimeOriginal": "2024:06:15 14:30:45",
            # CreateDate missing
            "ModifyDate":       "2024:06:15 14:30:45",
            "FileCreateDate":   "2024:06:15 14:30:45",
            "FileModifyDate":   "2024:06:15 14:30:45",
        }
        self.assertFalse(is_metadata_in_sync(metadata, dt, NZ_PLUS_13, IMAGE_TAG_MODES))

    def test_video_in_sync(self):
        # Filename time 14:30:45 NZ = 01:30:45 UTC.
        # UTC tags → "2024:06:15 01:30:45"
        # CreationDate (local_with_tz) → "2024:06:15 14:30:45+13:00"
        # filesystem (local) → "2024:06:15 14:30:45"
        dt = datetime(2024, 6, 15, 14, 30, 45)
        metadata = {
            "MediaCreateDate":  "2024:06:15 01:30:45",
            "MediaModifyDate":  "2024:06:15 01:30:45",
            "TrackCreateDate":  "2024:06:15 01:30:45",
            "TrackModifyDate":  "2024:06:15 01:30:45",
            "CreateDate":       "2024:06:15 01:30:45",
            "ModifyDate":       "2024:06:15 01:30:45",
            "CreationDate":     "2024:06:15 14:30:45+13:00",
            "FileCreateDate":   "2024:06:15 14:30:45+13:00",
            "FileModifyDate":   "2024:06:15 14:30:45+13:00",
        }
        self.assertTrue(is_metadata_in_sync(metadata, dt, NZ_PLUS_13, VIDEO_TAG_MODES))

    def test_video_out_of_sync_creationdate(self):
        # Filename time matches UTC tags but CreationDate has wrong offset
        dt = datetime(2024, 6, 15, 14, 30, 45)
        metadata = {
            "MediaCreateDate":  "2024:06:15 01:30:45",
            "MediaModifyDate":  "2024:06:15 01:30:45",
            "TrackCreateDate":  "2024:06:15 01:30:45",
            "TrackModifyDate":  "2024:06:15 01:30:45",
            "CreateDate":       "2024:06:15 01:30:45",
            "ModifyDate":       "2024:06:15 01:30:45",
            "CreationDate":     "2024:06:15 14:30:45+01:00",  # wrong offset
            "FileCreateDate":   "2024:06:15 14:30:45",
            "FileModifyDate":   "2024:06:15 14:30:45",
        }
        self.assertFalse(is_metadata_in_sync(metadata, dt, NZ_PLUS_13, VIDEO_TAG_MODES))

    def test_date_created_excluded_from_check(self):
        # XMP DateCreated stored as date-only must not block the in-sync verdict
        dt = datetime(2024, 6, 15, 14, 30, 45)
        metadata = {
            "DateTimeOriginal": "2024:06:15 14:30:45",
            "CreateDate":       "2024:06:15 14:30:45",
            "DateCreated":      "2024:06:15",  # date-only (real exiftool behavior)
            "ModifyDate":       "2024:06:15 14:30:45",
            "FileCreateDate":   "2024:06:15 14:30:45",
            "FileModifyDate":   "2024:06:15 14:30:45",
        }
        self.assertTrue(is_metadata_in_sync(metadata, dt, NZ_PLUS_13, IMAGE_TAG_MODES))

    def test_filesystem_tag_tz_suffix_stripped(self):
        # Windows exiftool reports filesystem dates with a trailing offset.
        # The comparison must strip it before checking equality.
        dt = datetime(2024, 6, 15, 14, 30, 45)
        metadata = {
            "DateTimeOriginal": "2024:06:15 14:30:45",
            "CreateDate":       "2024:06:15 14:30:45",
            "ModifyDate":       "2024:06:15 14:30:45",
            "FileCreateDate":   "2024:06:15 14:30:45-08:00",  # negative offset
            "FileModifyDate":   "2024:06:15 14:30:45+13:00",
        }
        self.assertTrue(is_metadata_in_sync(metadata, dt, NZ_PLUS_13, IMAGE_TAG_MODES))


if __name__ == '__main__':
    unittest.main()
