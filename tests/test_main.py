"""Tests for main.py — the interactive photo renamer / EXIF rewriter.

Covers both directions of the workflow:
  - ``rename_photos``: read EXIF/QuickTime dates, rename file to YYYY-MM-DD HH.MM.SS_N.ext
  - ``change_exif_date``: read filename, write EXIF/QuickTime back to match
plus the overseas-photo (Melbourne) round trip and the Jan-1 placeholder bump,
which were the original motivation for the per-file TZ detection.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'RenameFileToDateTool'))

import main  # noqa: E402
from tests._fixture_helpers import (  # noqa: E402
    copy_fixture_image,
    copy_fixture_video,
    make_image_with_tz,
    make_video_with_tz,
    read_exif_tag,
)


class TestRenamePhotos(unittest.TestCase):
    """rename_photos: pull date from EXIF, rename file to canonical form. Image
    EXIF is local naive; video QuickTime UTC tags get converted using the photo's
    detected TZ so an overseas video keeps its on-camera local time."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_rename_')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_image_rename_uses_local_exif_time(self):
        # Fixture image has DateTimeOriginal=2026:01:15 14:30:45 (local NZ)
        copy_fixture_image(self.tmpdir, name='IMG_random.jpg')
        main.rename_photos(self.tmpdir)
        files = os.listdir(self.tmpdir)
        self.assertIn('2026-01-15 14.30.45_1.jpg', files,
                      f"expected renamed image, got: {files}")

    def test_video_rename_converts_utc_to_local(self):
        # Fixture video has MediaCreateDate=2026:01:15 01:30:45 (UTC); NZ January = UTC+13
        # so the local time is 2026-01-15 14:30:45 and the filename should reflect that.
        copy_fixture_video(self.tmpdir, name='VID_random.mov')
        main.rename_photos(self.tmpdir)
        files = os.listdir(self.tmpdir)
        self.assertIn('2026-01-15 14.30.45_1.mov', files,
                      f"expected video renamed to local time, got: {files}")

    def test_rerun_idempotent(self):
        # Running rename twice on the same folder should not produce duplicate _N files.
        copy_fixture_image(self.tmpdir, name='IMG_random.jpg')
        main.rename_photos(self.tmpdir)
        files_after_first = sorted(os.listdir(self.tmpdir))
        main.rename_photos(self.tmpdir)
        files_after_second = sorted(os.listdir(self.tmpdir))
        self.assertEqual(files_after_first, files_after_second,
                         "second rename pass should not duplicate or rename anything")

    def test_two_files_same_timestamp_get_distinct_suffixes(self):
        copy_fixture_image(self.tmpdir, name='IMG_a.jpg')
        copy_fixture_image(self.tmpdir, name='IMG_b.jpg')
        main.rename_photos(self.tmpdir)
        files = sorted(os.listdir(self.tmpdir))
        self.assertEqual(
            files,
            ['2026-01-15 14.30.45_1.jpg', '2026-01-15 14.30.45_2.jpg'],
        )


class TestChangeExifDate(unittest.TestCase):
    """change_exif_date: pull date from filename, write to image EXIF as local
    naive and to video QuickTime as UTC. NZ DST boundary cases included since
    NZ flips between NZST (+12) and NZDT (+13) in early April/late September."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_change_exif_')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_image_writes_exif_to_match_filename(self):
        # Use a filename with a deliberately different date so we know the write happened.
        path = copy_fixture_image(self.tmpdir, name='2026-03-20 10.15.30_1.jpg')
        main.change_exif_date(self.tmpdir)
        self.assertEqual(read_exif_tag(path, 'DateTimeOriginal'), '2026:03:20 10:15:30')
        self.assertEqual(read_exif_tag(path, 'CreateDate'), '2026:03:20 10:15:30')

    def test_video_writes_exif_in_utc(self):
        # 2026-03-20 10:15:30 NZ NZDT (still in DST in March) → 2026-03-19 21:15:30 UTC
        path = copy_fixture_video(self.tmpdir, name='2026-03-20 10.15.30_1.mov')
        main.change_exif_date(self.tmpdir)
        self.assertEqual(read_exif_tag(path, 'MediaCreateDate'), '2026:03:19 21:15:30')
        self.assertEqual(read_exif_tag(path, 'TrackCreateDate'), '2026:03:19 21:15:30')

    def test_video_round_trip(self):
        # After writing EXIF from filename, renaming should produce the same filename.
        copy_fixture_video(self.tmpdir, name='2026-07-10 09.05.20_1.mov')
        main.change_exif_date(self.tmpdir)
        main.rename_photos(self.tmpdir)
        files = os.listdir(self.tmpdir)
        self.assertIn('2026-07-10 09.05.20_1.mov', files,
                      f"round-trip failed, files: {files}")

    def test_image_writes_filesystem_dates(self):
        # FileCreateDate / FileModifyDate should be set to the filename's local time.
        # exiftool reports these as local-with-TZ — we check the YYYY:MM:DD HH:MM:SS portion.
        path = copy_fixture_image(self.tmpdir, name='2026-03-20 10.15.30_1.jpg')
        main.change_exif_date(self.tmpdir)
        self.assertTrue(read_exif_tag(path, 'FileModifyDate').startswith('2026:03:20 10:15:30'))
        self.assertTrue(read_exif_tag(path, 'FileCreateDate').startswith('2026:03:20 10:15:30'))

    def test_video_writes_filesystem_dates(self):
        path = copy_fixture_video(self.tmpdir, name='2026-03-20 10.15.30_1.mov')
        main.change_exif_date(self.tmpdir)
        self.assertTrue(read_exif_tag(path, 'FileModifyDate').startswith('2026:03:20 10:15:30'))
        self.assertTrue(read_exif_tag(path, 'FileCreateDate').startswith('2026:03:20 10:15:30'))

    def test_video_writes_apple_creation_date_with_tz(self):
        # CreationDate is the Apple Keys atom — local time with explicit TZ offset.
        # March 20 in NZ is still NZDT (UTC+13).
        path = copy_fixture_video(self.tmpdir, name='2026-03-20 10.15.30_1.mov')
        main.change_exif_date(self.tmpdir)
        self.assertEqual(read_exif_tag(path, 'CreationDate'), '2026:03:20 10:15:30+13:00')

    def test_video_writes_apple_creation_date_winter_tz(self):
        # July in NZ is NZST (UTC+12)
        path = copy_fixture_video(self.tmpdir, name='2026-07-10 09.05.20_1.mov')
        main.change_exif_date(self.tmpdir)
        self.assertEqual(read_exif_tag(path, 'CreationDate'), '2026:07:10 09:05:20+12:00')


class TestOverseasPhotoEndToEnd(unittest.TestCase):
    """End-to-end for the original 'photos taken overseas' problem: a Melbourne
    photo (+10:00) round-trips through change_exif_date + rename_photos with the
    filename time preserved, instead of being NZ-shifted."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_overseas_')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_melbourne_image_uses_detected_tz_for_filesystem(self):
        # Image taken in Melbourne (AEST +10:00). Filename time is Melbourne local.
        # Write should preserve image EXIF as filename time; filesystem dates also use filename time.
        path = make_image_with_tz(
            self.tmpdir, '2026-04-09 19.52.51_1.jpg',
            datetime_local='2026:04:09 19:52:51', offset='+10:00')
        main.change_exif_date(self.tmpdir)
        self.assertEqual(read_exif_tag(path, 'DateTimeOriginal'), '2026:04:09 19:52:51')

    def test_melbourne_video_writes_utc_using_detected_tz(self):
        # Video shot in Melbourne (AEST +10:00). Filename = local Melbourne time.
        # MediaCreateDate must be UTC of the actual moment — 19:52 AEST = 09:52 UTC.
        path = make_video_with_tz(
            self.tmpdir, '2026-04-09 19.52.51_1.mov',
            datetime_utc='2026:04:09 09:52:51',
            datetime_local='2026:04:09 19:52:51',
            offset='+10:00')
        main.change_exif_date(self.tmpdir)
        self.assertEqual(read_exif_tag(path, 'MediaCreateDate'), '2026:04:09 09:52:51')
        self.assertEqual(read_exif_tag(path, 'CreationDate'), '2026:04:09 19:52:51+10:00')

    def test_melbourne_video_round_trip_preserves_filename(self):
        # After writing with detected TZ, renaming should produce the same filename.
        make_video_with_tz(
            self.tmpdir, '2026-04-09 19.52.51_1.mov',
            datetime_utc='2026:04:09 09:52:51',
            datetime_local='2026:04:09 19:52:51',
            offset='+10:00')
        main.change_exif_date(self.tmpdir)
        main.rename_photos(self.tmpdir)
        files = os.listdir(self.tmpdir)
        self.assertIn('2026-04-09 19.52.51_1.mov', files,
                      f"Round-trip failed for Melbourne video, files: {files}")

    def test_no_tz_info_falls_back_to_nz(self):
        # Plain fixture (no embedded TZ) → fall back to NZ. Behaves as before.
        copy_fixture_image(self.tmpdir, name='2026-01-15 14.30.45_1.jpg')
        main.change_exif_date(self.tmpdir)
        path = os.path.join(self.tmpdir, '2026-01-15 14.30.45_1.jpg')
        self.assertEqual(read_exif_tag(path, 'DateTimeOriginal'), '2026:01:15 14:30:45')

    def test_placeholder_midnight_bumped_in_exif_and_filename_renamed(self):
        # Filename is Jan 1 midnight — EXIF should be written with 13:00 AND the
        # file renamed to 13.00.00 so filename ≡ EXIF.
        copy_fixture_image(self.tmpdir, name='2000-01-01 00.00.00_1.jpg')
        main.change_exif_date(self.tmpdir)

        old_path = os.path.join(self.tmpdir, '2000-01-01 00.00.00_1.jpg')
        new_path = os.path.join(self.tmpdir, '2000-01-01 13.00.00_1.jpg')
        self.assertFalse(os.path.exists(old_path),
                         "Placeholder file should have been renamed away from 00.00.00")
        self.assertTrue(os.path.exists(new_path),
                        "Placeholder file should have been renamed to 13.00.00")
        self.assertEqual(read_exif_tag(new_path, 'DateTimeOriginal'), '2000:01:01 13:00:00')


class TestMainAcceptsAllCanonicalExtensions(unittest.TestCase):
    """Regression for Bug A: main.py used to define its own narrow extension list
    that excluded heif/3gp/m4v even though every other module accepted them. Files
    in those formats were silently rejected with 'Invalid file type'. The canonical
    photo_lib.extensions sets now drive every module."""

    def test_heif_accepted(self):
        self.assertIn('heif', main.IMAGE_FILE_EXTENSIONS)

    def test_3gp_accepted(self):
        self.assertIn('3gp', main.VIDEO_FILE_EXTENSIONS)

    def test_m4v_accepted(self):
        self.assertIn('m4v', main.VIDEO_FILE_EXTENSIONS)


if __name__ == '__main__':
    unittest.main()
