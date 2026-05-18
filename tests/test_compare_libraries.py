"""Tests for compare_libraries.py — the post-sweep backup-vs-master diff tool.

The matching logic is the interesting part: it has to pair a backup file named
``YYYY-01-01 00.00.00_N.ext`` with the master's ``YYYY-01-01 13.00.00_N.ext``
because that's the rename Mode 1 performs. Tests cover the pairing helper
directly plus an end-to-end diff over a tiny tmp tree.
"""

import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

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

    def test_jan_1_with_non_midnight_time_not_a_placeholder(self):
        # Only 00.00.00 and 13.00.00 are placeholder pairs; e.g. 12.00.00 is a real time
        self.assertIsNone(
            compare_libraries.placeholder_counterpart('2024/2024-01-01 12.00.00_1.jpg'))


class TestDiffTrees(unittest.TestCase):
    def setUp(self):
        self.before = tempfile.mkdtemp(prefix='cmp_before_')
        self.after = tempfile.mkdtemp(prefix='cmp_after_')

    def tearDown(self):
        shutil.rmtree(self.before, ignore_errors=True)
        shutil.rmtree(self.after, ignore_errors=True)

    def _make(self, root, rel, size=100):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(b'x' * size)

    def test_identical_trees(self):
        self._make(self.before, '2024/2024-06-15 14.30.00_1.jpg', size=1000)
        self._make(self.after,  '2024/2024-06-15 14.30.00_1.jpg', size=1000)
        result = compare_libraries.diff_trees(
            compare_libraries.walk_tree(self.before),
            compare_libraries.walk_tree(self.after),
        )
        self.assertEqual(len(result['matched_same']), 1)
        self.assertEqual(len(result['matched_renamed']), 0)
        self.assertEqual(result['only_in_before'], [])
        self.assertEqual(result['only_in_after'], [])
        self.assertEqual(result['size_anomalies'], [])

    def test_placeholder_rename_paired(self):
        self._make(self.before, '2011/2011-01-01 00.00.00_5.JPG', size=2000)
        self._make(self.after,  '2011/2011-01-01 13.00.00_5.JPG', size=2000)
        result = compare_libraries.diff_trees(
            compare_libraries.walk_tree(self.before),
            compare_libraries.walk_tree(self.after),
        )
        self.assertEqual(len(result['matched_renamed']), 1)
        self.assertEqual(result['only_in_before'], [])
        self.assertEqual(result['only_in_after'], [])

    def test_missing_file_in_after_flagged(self):
        self._make(self.before, '2024/2024-06-15 14.30.00_1.jpg', size=1000)
        # Don't create in after
        result = compare_libraries.diff_trees(
            compare_libraries.walk_tree(self.before),
            compare_libraries.walk_tree(self.after),
        )
        self.assertEqual(len(result['only_in_before']), 1)
        self.assertIn(os.path.join('2024', '2024-06-15 14.30.00_1.jpg'),
                      result['only_in_before'])

    def test_unexpected_addition_flagged(self):
        self._make(self.after, '2024/2024-06-15 14.30.00_1.jpg', size=1000)
        result = compare_libraries.diff_trees(
            compare_libraries.walk_tree(self.before),
            compare_libraries.walk_tree(self.after),
        )
        self.assertEqual(len(result['only_in_after']), 1)

    def test_size_anomaly_flagged(self):
        # 1 MB before, 100 KB after — way over the 100 KB tolerance
        self._make(self.before, 'a.jpg', size=1_000_000)
        self._make(self.after,  'a.jpg', size=100_000)
        result = compare_libraries.diff_trees(
            compare_libraries.walk_tree(self.before),
            compare_libraries.walk_tree(self.after),
        )
        self.assertEqual(len(result['size_anomalies']), 1)

    def test_small_size_diff_within_tolerance(self):
        # Exiftool typically changes container size by a few hundred bytes
        self._make(self.before, 'a.jpg', size=1_000_000)
        self._make(self.after,  'a.jpg', size=1_000_500)
        result = compare_libraries.diff_trees(
            compare_libraries.walk_tree(self.before),
            compare_libraries.walk_tree(self.after),
        )
        self.assertEqual(result['size_anomalies'], [])


if __name__ == '__main__':
    unittest.main()
