"""End-to-end tests for normalize_canonical_names.py: the CLI-facing wrapper
around photo_lib.canonical_renumber.

The detailed bucket-level tests live in test_photo_lib/test_canonical_renumber.py;
these tests focus on the script's recursive sweep, dry-run mode and the noop case.
"""

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

import normalize_canonical_names  # noqa: E402


def _touch(folder: str, name: str) -> str:
    path = os.path.join(folder, name)
    with open(path, "wb") as fh:
        fh.write(name.encode())
    return path


def _names_recursive(root: str) -> set[str]:
    out: set[str] = set()
    for current_dir, _subdirs, filenames in os.walk(root):
        for name in filenames:
            out.add(os.path.relpath(os.path.join(current_dir, name), root))
    return out


class TestNormalizeTree(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_normalize_canonical_')
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_normalizes_jpeg_to_jpg_recursively(self):
        year_dir = os.path.join(self.tmpdir, '2026')
        os.makedirs(year_dir)
        _touch(year_dir, '2026-04-12 09.15.30_1.jpeg')

        normalize_canonical_names.normalize_tree(self.tmpdir)

        self.assertIn(os.path.join('2026', '2026-04-12 09.15.30_1.jpg'),
                      _names_recursive(self.tmpdir))

    def test_dry_run_does_not_touch_disk(self):
        year_dir = os.path.join(self.tmpdir, '2026')
        os.makedirs(year_dir)
        _touch(year_dir, '2026-04-12 09.15.30_1.jpeg')
        before = _names_recursive(self.tmpdir)

        normalize_canonical_names.normalize_tree(self.tmpdir, dry_run=True)

        after = _names_recursive(self.tmpdir)
        self.assertEqual(before, after)

    def test_already_canonical_tree_is_noop(self):
        year_dir = os.path.join(self.tmpdir, '2026')
        os.makedirs(year_dir)
        _touch(year_dir, '2026-04-12 09.15.30_1.jpg')
        _touch(year_dir, '2026-04-12 09.15.30_2.jpg')
        before = _names_recursive(self.tmpdir)

        normalize_canonical_names.normalize_tree(self.tmpdir)

        self.assertEqual(_names_recursive(self.tmpdir), before)

    def test_empty_path_exits_quietly(self):
        # Resolving an empty path is the "user cancelled the picker" case;
        # the helper should return without raising.
        normalize_canonical_names.normalize_tree('')


if __name__ == '__main__':
    unittest.main()
