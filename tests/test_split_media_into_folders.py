"""Tests for SplitMediaIntoFolders.py: the 100-files-per-subfolder splitter that
prepares photos for batched upload to a phone.
"""

import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

import SplitMediaIntoFolders as splitter  # noqa: E402


class TestIsMediaFile(unittest.TestCase):
    """is_media_file is the per-file classifier — drives the media vs non-media
    split that decides which files get batched and which go into non_media/."""

    def test_recognized_image(self):
        self.assertTrue(splitter.is_media_file('foo.jpg'))
        self.assertTrue(splitter.is_media_file('foo.HEIC'))

    def test_recognized_video(self):
        self.assertTrue(splitter.is_media_file('clip.mp4'))
        self.assertTrue(splitter.is_media_file('clip.MOV'))

    def test_rejects_non_media(self):
        self.assertFalse(splitter.is_media_file('notes.txt'))
        self.assertFalse(splitter.is_media_file('archive.zip'))

    def test_no_extension_returns_false(self):
        self.assertFalse(splitter.is_media_file('Makefile'))


class TestSplitMediaFiles(unittest.TestCase):
    """split_media_files is the core batching logic: take a list of media files
    and a parent folder, distribute the files across new <parent>_NN subfolders
    holding 100 each. These tests cover the under-100, exactly-100, just-over-100,
    and span-multiple-folders cases."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_split_')
        self.parent = os.path.join(self.tmpdir, 'batch')
        os.makedirs(self.parent)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_files(self, count, ext='.jpg'):
        paths = []
        for i in range(count):
            path = os.path.join(self.parent, f'photo_{i:03}{ext}')
            with open(path, 'w') as f:
                f.write('x')
            paths.append(path)
        return paths

    def test_under_100_files_creates_one_subfolder(self):
        files = self._make_files(75)
        splitter.split_media_files(files, self.parent)
        subfolders = sorted(d for d in os.listdir(self.parent)
                            if os.path.isdir(os.path.join(self.parent, d)))
        self.assertEqual(subfolders, ['batch_01'])
        moved = os.listdir(os.path.join(self.parent, 'batch_01'))
        self.assertEqual(len(moved), 75)

    def test_exactly_100_files_creates_one_subfolder(self):
        files = self._make_files(100)
        splitter.split_media_files(files, self.parent)
        subfolders = sorted(d for d in os.listdir(self.parent)
                            if os.path.isdir(os.path.join(self.parent, d)))
        self.assertEqual(subfolders, ['batch_01'])
        self.assertEqual(len(os.listdir(os.path.join(self.parent, 'batch_01'))), 100)

    def test_101_files_creates_second_subfolder(self):
        files = self._make_files(101)
        splitter.split_media_files(files, self.parent)
        subfolders = sorted(d for d in os.listdir(self.parent)
                            if os.path.isdir(os.path.join(self.parent, d)))
        self.assertEqual(subfolders, ['batch_01', 'batch_02'])
        self.assertEqual(len(os.listdir(os.path.join(self.parent, 'batch_01'))), 100)
        self.assertEqual(len(os.listdir(os.path.join(self.parent, 'batch_02'))), 1)

    def test_250_files_spans_three_subfolders(self):
        files = self._make_files(250)
        splitter.split_media_files(files, self.parent)
        subfolders = sorted(d for d in os.listdir(self.parent)
                            if os.path.isdir(os.path.join(self.parent, d)))
        self.assertEqual(subfolders, ['batch_01', 'batch_02', 'batch_03'])
        counts = [len(os.listdir(os.path.join(self.parent, d))) for d in subfolders]
        self.assertEqual(counts, [100, 100, 50])


class TestProcessFolderStructure(unittest.TestCase):
    """process_folder_structure is the top-level orchestrator: walk the root,
    skip folders we've created ourselves (split-suffix _NN folders and the
    non_media spillover), and apply the batching to every other immediate
    subfolder. The non_media exclusion is the regression for Bug D — without it
    the walk infinitely nested non_media/non_media/non_media/..."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_split_full_')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_subfolder(self, name, files):
        sub = os.path.join(self.tmpdir, name)
        os.makedirs(sub)
        for f in files:
            with open(os.path.join(sub, f), 'w') as fh:
                fh.write('x')
        return sub

    def test_already_split_subfolders_are_skipped(self):
        # Folders matching _NN suffix should NOT be re-processed (would cause infinite splits)
        sub = self._make_subfolder('year2024_01', ['a.jpg', 'b.jpg'])
        splitter.process_folder_structure(self.tmpdir)
        # The files in the _NN folder should still be there, not nested again
        contents = sorted(os.listdir(sub))
        self.assertEqual(contents, ['a.jpg', 'b.jpg'])

    def test_non_media_files_go_to_non_media_folder(self):
        # Regression for Bug D: previously process_folder_structure walked recursively
        # into the non_media folder it just created, causing infinite path nesting.
        # The fix excludes NON_MEDIA_FOLDER_NAME from the recursive descent.
        sub = self._make_subfolder('year2024', ['photo.jpg', 'notes.txt'])
        splitter.process_folder_structure(self.tmpdir)
        non_media = os.path.join(sub, 'non_media')
        self.assertTrue(os.path.isdir(non_media))
        self.assertIn('notes.txt', os.listdir(non_media))
        # The media file should be in a split subfolder
        split_subfolders = [d for d in os.listdir(sub)
                            if os.path.isdir(os.path.join(sub, d)) and d != 'non_media']
        self.assertEqual(len(split_subfolders), 1)


if __name__ == '__main__':
    unittest.main()
