"""Tests for combine_libraries.py: multi-source copy with collision-driven _N bump."""

import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

import combine_libraries  # noqa: E402


def _touch(folder: str, name: str, content: bytes | None = None) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "wb") as fh:
        fh.write(content if content is not None else name.encode())
    return path


def _names_recursive(root: str) -> set[str]:
    out: set[str] = set()
    for current_dir, _subdirs, filenames in os.walk(root):
        for name in filenames:
            out.add(os.path.relpath(os.path.join(current_dir, name), root))
    return out


class TestCombine(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='test_combine_')
        self.source_a = os.path.join(self.workdir, 'A')
        self.source_b = os.path.join(self.workdir, 'B')
        self.dest = os.path.join(self.workdir, 'dest')
        self.addCleanup(lambda: shutil.rmtree(self.workdir, ignore_errors=True))

    def test_no_collision_copies_through(self):
        _touch(os.path.join(self.source_a, '2014'), '2014-01-01 13.00.00_1.jpg', b"A1")
        _touch(os.path.join(self.source_b, '2014'), '2014-01-02 13.00.00_1.jpg', b"B1")
        combine_libraries.combine([self.source_a, self.source_b], self.dest)
        self.assertEqual(
            _names_recursive(self.dest),
            {
                os.path.join('2014', '2014-01-01 13.00.00_1.jpg'),
                os.path.join('2014', '2014-01-02 13.00.00_1.jpg'),
            },
        )

    def test_collision_bumps_canonical_n(self):
        _touch(os.path.join(self.source_a, '2014'), '2014-01-01 13.00.00_1.jpg', b"A1")
        _touch(os.path.join(self.source_b, '2014'), '2014-01-01 13.00.00_1.jpg', b"B1")
        combine_libraries.combine([self.source_a, self.source_b], self.dest)
        names = _names_recursive(self.dest)
        self.assertEqual(
            names,
            {
                os.path.join('2014', '2014-01-01 13.00.00_1.jpg'),
                os.path.join('2014', '2014-01-01 13.00.00_2.jpg'),
            },
        )

    def test_collision_finds_next_free_slot(self):
        _touch(os.path.join(self.source_a, '2014'), '2014-01-01 13.00.00_1.jpg', b"A1")
        _touch(os.path.join(self.source_a, '2014'), '2014-01-01 13.00.00_2.jpg', b"A2")
        _touch(os.path.join(self.source_b, '2014'), '2014-01-01 13.00.00_1.jpg', b"B1")
        combine_libraries.combine([self.source_a, self.source_b], self.dest)
        # Source B's _1 must land at _3 because _1 and _2 are already taken by A.
        names = _names_recursive(self.dest)
        self.assertEqual(
            names,
            {
                os.path.join('2014', '2014-01-01 13.00.00_1.jpg'),
                os.path.join('2014', '2014-01-01 13.00.00_2.jpg'),
                os.path.join('2014', '2014-01-01 13.00.00_3.jpg'),
            },
        )

    def test_non_canonical_collision_falls_back_to_dup_suffix(self):
        _touch(os.path.join(self.source_a, '2014'), 'IMG_0001.jpg', b"A1")
        _touch(os.path.join(self.source_b, '2014'), 'IMG_0001.jpg', b"B1")
        combine_libraries.combine([self.source_a, self.source_b], self.dest)
        names = _names_recursive(self.dest)
        self.assertEqual(
            names,
            {
                os.path.join('2014', 'IMG_0001.jpg'),
                os.path.join('2014', 'IMG_0001_dup1.jpg'),
            },
        )

    def test_dry_run_does_not_touch_disk(self):
        _touch(os.path.join(self.source_a, '2014'), '2014-01-01 13.00.00_1.jpg')
        combine_libraries.combine([self.source_a], self.dest, dry_run=True)
        self.assertFalse(os.path.exists(self.dest))

    def test_subfolder_structure_preserved(self):
        _touch(os.path.join(self.source_a, '2014'), 'foo.jpg')
        _touch(os.path.join(self.source_a, '2026'), 'bar.jpg')
        combine_libraries.combine([self.source_a], self.dest)
        self.assertEqual(
            _names_recursive(self.dest),
            {os.path.join('2014', 'foo.jpg'), os.path.join('2026', 'bar.jpg')},
        )


if __name__ == '__main__':
    unittest.main()
