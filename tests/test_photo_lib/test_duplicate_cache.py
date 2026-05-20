"""Tests for photo_lib.duplicate_cache: round-trip + size/mtime invalidation."""

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

from photo_lib.duplicate_cache import FingerprintCache  # noqa: E402
from photo_lib.duplicate_finder import FileFingerprint  # noqa: E402


def _image_fp(path: str, size: int = 1024, mtime: float = 1700000000.0) -> FileFingerprint:
    return FileFingerprint(
        path=path, size=size, mtime=mtime, media_kind="image",
        file_sha256="abc", pixel_sha256="def", phash_hex="0123456789abcdef",
        frame_phashes_hex=None, width=200, height=100,
    )


def _video_fp(path: str) -> FileFingerprint:
    return FileFingerprint(
        path=path, size=999, mtime=1700000001.0, media_kind="video",
        file_sha256="vhash", pixel_sha256=None, phash_hex=None,
        frame_phashes_hex=("aa", "bb", "cc", "dd", "ee"),
        width=None, height=None,
    )


class TestFingerprintCache(unittest.TestCase):
    def test_round_trip_image(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = os.path.join(folder, "cache.db")
            with FingerprintCache(db_path) as cache:
                stored = _image_fp("/lib/a.jpg")
                cache.store(stored)
                got = cache.lookup("/lib/a.jpg", stored.size, stored.mtime)
            self.assertEqual(got, stored)

    def test_round_trip_video_preserves_frame_phashes(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = os.path.join(folder, "cache.db")
            with FingerprintCache(db_path) as cache:
                stored = _video_fp("/lib/clip.mov")
                cache.store(stored)
                got = cache.lookup("/lib/clip.mov", stored.size, stored.mtime)
            self.assertEqual(got.frame_phashes_hex, ("aa", "bb", "cc", "dd", "ee"))
            self.assertEqual(got.media_kind, "video")

    def test_size_drift_invalidates(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = os.path.join(folder, "cache.db")
            with FingerprintCache(db_path) as cache:
                stored = _image_fp("/lib/a.jpg", size=1000)
                cache.store(stored)
                got = cache.lookup("/lib/a.jpg", size=2000, mtime=stored.mtime)
            self.assertIsNone(got)

    def test_mtime_drift_invalidates(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = os.path.join(folder, "cache.db")
            with FingerprintCache(db_path) as cache:
                stored = _image_fp("/lib/a.jpg", mtime=1700000000.0)
                cache.store(stored)
                got = cache.lookup("/lib/a.jpg", stored.size, 1700009999.0)
            self.assertIsNone(got)

    def test_all_fingerprints_returns_everything(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = os.path.join(folder, "cache.db")
            with FingerprintCache(db_path) as cache:
                cache.store(_image_fp("/lib/a.jpg"))
                cache.store(_image_fp("/lib/b.jpg"))
                cache.store(_video_fp("/lib/clip.mov"))
                all_fingerprints = cache.all_fingerprints()
            paths = {fp.path for fp in all_fingerprints}
            self.assertEqual(paths, {"/lib/a.jpg", "/lib/b.jpg", "/lib/clip.mov"})

    def test_forget_removes(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = os.path.join(folder, "cache.db")
            with FingerprintCache(db_path) as cache:
                cache.store(_image_fp("/lib/a.jpg"))
                cache.forget("/lib/a.jpg")
                self.assertIsNone(cache.lookup("/lib/a.jpg", 1024, 1700000000.0))


if __name__ == "__main__":
    unittest.main()
