"""Tests for audit_master.py: ensure the audit reports correctly for overseas photos.

The audit must not flag a correctly-stored overseas video as "bad" just because its
TZ offset differs from NZ. It should use the file's detected TZ when computing
expected UTC and TZ-tagged strings.
"""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

import audit_master  # noqa: E402
from tests._fixture_helpers import (  # noqa: E402
    copy_fixture_image,
    make_video_with_tz,
    read_exif_tag,
)


def _all_checks_match(checks):
    return all(ok for *_x, ok in checks)


class TestCheckFileTZAware(unittest.TestCase):
    """check_file is the per-file comparison: given a filename + metadata + expected
    datetime, return per-tag (expected, actual, ok) tuples. These tests verify the
    audit honors per-file TZ — an overseas video that's correctly tagged with its
    real TZ offset must NOT be flagged just because the offset isn't NZ."""

    def test_melbourne_video_with_creationdate_offset_passes_audit(self):
        # Simulate metadata as exiftool would emit it for a correctly-stored Melbourne video.
        # All 6 UTC tags must be set — the audit derives its tag list from VIDEO_TAG_MODES
        # which includes the Media/Track *Modify* tags too (main.py writes them).
        metadata = {
            "MediaCreateDate": "2026:04:09 09:52:51",     # UTC of capture
            "MediaModifyDate": "2026:04:09 09:52:51",
            "TrackCreateDate": "2026:04:09 09:52:51",
            "TrackModifyDate": "2026:04:09 09:52:51",
            "CreateDate": "2026:04:09 09:52:51",
            "ModifyDate": "2026:04:09 09:52:51",
            "CreationDate": "2026:04:09 19:52:51+10:00",  # local + offset
            "FileCreateDate": "2026:04:09 19:52:51+10:00",
            "FileModifyDate": "2026:04:09 19:52:51+10:00",
        }
        expected = datetime(2026, 4, 9, 19, 52, 51)
        checks = audit_master.check_file("2026-04-09 19.52.51_1.mov", metadata,
                                         expected, is_video=True)
        self.assertTrue(
            _all_checks_match(checks),
            "Overseas video stored with its true TZ should NOT be flagged. "
            f"Failures: {[c for c in checks if not c[3]]}",
        )

    def test_nz_video_without_offset_still_passes_audit(self):
        # No TZ hint anywhere → detect_file_tz falls back to NZ → audit uses NZ.
        # 14:30:45 NZDT (UTC+13 in Jan) → 01:30:45 UTC.
        metadata = {
            "MediaCreateDate": "2026:01:15 01:30:45",
            "MediaModifyDate": "2026:01:15 01:30:45",
            "TrackCreateDate": "2026:01:15 01:30:45",
            "TrackModifyDate": "2026:01:15 01:30:45",
            "CreateDate": "2026:01:15 01:30:45",
            "ModifyDate": "2026:01:15 01:30:45",
            "CreationDate": "2026:01:15 14:30:45+13:00",
            "FileCreateDate": "2026:01:15 14:30:45+13:00",
            "FileModifyDate": "2026:01:15 14:30:45+13:00",
        }
        expected = datetime(2026, 1, 15, 14, 30, 45)
        checks = audit_master.check_file("2026-01-15 14.30.45_1.mov", metadata,
                                         expected, is_video=True)
        self.assertTrue(
            _all_checks_match(checks),
            f"Failures: {[c for c in checks if not c[3]]}",
        )

    def test_corrupted_overseas_video_is_flagged(self):
        # Video has CreationDate saying +10:00 but the UTC tags were written using
        # NZ TZ by mistake → audit should flag the mismatch.
        metadata = {
            "MediaCreateDate": "2026:04:09 06:52:51",     # WRONG: NZ-derived UTC
            "MediaModifyDate": "2026:04:09 06:52:51",
            "TrackCreateDate": "2026:04:09 06:52:51",
            "TrackModifyDate": "2026:04:09 06:52:51",
            "CreateDate": "2026:04:09 06:52:51",
            "ModifyDate": "2026:04:09 06:52:51",
            "CreationDate": "2026:04:09 19:52:51+10:00",
            "FileCreateDate": "2026:04:09 19:52:51+10:00",
            "FileModifyDate": "2026:04:09 19:52:51+10:00",
        }
        expected = datetime(2026, 4, 9, 19, 52, 51)
        checks = audit_master.check_file("2026-04-09 19.52.51_1.mov", metadata,
                                         expected, is_video=True)
        self.assertFalse(
            _all_checks_match(checks),
            "Audit must flag the mismatched UTC tags when CreationDate offset disagrees",
        )

    def test_overseas_image_with_offsettimeoriginal_passes(self):
        # Image taken in Melbourne — DateTimeOriginal is local (naive), TZ is in OffsetTimeOriginal.
        metadata = {
            "DateTimeOriginal": "2026:04:09 19:52:51",
            "CreateDate": "2026:04:09 19:52:51",
            "ModifyDate": "2026:04:09 19:52:51",
            "OffsetTimeOriginal": "+10:00",
            "FileCreateDate": "2026:04:09 19:52:51+10:00",
            "FileModifyDate": "2026:04:09 19:52:51+10:00",
        }
        expected = datetime(2026, 4, 9, 19, 52, 51)
        checks = audit_master.check_file("2026-04-09 19.52.51_1.jpg", metadata,
                                         expected, is_video=False)
        self.assertTrue(
            _all_checks_match(checks),
            f"Failures: {[c for c in checks if not c[3]]}",
        )


class TestCheckFileEndToEnd(unittest.TestCase):
    """Sanity check that a real overseas video, written by main.change_exif_date and
    then re-read with exiftool, audits clean."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_audit_e2e_')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_melbourne_video_round_trip_audits_clean(self):
        import main
        from photo_lib.exiftool_runner import get_metadata_for_tags
        path = make_video_with_tz(
            self.tmpdir, '2026-04-09 19.52.51_1.mov',
            datetime_utc='2026:04:09 09:52:51',
            datetime_local='2026:04:09 19:52:51',
            offset='+10:00')
        main.change_exif_date(self.tmpdir)

        all_tags = (audit_master.IMAGE_LOCAL_TAGS + audit_master.VIDEO_UTC_TAGS
                    + audit_master.VIDEO_TZ_TAGS + audit_master.FILESYSTEM_TAGS
                    + audit_master.TZ_HINT_TAGS)
        metadata = get_metadata_for_tags([path], all_tags)[0]

        expected = datetime(2026, 4, 9, 19, 52, 51)
        checks = audit_master.check_file('2026-04-09 19.52.51_1.mov', metadata,
                                         expected, is_video=True)
        self.assertTrue(
            _all_checks_match(checks),
            f"Round-trip overseas video audited as bad. Failures: "
            f"{[c for c in checks if not c[3]]}",
        )


class TestAuditAppliesPlaceholderBump(unittest.TestCase):
    """Regression for Bug B: audit_master used to compare filename time to EXIF
    directly, ignoring main.py's Jan-1 → 13:00 placeholder bump. So a file named
    ``2000-01-01 00.00.00_1.jpg`` written *correctly* by main.py was reported as
    NEEDS FIX (since its EXIF says 13:00 but the filename says 00:00). The fix:
    check_file applies the same bump before computing expected values."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_audit_bump_')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_placeholder_file_audits_clean_after_main_writes_it(self):
        from photo_lib.exiftool_runner import get_metadata_for_tags
        import main

        path = copy_fixture_image(self.tmpdir, name='2000-01-01 00.00.00_1.jpg')
        main.change_exif_date(self.tmpdir)

        all_tags = (audit_master.IMAGE_LOCAL_TAGS + audit_master.VIDEO_UTC_TAGS
                    + audit_master.VIDEO_TZ_TAGS + audit_master.FILESYSTEM_TAGS
                    + audit_master.TZ_HINT_TAGS)
        metadata = get_metadata_for_tags([path], all_tags)[0]

        expected = audit_master.parse_filename_datetime('2000-01-01 00.00.00_1.jpg')
        checks = audit_master.check_file('2000-01-01 00.00.00_1.jpg', metadata,
                                         expected, is_video=False)
        self.assertTrue(
            all(ok for *_x, ok in checks),
            f"Placeholder-bumped file flagged as bad. Mismatches: "
            f"{[c for c in checks if not c[3]]}",
        )


class TestAuditMainE2E(unittest.TestCase):
    """Drive ``audit_master.main()`` over a tiny master-tree fixture so the verdict
    assembly (per-folder OK vs NEEDS FIX) gets exercised, not just check_file."""

    def setUp(self):
        self.master = tempfile.mkdtemp(prefix='test_audit_main_')

    def tearDown(self):
        shutil.rmtree(self.master, ignore_errors=True)

    def _seed_year(self, year, files):
        year_dir = os.path.join(self.master, str(year))
        os.makedirs(year_dir, exist_ok=True)
        paths = []
        for name, builder in files:
            path = builder(year_dir, name)
            paths.append(path)
        return paths

    def test_clean_tree_reports_ok(self):
        # Image, written correctly by main.change_exif_date — should audit clean.
        import main
        from tests._fixture_helpers import copy_fixture_image
        self._seed_year(2026, [('2026-03-20 10.15.30_1.jpg', copy_fixture_image)])
        main.change_exif_date(os.path.join(self.master, '2026'))

        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            audit_master.main(master_root=self.master)
        output = buf.getvalue()
        self.assertIn('2026  [OK]', output)
        self.assertNotIn('[NEEDS FIX]', output)

    def test_corrupted_tree_reports_needs_fix(self):
        # Place a file with a filename that disagrees with its EXIF (file was renamed
        # but EXIF was never rewritten). Audit should flag it.
        from tests._fixture_helpers import copy_fixture_image
        # Fixture has EXIF DateTimeOriginal=2026:01:15 14:30:45 but we rename to a 2027 date
        os.makedirs(os.path.join(self.master, '2027'), exist_ok=True)
        copy_fixture_image(os.path.join(self.master, '2027'), '2027-06-15 11.22.33_1.jpg')

        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            audit_master.main(master_root=self.master)
        output = buf.getvalue()
        self.assertIn('2027  [NEEDS FIX]', output)


if __name__ == '__main__':
    unittest.main()
