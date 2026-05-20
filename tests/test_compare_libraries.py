"""Tests for compare_libraries.py — the post-sweep backup-vs-master diff tool.

The pairing logic is the interesting part: it has to recognise every kind of
transformation Mode 0 / Mode 1 / the converter performs and pair the
corresponding files across the trees. Tests cover each strategy in isolation
plus end-to-end diffs that mix several at once.
"""

import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

import compare_libraries  # noqa: E402


class TestPlaceholderCounterpart(unittest.TestCase):
    def test_00_pairs_with_13(self):
        self.assertEqual(
            compare_libraries.placeholder_counterpart('2011/2011-01-01 00.00.00_5.JPG'),
            os.path.join('2011', '2011-01-01 13.00.00_5.JPG'),
        )

    def test_13_pairs_with_00(self):
        self.assertEqual(
            compare_libraries.placeholder_counterpart('2011/2011-01-01 13.00.00_5.JPG'),
            os.path.join('2011', '2011-01-01 00.00.00_5.JPG'),
        )

    def test_non_placeholder_returns_none(self):
        self.assertIsNone(
            compare_libraries.placeholder_counterpart('2024/2024-06-15 14.30.00_1.jpg'))


class TestDiffTrees(unittest.TestCase):
    """End-to-end pairing tests on tiny synthetic trees."""

    def setUp(self):
        self.before = tempfile.mkdtemp(prefix='cmp_before_')
        self.after = tempfile.mkdtemp(prefix='cmp_after_')

    def tearDown(self):
        shutil.rmtree(self.before, ignore_errors=True)
        shutil.rmtree(self.after, ignore_errors=True)

    def _make(self, root, rel, size=100):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(b'x' * size)

    def _diff(self):
        return compare_libraries.diff_trees(
            compare_libraries.walk_tree(self.before),
            compare_libraries.walk_tree(self.after),
            os.path.abspath(self.after),
        )

    def test_identical_trees(self):
        self._make(self.before, '2024/2024-06-15 14.30.00_1.jpg', size=1000)
        self._make(self.after, '2024/2024-06-15 14.30.00_1.jpg', size=1000)
        r = self._diff()
        self.assertEqual(len(r['matched_by_type']['same_name']), 1)
        self.assertEqual(r['only_in_before'], [])
        self.assertEqual(r['only_in_after'], [])
        self.assertEqual(r['size_anomalies'], [])

    def test_placeholder_rename_paired(self):
        self._make(self.before, '2011/2011-01-01 00.00.00_5.JPG', size=2000)
        self._make(self.after, '2011/2011-01-01 13.00.00_5.JPG', size=2000)
        r = self._diff()
        self.assertEqual(len(r['matched_by_type']['placeholder_rename']), 1)
        self.assertEqual(r['only_in_before'], [])

    def test_canonical_rename_paired(self):
        # Old Takeout format → canonical (the Mode 0 transformation)
        self._make(self.before, '2025/2025-06-10_09-36-20.jpg', size=3000)
        self._make(self.after, '2025/2025-06-10 09.36.20_1.jpg', size=3000)
        r = self._diff()
        self.assertEqual(len(r['matched_by_type']['canonical_rename']), 1)
        self.assertEqual(r['only_in_before'], [])

    def test_jpeg_to_jpg_paired(self):
        self._make(self.before, '2024/2024-06-15 14.30.00_1.jpeg', size=1500)
        self._make(self.after, '2024/2024-06-15 14.30.00_1.jpg', size=1500)
        r = self._diff()
        self.assertEqual(len(r['matched_by_type']['jpeg_to_jpg']), 1)
        self.assertEqual(r['only_in_before'], [])

    def test_extension_change_paired_heic_to_jpg(self):
        # .heic-named file that was actually JPEG → renamed to .jpg
        self._make(self.before, '2025/2025-03-07 09.19.47_1.heic', size=4500)
        self._make(self.after, '2025/2025-03-07 09.19.47_1.jpg', size=4500)
        r = self._diff()
        self.assertEqual(len(r['matched_by_type']['extension_change']), 1)
        self.assertEqual(r['only_in_before'], [])

    def test_transcode_paired_with_soft_delete(self):
        # MPG transcoded to MP4, original soft-deleted to _Inbox/removed_mpgs
        self._make(self.before, '2007/2007-11-24 11.07.28_1.mpg', size=778000)
        self._make(self.after, '2007/2007-11-24 11.07.28_1.mp4', size=870000)
        self._make(self.after,
                   os.path.join('_Inbox', 'removed_mpgs', '2007-11-24 11.07.28_1.mpg'),
                   size=778000)
        r = self._diff()
        self.assertEqual(len(r['matched_by_type']['transcode']), 1)
        self.assertEqual(r['transcodes_missing_soft_delete'], [])
        self.assertEqual(r['only_in_before'], [])

    def test_transcode_without_soft_delete_flagged(self):
        # MPG transcoded but original is NOT preserved — should warn
        self._make(self.before, '2007/2007-11-24 11.07.28_1.mpg', size=778000)
        self._make(self.after, '2007/2007-11-24 11.07.28_1.mp4', size=870000)
        r = self._diff()
        self.assertEqual(len(r['matched_by_type']['transcode']), 1)
        self.assertEqual(len(r['transcodes_missing_soft_delete']), 1)

    def test_size_tiebreak_picks_closest(self):
        # Backup has _1.heic (4.6 MB JPEG content). Master has _1.jpg and _2.jpg
        # at the same date+time. The closest size match is the right pair.
        self._make(self.before, '2025/2025-03-07 09.19.47_1.heic', size=4_600_000)
        self._make(self.after, '2025/2025-03-07 09.19.47_1.jpg', size=2_400_000)
        self._make(self.after, '2025/2025-03-07 09.19.47_2.jpg', size=4_600_000)
        r = self._diff()
        self.assertEqual(len(r['matched_by_type'].get('size_tiebreak', [])), 1)
        # The chosen pair should be _2.jpg (the one matching size)
        backup_rel, master_rel = r['matched_by_type']['size_tiebreak'][0]
        self.assertIn('_2.jpg', master_rel)

    def test_missing_file_flagged(self):
        # File present in backup, absent in master — data loss signal
        self._make(self.before, '2024/2024-06-15 14.30.00_1.jpg', size=1000)
        r = self._diff()
        self.assertEqual(len(r['only_in_before']), 1)
        self.assertEqual(sum(len(v) for v in r['matched_by_type'].values()), 0)

    def test_unexpected_addition_flagged(self):
        self._make(self.after, '2024/2024-06-15 14.30.00_1.jpg', size=1000)
        r = self._diff()
        self.assertEqual(len(r['only_in_after']), 1)

    def test_size_anomaly_flagged_for_same_name(self):
        self._make(self.before, '2024/a.jpg', size=1_000_000)
        self._make(self.after, '2024/a.jpg', size=100_000)  # 900 KB diff
        r = self._diff()
        self.assertEqual(len(r['size_anomalies']), 1)

    def test_size_diff_skipped_for_transcode(self):
        # MPG → MP4 transcodes legitimately have very different sizes.
        self._make(self.before, '2007/2007-01-01 12.00.00_1.mpg', size=20_000_000)
        self._make(self.after, '2007/2007-01-01 12.00.00_1.mp4', size=15_000_000)
        self._make(self.after,
                   os.path.join('_Inbox', 'removed_mpgs', '2007-01-01 12.00.00_1.mpg'),
                   size=20_000_000)
        r = self._diff()
        self.assertEqual(r['size_anomalies'], [])

    def test_soft_delete_folders_not_reported_as_additions(self):
        # _Inbox/removed_mpgs/ on the master side represents the soft-deletes.
        # Files there shouldn't be flagged as "unexpected additions".
        self._make(self.before, '2007/2007-01-01 12.00.00_1.mpg', size=1000)
        self._make(self.after, '2007/2007-01-01 12.00.00_1.mp4', size=1100)
        self._make(self.after,
                   os.path.join('_Inbox', 'removed_mpgs', '2007-01-01 12.00.00_1.mpg'),
                   size=1000)
        r = self._diff()
        self.assertEqual(r['only_in_after'], [])

    def test_full_session_scenario(self):
        # End-to-end: a realistic mix of every transformation we performed.
        # Backup snapshot:
        self._make(self.before, '2011/2011-01-01 00.00.00_1.JPG', size=2000)   # placeholder
        self._make(self.before, '2025/2025-06-10_09-36-20.jpg', size=3000)     # old format
        self._make(self.before, '2025/2025-03-07 09.19.47_1.heic', size=4500)  # heic-as-jpg
        self._make(self.before, '2024/2024-06-15 14.30.00_1.jpeg', size=1500)  # jpeg→jpg
        self._make(self.before, '2007/2007-11-24 11.07.28_1.mpg', size=778000) # mpg
        self._make(self.before, '2024/2024-12-14 19.06.36_1.mov', size=5000)   # mov→mp4 cosmetic
        self._make(self.before, '2024/unchanged.jpg', size=999)                 # straight identical

        # Master after the session:
        self._make(self.after, '2011/2011-01-01 13.00.00_1.JPG', size=2000)   # bumped
        self._make(self.after, '2025/2025-06-10 09.36.20_1.jpg', size=3000)   # canonical
        self._make(self.after, '2025/2025-03-07 09.19.47_1.jpg', size=4500)   # ext changed
        self._make(self.after, '2024/2024-06-15 14.30.00_1.jpg', size=1500)   # jpeg→jpg
        self._make(self.after, '2007/2007-11-24 11.07.28_1.mp4', size=870000) # transcoded
        self._make(self.after,
                   os.path.join('_Inbox', 'removed_mpgs', '2007-11-24 11.07.28_1.mpg'),
                   size=778000)                                                # soft-deleted
        self._make(self.after, '2024/2024-12-14 19.06.36_1.mp4', size=5000)   # mov→mp4 ext
        self._make(self.after, '2024/unchanged.jpg', size=999)                 # straight identical

        r = self._diff()
        m = r['matched_by_type']
        self.assertEqual(len(m.get('placeholder_rename', [])), 1)
        self.assertEqual(len(m.get('canonical_rename', [])), 1)
        self.assertEqual(len(m.get('extension_change', [])), 2)  # heic→jpg AND mov→mp4
        self.assertEqual(len(m.get('jpeg_to_jpg', [])), 1)
        self.assertEqual(len(m.get('transcode', [])), 1)
        self.assertEqual(len(m.get('same_name', [])), 1)
        self.assertEqual(r['only_in_before'], [])
        self.assertEqual(r['only_in_after'], [])
        self.assertEqual(r['transcodes_missing_soft_delete'], [])


class TestPerYearBreakdown(unittest.TestCase):
    """The per-year breakdown is the headline diagnostic for "why does master
    have N more files than the takeout?" — it surfaces the imbalance year by
    year and categorizes the unmatched files so the source of the delta is
    immediately visible."""

    def setUp(self):
        self.before = tempfile.mkdtemp(prefix='cmp_year_b_')
        self.after = tempfile.mkdtemp(prefix='cmp_year_a_')

    def tearDown(self):
        shutil.rmtree(self.before, ignore_errors=True)
        shutil.rmtree(self.after, ignore_errors=True)

    def _make(self, root, rel, size=100):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(b'x' * size)

    def _diff(self):
        return compare_libraries.diff_trees(
            compare_libraries.walk_tree(self.before),
            compare_libraries.walk_tree(self.after),
            os.path.abspath(self.after),
        )

    def test_year_extracted_from_canonical_filename(self):
        self.assertEqual(
            compare_libraries._year_for_rel_path('2014/2014-06-15 14.30.00_1.jpg'),
            '2014',
        )

    def test_year_extracted_from_folder_when_basename_unparseable(self):
        self.assertEqual(
            compare_libraries._year_for_rel_path('2014/random_name.jpg'),
            '2014',
        )

    def test_unknown_when_no_year_anywhere(self):
        self.assertEqual(
            compare_libraries._year_for_rel_path('misc/no_date_here.jpg'),
            'unknown',
        )

    def test_per_year_rows_record_matched_and_unmatched(self):
        # Year 2014: 2 files on each side, both pair up (same_name).
        self._make(self.before, '2014/2014-06-15 14.30.00_1.jpg', size=1000)
        self._make(self.before, '2014/2014-07-01 10.00.00_1.jpg', size=1000)
        self._make(self.after, '2014/2014-06-15 14.30.00_1.jpg', size=1000)
        self._make(self.after, '2014/2014-07-01 10.00.00_1.jpg', size=1000)
        # Year 2024: master has an extra file with no before counterpart at all.
        self._make(self.after, '2024/2024-12-25 09.00.00_1.jpg', size=1000)

        result = self._diff()
        year_2014 = result['per_year_breakdown']['2014']
        self.assertEqual(year_2014['before'], 2)
        self.assertEqual(year_2014['after'], 2)
        self.assertEqual(year_2014['matched'], 2)

        year_2024 = result['per_year_breakdown']['2024']
        self.assertEqual(year_2024['before'], 0)
        self.assertEqual(year_2024['after'], 1)
        self.assertEqual(year_2024['isolated_extra'], 1)


class TestUnmatchedCategorization(unittest.TestCase):
    """Each only_in_after/only_in_before file is partitioned by its relationship
    to matched pairs so the user can tell "extra copy on the same timestamp" from
    "isolated extra with no other file on its date"."""

    def setUp(self):
        self.before = tempfile.mkdtemp(prefix='cmp_cat_b_')
        self.after = tempfile.mkdtemp(prefix='cmp_cat_a_')

    def tearDown(self):
        shutil.rmtree(self.before, ignore_errors=True)
        shutil.rmtree(self.after, ignore_errors=True)

    def _make(self, root, rel, size=100):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(b'x' * size)

    def _diff(self):
        return compare_libraries.diff_trees(
            compare_libraries.walk_tree(self.before),
            compare_libraries.walk_tree(self.after),
            os.path.abspath(self.after),
        )

    def test_same_timestamp_extra_when_master_has_second_copy(self):
        # _1 pairs; _2 is an extra at the same exact timestamp — likely a
        # duplicate kept locally that was not in Google Photos.
        self._make(self.before, '2014/2014-06-15 14.30.00_1.jpg', size=1000)
        self._make(self.after, '2014/2014-06-15 14.30.00_1.jpg', size=1000)
        self._make(self.after, '2014/2014-06-15 14.30.00_2.jpg', size=1500)
        result = self._diff()
        self.assertEqual(len(result['only_in_after_by_reason']['same_timestamp_extra']), 1)
        self.assertEqual(result['only_in_after_by_reason']['same_date_extra'], [])
        self.assertEqual(result['only_in_after_by_reason']['isolated_extra'], [])

    def test_same_date_extra_when_master_has_different_time_on_known_date(self):
        # Master has a photo on a date that Before knows about, but the time differs —
        # it's a different photo on the same day.
        self._make(self.before, '2014/2014-06-15 14.30.00_1.jpg', size=1000)
        self._make(self.after, '2014/2014-06-15 14.30.00_1.jpg', size=1000)
        self._make(self.after, '2014/2014-06-15 18.45.00_1.jpg', size=1100)
        result = self._diff()
        self.assertEqual(result['only_in_after_by_reason']['same_timestamp_extra'], [])
        self.assertEqual(len(result['only_in_after_by_reason']['same_date_extra']), 1)
        self.assertEqual(result['only_in_after_by_reason']['isolated_extra'], [])

    def test_isolated_extra_when_date_absent_from_before(self):
        # Year 2024 doesn't appear in Before at all — likely added after the takeout.
        self._make(self.before, '2014/2014-06-15 14.30.00_1.jpg', size=1000)
        self._make(self.after, '2014/2014-06-15 14.30.00_1.jpg', size=1000)
        self._make(self.after, '2024/2024-12-25 09.00.00_1.jpg', size=1500)
        result = self._diff()
        self.assertEqual(result['only_in_after_by_reason']['same_timestamp_extra'], [])
        self.assertEqual(result['only_in_after_by_reason']['same_date_extra'], [])
        self.assertEqual(len(result['only_in_after_by_reason']['isolated_extra']), 1)

    def test_symmetric_missing_categorization_on_before_side(self):
        # Before has a file Master doesn't — the symmetric "missing" buckets fire.
        self._make(self.before, '2014/2014-06-15 14.30.00_1.jpg', size=1000)
        self._make(self.before, '2014/2014-06-15 14.30.00_2.jpg', size=1200)
        self._make(self.after, '2014/2014-06-15 14.30.00_1.jpg', size=1000)
        result = self._diff()
        self.assertEqual(len(result['only_in_before_by_reason']['same_timestamp_missing']), 1)


if __name__ == '__main__':
    unittest.main()
