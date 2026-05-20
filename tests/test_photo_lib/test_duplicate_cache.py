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
                # Use normpath at construction so the test passes on both
                # Windows (which converts / to \) and POSIX (no-op).
                stored = _image_fp(os.path.normpath("/lib/a.jpg"))
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
            # Paths come back in canonical form (separators normalised), so
            # construct the expected set the same way.
            paths = {fp.path for fp in all_fingerprints}
            expected = {os.path.normpath(p) for p in (
                "/lib/a.jpg", "/lib/b.jpg", "/lib/clip.mov",
            )}
            self.assertEqual(paths, expected)

    def test_forget_removes(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = os.path.join(folder, "cache.db")
            with FingerprintCache(db_path) as cache:
                cache.store(_image_fp("/lib/a.jpg"))
                cache.forget("/lib/a.jpg")
                self.assertIsNone(cache.lookup("/lib/a.jpg", 1024, 1700000000.0))

    def test_rename_updates_path_key(self):
        # After a mark step renames a file on disk, the cache rename must
        # preserve the same hash data under the new path so a subsequent
        # report can read it without a re-scan.
        with tempfile.TemporaryDirectory() as folder:
            db_path = os.path.join(folder, "cache.db")
            with FingerprintCache(db_path) as cache:
                stored = _image_fp("/lib/a.jpg")
                cache.store(stored)
                moved = cache.rename("/lib/a.jpg", "/lib/a_a.jpg")
                self.assertTrue(moved)
                self.assertIsNone(cache.lookup("/lib/a.jpg", stored.size, stored.mtime))
                got = cache.lookup("/lib/a_a.jpg", stored.size, stored.mtime)
                self.assertIsNotNone(got)
                self.assertEqual(got.file_sha256, stored.file_sha256)

    def test_rename_missing_path_returns_false(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = os.path.join(folder, "cache.db")
            with FingerprintCache(db_path) as cache:
                self.assertFalse(cache.rename("/lib/nope.jpg", "/lib/nope_a.jpg"))

    def test_mixed_separator_lookup_finds_stored_entry(self):
        # Path-normalisation at the cache boundary: storing with one
        # separator form must still be findable via the other.
        # `os.path.join` here mimics what real callers produce — on Windows
        # both forms end up canonicalised by `os.path.normpath`.
        with tempfile.TemporaryDirectory() as folder:
            db_path = os.path.join(folder, "cache.db")
            with FingerprintCache(db_path) as cache:
                stored_path = os.path.normpath("/lib/sub/a.jpg")
                stored = _image_fp(stored_path)
                cache.store(stored)
                # Look up using a path with a redundant "." segment — must
                # still match because normpath collapses ".".
                got = cache.lookup(
                    "/lib/./sub/a.jpg", stored.size, stored.mtime,
                )
                self.assertIsNotNone(got)
                self.assertEqual(got.file_sha256, stored.file_sha256)

    def test_store_normalises_redundant_path_segments(self):
        # Two stores with logically-equivalent paths (one canonical, one
        # with a "./" segment) collapse to a single row, not two.
        with tempfile.TemporaryDirectory() as folder:
            db_path = os.path.join(folder, "cache.db")
            with FingerprintCache(db_path) as cache:
                canonical = os.path.normpath("/lib/sub/a.jpg")
                cache.store(_image_fp(canonical, size=1000))
                cache.store(_image_fp("/lib/./sub/a.jpg", size=2000))
                all_fingerprints = cache.all_fingerprints()
            self.assertEqual(len(all_fingerprints), 1)
            # Second store replaced the first (INSERT OR REPLACE semantics).
            self.assertEqual(all_fingerprints[0].size, 2000)

    def test_rename_handles_mixed_separator_old_key(self):
        # rename should find the entry even if the caller passes a
        # non-canonical old path.
        with tempfile.TemporaryDirectory() as folder:
            db_path = os.path.join(folder, "cache.db")
            with FingerprintCache(db_path) as cache:
                stored = _image_fp(os.path.normpath("/lib/sub/a.jpg"))
                cache.store(stored)
                moved = cache.rename("/lib/./sub/a.jpg", "/lib/sub/a_a.jpg")
                self.assertTrue(moved)


if __name__ == "__main__":
    unittest.main()
