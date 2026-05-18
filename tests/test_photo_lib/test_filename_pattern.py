"""Tests for photo_lib.filename_pattern: parsing dates out of master-library filenames,
extracting year, and the Jan-1-midnight placeholder bump.
"""

import os
import sys
import unittest
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

from photo_lib import filename_pattern  # noqa: E402


class TestParseFilenameDatetime(unittest.TestCase):
    """parse_filename_datetime accepts both the canonical YYYY-MM-DD HH.MM.SS form
    and the older HH-MM-SS / HH.MM (no seconds) forms left over from earlier
    versions of the renaming scripts."""

    def test_space_dot_format(self):
        dt = filename_pattern.parse_filename_datetime('2026-04-12 09.15.30_1.jpg')
        self.assertEqual(dt, datetime(2026, 4, 12, 9, 15, 30))

    def test_underscore_hyphen_format(self):
        # Older Google-takeout-script output: YYYY-MM-DD_HH-MM-SS
        dt = filename_pattern.parse_filename_datetime('2026-04-12_09-15-30.jpg')
        self.assertEqual(dt, datetime(2026, 4, 12, 9, 15, 30))

    def test_hh_mm_only_falls_back_to_zero_seconds(self):
        # Some old files were named without seconds — defaults to :00
        dt = filename_pattern.parse_filename_datetime('2026-04-09 19.52_1.jpg')
        self.assertEqual(dt, datetime(2026, 4, 9, 19, 52, 0))

    def test_no_date_returns_none(self):
        self.assertIsNone(filename_pattern.parse_filename_datetime('IMG_random.jpg'))
        self.assertIsNone(filename_pattern.parse_filename_datetime('no-date-here.png'))


class TestParseFilenameYear(unittest.TestCase):
    """parse_filename_year is the year-only fast path used by ingest_inbox_to_master."""

    def test_normal_year(self):
        self.assertEqual(filename_pattern.parse_filename_year('2024-11-30 18.20.00_1.mov'), 2024)

    def test_no_year_returns_none(self):
        self.assertIsNone(filename_pattern.parse_filename_year('IMG_random.jpg'))

    def test_implausible_year_returns_none(self):
        # Date-shaped strings like "0000-01-01..." or "9999-01-01..." shouldn't pass
        self.assertIsNone(filename_pattern.parse_filename_year('0001-01-01 00.00.00_1.jpg'))
        self.assertIsNone(filename_pattern.parse_filename_year('3001-01-01 00.00.00_1.jpg'))


class TestPlaceholderBump(unittest.TestCase):
    """apply_placeholder_time_bump rewrites YYYY-01-01 00.00.00 → 13:00 so the date
    doesn't roll back to Dec 31 in UTC-respecting viewers. Real timestamps untouched."""

    def test_jan_1_midnight_bumped_to_1pm(self):
        bumped = filename_pattern.apply_placeholder_time_bump(
            '2000-01-01 00.00.00_1.jpg', datetime(2000, 1, 1, 0, 0, 0))
        self.assertEqual(bumped, datetime(2000, 1, 1, 13, 0, 0))

    def test_jan_1_one_oh_one_bumped_to_1pm(self):
        # 01.01.00 is the second historical placeholder convention — also bumps.
        bumped = filename_pattern.apply_placeholder_time_bump(
            '2000-01-01 01.01.00_1.jpg', datetime(2000, 1, 1, 1, 1, 0))
        self.assertEqual(bumped, datetime(2000, 1, 1, 13, 0, 0))

    def test_other_dates_not_touched(self):
        unchanged = filename_pattern.apply_placeholder_time_bump(
            '2026-04-09 19.52.51_1.jpg', datetime(2026, 4, 9, 19, 52, 51))
        self.assertEqual(unchanged, datetime(2026, 4, 9, 19, 52, 51))

    def test_jan_1_with_real_time_not_touched(self):
        # Not a placeholder — has a real time
        unchanged = filename_pattern.apply_placeholder_time_bump(
            '2024-01-01 14.30.00_1.jpg', datetime(2024, 1, 1, 14, 30, 0))
        self.assertEqual(unchanged, datetime(2024, 1, 1, 14, 30, 0))


class TestMaybeRenamePlaceholder(unittest.TestCase):
    """maybe_rename_placeholder renames YYYY-01-01 00.00.00_N.ext files to
    13.00.00_N.ext on disk so the filename matches the EXIF that
    apply_placeholder_time_bump produces. Keeps the filename ≡ EXIF invariant."""

    def setUp(self):
        import shutil
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix='test_rename_placeholder_')
        self._shutil = shutil

    def tearDown(self):
        self._shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make(self, name):
        import os
        path = os.path.join(self.tmpdir, name)
        with open(path, 'w') as f:
            f.write('x')
        return path

    def test_placeholder_file_renamed_to_13(self):
        import os
        old = self._make('2011-01-01 00.00.00_5.jpg')
        new = filename_pattern.maybe_rename_placeholder(old)
        self.assertEqual(os.path.basename(new), '2011-01-01 13.00.00_5.jpg')
        self.assertTrue(os.path.exists(new))
        self.assertFalse(os.path.exists(old))

    def test_one_oh_one_placeholder_also_renamed_to_13(self):
        # The second placeholder convention (01.01.00) also gets renamed to 13.00.00.
        import os
        old = self._make('2000-01-01 01.01.00_5.jpg')
        new = filename_pattern.maybe_rename_placeholder(old)
        self.assertEqual(os.path.basename(new), '2000-01-01 13.00.00_5.jpg')
        self.assertTrue(os.path.exists(new))
        self.assertFalse(os.path.exists(old))

    def test_non_placeholder_left_alone(self):
        import os
        path = self._make('2024-06-15 14.30.00_1.jpg')
        result = filename_pattern.maybe_rename_placeholder(path)
        self.assertEqual(result, path)
        self.assertTrue(os.path.exists(path))

    def test_already_13_left_alone(self):
        # Idempotency: a file already at 13.00.00 doesn't match the placeholder regex
        import os
        path = self._make('2011-01-01 13.00.00_5.jpg')
        result = filename_pattern.maybe_rename_placeholder(path)
        self.assertEqual(result, path)
        self.assertTrue(os.path.exists(path))

    def test_collision_returns_none(self):
        # If the target name already exists, refuse rather than silently overwriting.
        old = self._make('2011-01-01 00.00.00_5.jpg')
        self._make('2011-01-01 13.00.00_5.jpg')  # already-occupied target
        result = filename_pattern.maybe_rename_placeholder(old)
        self.assertIsNone(result)

    def test_dry_run_does_not_rename(self):
        import os
        old = self._make('2011-01-01 00.00.00_5.jpg')
        new = filename_pattern.maybe_rename_placeholder(old, dry_run=True)
        self.assertEqual(os.path.basename(new), '2011-01-01 13.00.00_5.jpg')
        # File still at original location — dry-run returned the planned name only
        self.assertTrue(os.path.exists(old))
        self.assertFalse(os.path.exists(new))


class TestCanonicalFilenameRe(unittest.TestCase):
    """CANONICAL_FILENAME_RE is the strict ``YYYY-MM-DD HH.MM.SS_N.ext`` shape that
    detect_malformed_filenames uses to flag drift from the master-library convention."""

    def test_canonical_form_matches(self):
        self.assertIsNotNone(
            filename_pattern.CANONICAL_FILENAME_RE.match('2026-04-09 19.52.51_1.jpg'))
        self.assertIsNotNone(
            filename_pattern.CANONICAL_FILENAME_RE.match('2026-04-09 19.52.51_42.heic'))

    def test_underscore_in_date_time_separator_rejected(self):
        # Strict form requires space between date and time, NOT underscore
        self.assertIsNone(
            filename_pattern.CANONICAL_FILENAME_RE.match('2026-04-09_19.52.51_1.jpg'))

    def test_hyphen_in_time_rejected(self):
        # Strict form uses dots in the time part
        self.assertIsNone(
            filename_pattern.CANONICAL_FILENAME_RE.match('2026-04-09 19-52-51_1.jpg'))

    def test_missing_n_suffix_rejected(self):
        self.assertIsNone(
            filename_pattern.CANONICAL_FILENAME_RE.match('2026-04-09 19.52.51.jpg'))

    def test_no_extension_rejected(self):
        self.assertIsNone(
            filename_pattern.CANONICAL_FILENAME_RE.match('2026-04-09 19.52.51_1'))

    def test_camera_style_name_rejected(self):
        self.assertIsNone(
            filename_pattern.CANONICAL_FILENAME_RE.match('IMG_3118.JPG'))


if __name__ == '__main__':
    unittest.main()
