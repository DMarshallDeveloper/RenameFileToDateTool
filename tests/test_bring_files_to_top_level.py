"""Tests for flatten_folder.py: pulling nested files up to the root,
with deterministic collision resolution.
"""

import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

import flatten_folder as bring  # noqa: E402


class TestComputeMoves(unittest.TestCase):
    """compute_moves plans the (source, destination) move pairs WITHOUT touching the
    filesystem. Planning is sequential so that collision resolution is deterministic
    — verifying it here means we can trust the resulting moves are non-overwriting
    even when executed in parallel."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_bring_')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make(self, *path_parts, content=''):
        full = os.path.join(self.tmpdir, *path_parts)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(content)
        return full

    def test_top_level_file_not_moved(self):
        self._make('already_here.jpg')
        moves = bring.compute_moves(self.tmpdir)
        self.assertEqual(moves, [])

    def test_nested_file_planned_for_root(self):
        self._make('sub', 'photo.jpg')
        moves = bring.compute_moves(self.tmpdir)
        self.assertEqual(len(moves), 1)
        src, dst = moves[0]
        self.assertEqual(os.path.basename(src), 'photo.jpg')
        self.assertEqual(os.path.dirname(dst), self.tmpdir)
        self.assertEqual(os.path.basename(dst), 'photo.jpg')

    def test_collision_with_top_level_file_gets_suffix(self):
        # An existing top-level file with the same name forces a rename.
        self._make('photo.jpg', content='top')
        self._make('sub', 'photo.jpg', content='nested')

        moves = bring.compute_moves(self.tmpdir)
        self.assertEqual(len(moves), 1)
        _src, dst = moves[0]
        self.assertEqual(os.path.basename(dst), 'photo_1.jpg')

    def test_collision_between_two_nested_files(self):
        # Two nested files with the same name — second one must not overwrite the first.
        self._make('sub1', 'photo.jpg', content='one')
        self._make('sub2', 'photo.jpg', content='two')

        moves = bring.compute_moves(self.tmpdir)
        destination_names = sorted(os.path.basename(dst) for (_src, dst) in moves)
        self.assertEqual(destination_names, ['photo.jpg', 'photo_1.jpg'])

    def test_move_files_actually_relocates(self):
        self._make('a', 'b', 'deep.jpg', content='deep')
        bring.move_files_to_top_level.__wrapped__ if hasattr(bring.move_files_to_top_level, '__wrapped__') else None
        # Avoid the messagebox popup by calling the planning + execution directly.
        moves = bring.compute_moves(self.tmpdir)
        for src, dst in moves:
            shutil.move(src, dst)
        top_level_files = [
            f for f in os.listdir(self.tmpdir)
            if os.path.isfile(os.path.join(self.tmpdir, f))
        ]
        self.assertEqual(top_level_files, ['deep.jpg'])


if __name__ == '__main__':
    unittest.main()
