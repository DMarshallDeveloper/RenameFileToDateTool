"""Tests for ingest_inbox_to_master.py: the script that moves correctly-named files
out of the _Inbox staging folder into the right year folder of the master library.

Covers:
  - Year extraction from the filename (and the special 2000-2010 bundle folder)
  - Files whose names don't parse get left in the inbox, not moved
  - Name collisions in the destination year folder get a _dup<N> suffix
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'RenameFileToDateTool'))

import ingest_inbox_to_master as ingest  # noqa: E402


def make_empty(path):
    """Create an empty file at the given path (sufficient for move tests)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w'):
        pass


class TestPlanMoves(unittest.TestCase):
    """plan_moves returns a list of (source, destination_folder, filename, year)
    tuples WITHOUT touching the filesystem — pure planning, so we can verify the
    year-routing logic without doing real moves."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_ingest_')
        self.master = os.path.join(self.tmpdir, 'master')
        self.inbox = os.path.join(self.master, '_Inbox')
        os.makedirs(self.inbox)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_routes_files_to_year_folders(self):
        make_empty(os.path.join(self.inbox, '2023-04-12 09.15.30_1.jpg'))
        make_empty(os.path.join(self.inbox, '2024-11-30 18.20.00_1.mov'))

        moves, unparseable = ingest.plan_moves(self.inbox, self.master)
        self.assertEqual(unparseable, [])
        years_by_filename = {filename: year for (_src, _dst, filename, year) in moves}
        self.assertEqual(years_by_filename['2023-04-12 09.15.30_1.jpg'], 2023)
        self.assertEqual(years_by_filename['2024-11-30 18.20.00_1.mov'], 2024)

    def test_2000_to_2010_uses_bundled_folder(self):
        make_empty(os.path.join(self.inbox, '2005-08-15 12.00.00_1.jpg'))
        make_empty(os.path.join(self.inbox, '2010-12-31 23.59.59_1.jpg'))

        moves, _ = ingest.plan_moves(self.inbox, self.master)
        for _src, destination_folder, filename, _year in moves:
            self.assertTrue(destination_folder.endswith('2000 - 2010'),
                            f"{filename} routed to {destination_folder}, expected bundle")

    def test_2011_does_not_use_bundle(self):
        make_empty(os.path.join(self.inbox, '2011-01-01 00.00.00_1.jpg'))
        moves, _ = ingest.plan_moves(self.inbox, self.master)
        _src, destination_folder, _filename, _year = moves[0]
        self.assertTrue(destination_folder.endswith('2011'),
                        f"2011 file routed to {destination_folder}")

    def test_unparseable_filenames_are_skipped(self):
        make_empty(os.path.join(self.inbox, 'IMG_random.jpg'))
        make_empty(os.path.join(self.inbox, 'no-date-prefix.jpg'))
        make_empty(os.path.join(self.inbox, '2024-01-01 12.00.00_1.jpg'))  # one valid

        moves, unparseable = ingest.plan_moves(self.inbox, self.master)
        self.assertEqual(len(moves), 1)
        self.assertEqual(set(unparseable), {'IMG_random.jpg', 'no-date-prefix.jpg'})


class TestExecuteMoves(unittest.TestCase):
    """execute_moves actually performs the planned moves. These tests verify the
    filesystem ends up in the expected shape: files moved to year folders, inbox
    emptied, name collisions in the destination handled with a _dup<N> suffix."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_ingest_exec_')
        self.master = os.path.join(self.tmpdir, 'master')
        self.inbox = os.path.join(self.master, '_Inbox')
        os.makedirs(self.inbox)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_files_actually_moved_to_year_folders(self):
        make_empty(os.path.join(self.inbox, '2023-04-12 09.15.30_1.jpg'))
        make_empty(os.path.join(self.inbox, '2005-08-15 12.00.00_1.jpg'))

        moves, _ = ingest.plan_moves(self.inbox, self.master)
        ingest.execute_moves(moves)

        self.assertTrue(os.path.exists(os.path.join(self.master, '2023', '2023-04-12 09.15.30_1.jpg')))
        self.assertTrue(os.path.exists(os.path.join(self.master, '2000 - 2010', '2005-08-15 12.00.00_1.jpg')))
        # Inbox should now be empty of those files
        self.assertEqual(os.listdir(self.inbox), [])

    def test_collision_gets_dup_suffix(self):
        # Pre-existing file in the year folder
        os.makedirs(os.path.join(self.master, '2023'))
        with open(os.path.join(self.master, '2023', '2023-04-12 09.15.30_1.jpg'), 'w') as f:
            f.write('existing')

        # New file with the same name in inbox
        with open(os.path.join(self.inbox, '2023-04-12 09.15.30_1.jpg'), 'w') as f:
            f.write('new')

        moves, _ = ingest.plan_moves(self.inbox, self.master)
        ingest.execute_moves(moves)

        files_in_2023 = sorted(os.listdir(os.path.join(self.master, '2023')))
        self.assertEqual(files_in_2023,
                         ['2023-04-12 09.15.30_1.jpg', '2023-04-12 09.15.30_1_dup1.jpg'])


if __name__ == '__main__':
    unittest.main()
