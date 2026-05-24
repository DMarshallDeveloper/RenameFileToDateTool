"""Tests for extract_and_flatten_takeout.py: the Takeout-zip
flattener. Targets ``unique_path`` (collision resolution) and end-to-end behavior of
``flatten_takeout`` minus the messagebox UI.
"""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

import extract_and_flatten_takeout as flatten  # noqa: E402


class TestUniquePath(unittest.TestCase):
    """unique_path is the collision-resolver: given a target Path and a set of
    already-planned destination names, return a Path that's available. It checks
    BOTH the filesystem and the planned set so two pending moves don't pick the
    same destination."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix='test_uniquepath_'))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_dest_when_no_collision(self):
        target = self.tmpdir / 'photo.jpg'
        result = flatten.unique_path(target, set())
        self.assertEqual(result, target)

    def test_appends_counter_on_disk_collision(self):
        target = self.tmpdir / 'photo.jpg'
        target.write_text('existing')
        result = flatten.unique_path(target, set())
        self.assertEqual(result.name, 'photo_1.jpg')

    def test_planned_set_blocks_reuse(self):
        target = self.tmpdir / 'photo.jpg'
        result = flatten.unique_path(target, {'photo.jpg'})
        self.assertEqual(result.name, 'photo_1.jpg')

    def test_multiple_collisions_advance_counter(self):
        target = self.tmpdir / 'photo.jpg'
        target.write_text('a')
        (self.tmpdir / 'photo_1.jpg').write_text('b')
        result = flatten.unique_path(target, set())
        self.assertEqual(result.name, 'photo_2.jpg')


class TestFlattenTakeout(unittest.TestCase):
    """flatten_takeout is the top-level entry: extract any .zip files, then move
    every nested file up to a single 'Extracted data/' folder. The messagebox UI
    is mocked out so the test doesn't try to pop up a dialog."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix='test_flatten_'))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @mock.patch.object(flatten, 'messagebox')
    def test_flattens_nested_files_into_extracted_data(self, _mock_messagebox):
        # Nested files (no zips), flattened to "Extracted data/"
        (self.tmpdir / 'sub' / 'deep').mkdir(parents=True)
        (self.tmpdir / 'sub' / 'deep' / 'photo.jpg').write_text('p')
        (self.tmpdir / 'sub' / 'video.mov').write_text('v')

        flatten.flatten_takeout(self.tmpdir)

        extracted = self.tmpdir / 'Extracted data'
        landed = sorted(p.name for p in extracted.iterdir())
        self.assertEqual(landed, ['photo.jpg', 'video.mov'])

    @mock.patch.object(flatten, 'messagebox')
    def test_extracts_zip_contents(self, _mock_messagebox):
        # Build a small zip containing two files in a nested structure.
        zip_path = self.tmpdir / 'takeout.zip'
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr('Takeout/Google Photos/2026/a.jpg', 'contents-a')
            zf.writestr('Takeout/Google Photos/2026/b.heic', 'contents-b')

        flatten.flatten_takeout(self.tmpdir)

        extracted = self.tmpdir / 'Extracted data'
        # Must land at the TOP LEVEL of Extracted data, not nested under
        # Takeout/Google Photos/... — step 2 only scans the top level.
        landed_names = {p.name for p in extracted.iterdir() if p.is_file()}
        self.assertIn('a.jpg', landed_names)
        self.assertIn('b.heic', landed_names)
        # And the intermediate Takeout/ tree should be cleaned up.
        self.assertFalse((extracted / 'Takeout').exists())

    @mock.patch.object(flatten, 'messagebox')
    def test_collision_between_nested_files_gets_suffix(self, _mock_messagebox):
        # Two files named the same at different depths — flattener must not lose either.
        (self.tmpdir / 'sub1').mkdir()
        (self.tmpdir / 'sub2').mkdir()
        (self.tmpdir / 'sub1' / 'photo.jpg').write_text('one')
        (self.tmpdir / 'sub2' / 'photo.jpg').write_text('two')

        flatten.flatten_takeout(self.tmpdir)

        extracted = self.tmpdir / 'Extracted data'
        landed = sorted(p.name for p in extracted.iterdir())
        self.assertEqual(landed, ['photo.jpg', 'photo_1.jpg'])


if __name__ == '__main__':
    unittest.main()
