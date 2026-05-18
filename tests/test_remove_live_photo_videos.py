"""Tests for remove_live_photo_videos.py: detecting iOS Live Photo split-video files.

A Live Photo split is an image plus a short video that share the same name stem
(e.g. ``IMG_3118.HEIC`` + ``IMG_3118.MP4``). This script identifies them by:
  - Looking for a video whose stem matches a still image in the same folder, AND
  - Checking the video's duration (via ffprobe) is under a threshold (default 5 s)

These tests cover both pieces in isolation, plus the quarantine action that
moves matched files into a ``_LivePhotoMOVs/`` subfolder.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'RenameFileToDateTool'))

import remove_live_photo_videos as rlpv  # noqa: E402
from tests._fixture_helpers import copy_fixture_image, copy_fixture_video  # noqa: E402


class TestPairDetection(unittest.TestCase):
    """find_live_photo_video_candidates is the heart of the script — given a folder
    and a max-duration threshold, return the list of video filenames that look like
    Live Photo splits. These tests cover the matched-stem rule, the threshold cutoff,
    and case-insensitive matching (iOS sometimes uppercases extensions)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_rlpv_')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_image_plus_short_video_with_matching_stem_is_detected(self):
        # Fixture video is 1 second long — under the 5 s default threshold
        copy_fixture_image(self.tmpdir, name='IMG_3118.jpg')
        copy_fixture_video(self.tmpdir, name='IMG_3118.mov')

        matched = rlpv.find_live_photo_video_candidates(self.tmpdir, max_duration=5.0)
        self.assertEqual(matched, ['IMG_3118.mov'])

    def test_standalone_video_not_detected(self):
        # No paired image with matching stem
        copy_fixture_video(self.tmpdir, name='standalone_clip.mov')

        matched = rlpv.find_live_photo_video_candidates(self.tmpdir, max_duration=5.0)
        self.assertEqual(matched, [])

    def test_paired_video_over_threshold_not_detected(self):
        # Same pairing as the first test, but with a duration threshold the
        # 1 s fixture exceeds — shouldn't be treated as a Live Photo split.
        copy_fixture_image(self.tmpdir, name='IMG_3118.jpg')
        copy_fixture_video(self.tmpdir, name='IMG_3118.mov')

        matched = rlpv.find_live_photo_video_candidates(self.tmpdir, max_duration=0.5)
        self.assertEqual(matched, [])

    def test_stem_match_is_case_insensitive(self):
        copy_fixture_image(self.tmpdir, name='img_3118.jpg')
        copy_fixture_video(self.tmpdir, name='IMG_3118.MOV')

        matched = rlpv.find_live_photo_video_candidates(self.tmpdir, max_duration=5.0)
        self.assertEqual(matched, ['IMG_3118.MOV'])


class TestQuarantine(unittest.TestCase):
    """quarantine_videos moves a list of matched filenames into the _LivePhotoMOVs/
    subfolder rather than deleting them, so the user can review before binning."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_rlpv_q_')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_matched_files_moved_into_subfolder(self):
        copy_fixture_image(self.tmpdir, name='IMG_3118.jpg')
        copy_fixture_video(self.tmpdir, name='IMG_3118.mov')

        moved = rlpv.quarantine_videos(self.tmpdir, ['IMG_3118.mov'])
        self.assertEqual(moved, 1)
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, 'IMG_3118.mov')))
        self.assertTrue(os.path.exists(
            os.path.join(self.tmpdir, rlpv.QUARANTINE_FOLDER_NAME, 'IMG_3118.mov')
        ))
        # The paired image stays in place
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, 'IMG_3118.jpg')))


if __name__ == '__main__':
    unittest.main()
