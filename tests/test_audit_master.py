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
        # which includes the Media/Track *Modify* tags too (the writer writes them).
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
    """Sanity check that a real overseas video, written by write_exif_from_filename.change_exif_date and
    then re-read with exiftool, audits clean."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_audit_e2e_')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_melbourne_video_round_trip_audits_clean(self):
        import write_exif_from_filename
        from photo_lib.exiftool_runner import get_metadata_for_tags
        path = make_video_with_tz(
            self.tmpdir, '2026-04-09 19.52.51_1.mov',
            datetime_utc='2026:04:09 09:52:51',
            datetime_local='2026:04:09 19:52:51',
            offset='+10:00')
        write_exif_from_filename.change_exif_date(self.tmpdir)

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
        # write_exif_from_filename.change_exif_date both bumps EXIF to 13:00 AND renames the file to
        # 13.00.00 so the filename ≡ EXIF invariant holds. The audit should then
        # find the renamed file clean — its filename time matches its EXIF directly,
        # no placeholder-bump compensation needed.
        from photo_lib.exiftool_runner import get_metadata_for_tags
        import write_exif_from_filename

        copy_fixture_image(self.tmpdir, name='2000-01-01 00.00.00_1.jpg')
        write_exif_from_filename.change_exif_date(self.tmpdir)

        renamed_path = os.path.join(self.tmpdir, '2000-01-01 13.00.00_1.jpg')
        self.assertTrue(os.path.exists(renamed_path),
                        "write_exif_from_filename.change_exif_date should rename the placeholder file")

        all_tags = (audit_master.IMAGE_LOCAL_TAGS + audit_master.VIDEO_UTC_TAGS
                    + audit_master.VIDEO_TZ_TAGS + audit_master.FILESYSTEM_TAGS
                    + audit_master.TZ_HINT_TAGS)
        metadata = get_metadata_for_tags([renamed_path], all_tags)[0]

        expected = audit_master.parse_filename_datetime('2000-01-01 13.00.00_1.jpg')
        checks = audit_master.check_file('2000-01-01 13.00.00_1.jpg', metadata,
                                         expected, is_video=False)
        self.assertTrue(
            all(ok for *_x, ok in checks),
            f"Renamed placeholder file flagged as bad. Mismatches: "
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
        # Image, written correctly by write_exif_from_filename.change_exif_date — should audit clean.
        import write_exif_from_filename
        from tests._fixture_helpers import copy_fixture_image
        self._seed_year(2026, [('2026-03-20 10.15.30_1.jpg', copy_fixture_image)])
        write_exif_from_filename.change_exif_date(os.path.join(self.master, '2026'))

        with self.assertLogs('photo_lib', level='INFO') as cm:
            audit_master.main(master_root=self.master)
        output = "\n".join(r.getMessage() for r in cm.records)
        self.assertIn('2026  [OK]', output)
        self.assertNotIn('[NEEDS FIX]', output)

    def test_corrupted_tree_reports_needs_fix(self):
        # Place a file with a filename that disagrees with its EXIF (file was renamed
        # but EXIF was never rewritten). Audit should flag it.
        from tests._fixture_helpers import copy_fixture_image
        # Fixture has EXIF DateTimeOriginal=2026:01:15 14:30:45 but we rename to a 2027 date
        os.makedirs(os.path.join(self.master, '2027'), exist_ok=True)
        copy_fixture_image(os.path.join(self.master, '2027'), '2027-06-15 11.22.33_1.jpg')

        with self.assertLogs('photo_lib', level='INFO') as cm:
            audit_master.main(master_root=self.master)
        output = "\n".join(r.getMessage() for r in cm.records)
        self.assertIn('2027  [NEEDS FIX]', output)


class TestStructuralChecks(unittest.TestCase):
    """The structural checks walk every file (no sampling) and flag problems
    independent of EXIF date drift: wrong extension, wrong year folder, names
    that don't match the canonical pattern."""

    def setUp(self):
        self.master = tempfile.mkdtemp(prefix='test_audit_structural_')

    def tearDown(self):
        shutil.rmtree(self.master, ignore_errors=True)

    def _make_file(self, year, name, content=b'fake'):
        folder = os.path.join(self.master, year)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, 'wb') as f:
            f.write(content)
        return path

    def test_extension_mismatch_detected(self):
        # Drop a real JPEG fixture under a .heic name — exiftool should report
        # the actual format is jpg, not heic.
        os.makedirs(os.path.join(self.master, '2024'), exist_ok=True)
        copy_fixture_image(os.path.join(self.master, '2024'),
                           name='2024-06-15 14.30.00_1.heic')
        mismatches = audit_master.check_extension_mismatches(['2024'], self.master)
        self.assertEqual(len(mismatches), 1)
        folder, fname, claimed, actual = mismatches[0]
        self.assertEqual(folder, '2024')
        self.assertEqual(claimed, 'heic')
        self.assertEqual(actual, 'jpg')

    def test_extension_match_not_flagged(self):
        # Real JPEG with a .jpg name → no mismatch.
        os.makedirs(os.path.join(self.master, '2024'), exist_ok=True)
        copy_fixture_image(os.path.join(self.master, '2024'),
                           name='2024-06-15 14.30.00_1.jpg')
        self.assertEqual(audit_master.check_extension_mismatches(['2024'], self.master), [])

    def test_year_folder_mismatch_detected(self):
        # 2023-dated file living in the 2024 folder
        self._make_file('2024', '2023-12-15 10.00.00_1.jpg')
        bad = audit_master.check_year_folder_mismatches(['2024'], self.master)
        self.assertEqual(len(bad), 1)
        folder, fname, file_year = bad[0]
        self.assertEqual(folder, '2024')
        self.assertEqual(file_year, 2023)

    def test_year_folder_matches_no_flag(self):
        self._make_file('2024', '2024-06-15 14.30.00_1.jpg')
        self.assertEqual(audit_master.check_year_folder_mismatches(['2024'], self.master), [])

    def test_bundle_folder_accepts_its_range(self):
        # The "2000 - 2010" bundle should accept any year in BUNDLED_EARLY_YEAR_RANGE
        self._make_file('2000 - 2010', '2007-03-15 12.00.00_1.jpg')
        self.assertEqual(
            audit_master.check_year_folder_mismatches(['2000 - 2010'], self.master),
            [],
        )

    def test_non_canonical_media_name_detected(self):
        # Old underscore format — parseable but not canonical
        self._make_file('2024', '2024-06-15_14-30-00_1.jpg')
        bad = audit_master.check_non_canonical_filenames(['2024'], self.master)
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0][1], '2024-06-15_14-30-00_1.jpg')

    def test_non_media_files_not_flagged_for_canonical(self):
        # A PDF or other non-media file shouldn't be flagged just because it
        # doesn't match the photo naming convention.
        self._make_file('2024', "Dad's Child Photos.pdf")
        self.assertEqual(audit_master.check_non_canonical_filenames(['2024'], self.master), [])

    def test_canonical_media_name_not_flagged(self):
        self._make_file('2024', '2024-06-15 14.30.00_1.jpg')
        self.assertEqual(audit_master.check_non_canonical_filenames(['2024'], self.master), [])


if __name__ == '__main__':
    unittest.main()
