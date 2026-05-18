"""Tests for write_exif_from_filename.py: filename → EXIF, recursive walk,
image/video split."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'RenameFileToDateTool'))

import write_exif_from_filename  # noqa: E402
from photo_lib.filename_pattern import parse_filename_datetime  # noqa: E402
from tests._fixture_helpers import (  # noqa: E402
    copy_fixture_image,
    copy_fixture_video,
    read_exif_tag,
)


class TestParseFilenameDatetime(unittest.TestCase):
    """The recursive writer relies on parse_filename_datetime to decide which
    files are candidates. These tests guard the formats the writer needs to
    accept: canonical HH.MM.SS, older HH-MM-SS and HH.MM-without-seconds."""

    def test_space_dot_format(self):
        parsed_date_time = parse_filename_datetime('2026-04-12 09.15.30_1.jpg')
        self.assertIsNotNone(parsed_date_time)
        self.assertEqual(parsed_date_time.strftime('%Y-%m-%d %H:%M:%S'),
                         '2026-04-12 09:15:30')

    def test_underscore_hyphen_format(self):
        # Older takeout-script format
        parsed_date_time = parse_filename_datetime('2026-04-12_09-15-30.jpg')
        self.assertIsNotNone(parsed_date_time)
        self.assertEqual(parsed_date_time.strftime('%Y-%m-%d %H:%M:%S'),
                         '2026-04-12 09:15:30')

    def test_no_date_returns_none(self):
        self.assertIsNone(parse_filename_datetime('IMG_random.jpg'))

    def test_hh_mm_no_seconds_accepted(self):
        # Regression: the older recursive script used to require HH.MM.SS, but
        # rename_files_from_exif accepted HH.MM (seconds default to 0). Both
        # now go through photo_lib.filename_pattern.parse_filename_datetime
        # which handles either form.
        parsed_date_time = parse_filename_datetime('2026-04-09 19.52_1.jpg')
        self.assertIsNotNone(parsed_date_time)
        self.assertEqual(parsed_date_time.strftime('%Y-%m-%d %H:%M:%S'),
                         '2026-04-09 19:52:00')


class TestChangeExifDate(unittest.TestCase):
    """change_exif_date is the main entry point: recursively walk a folder,
    parse each filename's date, and write that date back into the
    EXIF/QuickTime metadata. These tests verify the image vs video split, the
    recursive walk, that filesystem dates also get updated, and that the
    Apple Keys CreationDate gets its TZ offset written correctly (a Windows-
    display gotcha that took a while to get right)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_write_exif_')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_writes_image_exif(self):
        path = copy_fixture_image(self.tmpdir, name='2026-05-01 13.45.20_1.jpg')
        write_exif_from_filename.change_exif_date(self.tmpdir)
        self.assertEqual(read_exif_tag(path, 'DateTimeOriginal'), '2026:05:01 13:45:20')

    def test_writes_video_exif_in_utc(self):
        path = copy_fixture_video(self.tmpdir, name='2026-05-01 13.45.20_1.mov')
        write_exif_from_filename.change_exif_date(self.tmpdir)
        # May in NZ is NZST (UTC+12 — DST ends early April)
        # 13:45:20 NZST → 01:45:20 UTC same day
        self.assertEqual(read_exif_tag(path, 'MediaCreateDate'), '2026:05:01 01:45:20')

    def test_recursive_walk(self):
        sub = os.path.join(self.tmpdir, 'sub')
        os.makedirs(sub)
        path = copy_fixture_image(sub, name='2026-05-01 13.45.20_1.jpg')
        write_exif_from_filename.change_exif_date(self.tmpdir)  # called on parent dir
        self.assertEqual(read_exif_tag(path, 'DateTimeOriginal'), '2026:05:01 13:45:20')

    def test_image_writes_filesystem_dates(self):
        path = copy_fixture_image(self.tmpdir, name='2026-05-01 13.45.20_1.jpg')
        write_exif_from_filename.change_exif_date(self.tmpdir)
        self.assertTrue(read_exif_tag(path, 'FileModifyDate').startswith('2026:05:01 13:45:20'))
        self.assertTrue(read_exif_tag(path, 'FileCreateDate').startswith('2026:05:01 13:45:20'))

    def test_video_writes_apple_creation_date(self):
        path = copy_fixture_video(self.tmpdir, name='2026-05-01 13.45.20_1.mov')
        write_exif_from_filename.change_exif_date(self.tmpdir)
        # May 1 in NZ is NZST (UTC+12) — DST ended early April
        self.assertEqual(read_exif_tag(path, 'CreationDate'), '2026:05:01 13:45:20+12:00')


if __name__ == '__main__':
    unittest.main()
