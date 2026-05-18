"""Tests for process_takeout.py — the one-command Takeout → Inbox pipeline.

The two underlying steps (``flatten_takeout`` and ``process_and_copy_media_files``)
have their own thorough tests. The orchestrator's job is just to chain them in
the right order with the right intermediate paths, so the tests focus on:

  - The helper ``default_destination`` builds the expected path.
  - An end-to-end run on a tiny synthetic Takeout produces canonical-named
    files in the destination.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

import process_takeout  # noqa: E402
from tests._fixture_helpers import copy_fixture_image  # noqa: E402


class TestDefaultDestination(unittest.TestCase):
    def test_basename_appended_to_inbox(self):
        # Whatever the Takeout folder is called, the staging path keeps that
        # name as a subfolder of MASTER_ROOT/_Inbox/ so each batch is isolated.
        result = process_takeout.default_destination(r'C:\Downloads\takeout-20260517T025830Z-3-001')
        self.assertTrue(result.endswith(os.path.join('_Inbox', 'takeout-20260517T025830Z-3-001')))

    def test_trailing_separator_handled(self):
        result = process_takeout.default_destination(r'C:\Downloads\takeout-2026\\')
        self.assertTrue(result.endswith(os.path.join('_Inbox', 'takeout-2026')))


class TestProcessEndToEnd(unittest.TestCase):
    """End-to-end pipeline on a tiny synthetic Takeout layout (no zip files —
    flatten_takeout walks the nested directories whether they came from a zip
    extraction or already-loose files)."""

    def setUp(self):
        self.takeout = tempfile.mkdtemp(prefix='test_takeout_src_')
        self.dst = tempfile.mkdtemp(prefix='test_takeout_dst_')

    def tearDown(self):
        shutil.rmtree(self.takeout, ignore_errors=True)
        shutil.rmtree(self.dst, ignore_errors=True)

    def _make_takeout_layout(self):
        # Nested Takeout-shape: Takeout/Google Photos/<album>/<year>/IMG.jpg + IMG.jpg.json
        album_dir = os.path.join(self.takeout, 'Takeout', 'Google Photos', 'Holiday', '2024')
        os.makedirs(album_dir, exist_ok=True)
        img_path = copy_fixture_image(album_dir, name='IMG_001.jpg')
        # Minimal Takeout JSON sidecar — photoTakenTime.timestamp is the only
        # field process_and_copy_media_files actually needs.
        json_path = img_path + '.json'
        # 2024-06-15 14:30:45 NZST = 02:30:45 UTC (NZ in June is NZST +12)
        with open(json_path, 'w') as f:
            json.dump({"photoTakenTime": {"timestamp": "1718418645"}}, f)
        return img_path, json_path

    def test_pipeline_produces_canonical_file_in_destination(self):
        self._make_takeout_layout()
        process_takeout.process(self.takeout, self.dst, dry_run=False)
        # The destination should contain a canonical-named copy of the fixture image
        files = os.listdir(self.dst)
        canonical = [f for f in files if f.startswith('2024-06-15 ') and f.endswith('.jpg')]
        self.assertEqual(len(canonical), 1, f"expected one canonical-named jpg, got: {files}")


if __name__ == '__main__':
    unittest.main()
