"""Tests for photo_lib.source_manifest — sidecar SQLite + label derivation."""

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

from photo_lib.source_manifest import (  # noqa: E402
    DEFAULT_MANIFEST_FILENAME,
    SourceManifest,
    default_manifest_path,
    derive_source_label,
    sanitize_source_label,
)


class TestSanitizeSourceLabel(unittest.TestCase):
    def test_spaces_become_hyphens(self):
        self.assertEqual(sanitize_source_label("Pictures and Videos"), "Pictures-and-Videos")

    def test_runs_of_specials_collapse_to_one_hyphen(self):
        self.assertEqual(sanitize_source_label("foo!!!bar"), "foo-bar")
        self.assertEqual(sanitize_source_label("a   b"), "a-b")

    def test_leading_and_trailing_specials_trimmed(self):
        self.assertEqual(sanitize_source_label("  hello  "), "hello")
        self.assertEqual(sanitize_source_label("__foo__"), "foo")

    def test_alphanumeric_preserved(self):
        self.assertEqual(sanitize_source_label("PhotosCopy"), "PhotosCopy")
        self.assertEqual(sanitize_source_label("USB2024"), "USB2024")


class TestDeriveSourceLabel(unittest.TestCase):
    def test_basename_used_when_not_year(self):
        self.assertEqual(derive_source_label("/data/PhotosCopy"), "PhotosCopy")

    def test_year_basename_steps_up_to_parent(self):
        # Sourcing from D:\Photos\2014 must yield "Photos", not "2014" —
        # otherwise two pilot sources both ending in 2014 would collide.
        path = os.path.join("data", "Photos", "2014")
        self.assertEqual(derive_source_label(path), "Photos")

    def test_bundled_year_range_basename_steps_up_to_parent(self):
        path = os.path.join("data", "Library", "2000 - 2010")
        self.assertEqual(derive_source_label(path), "Library")

    def test_spaces_in_parent_get_sanitised(self):
        path = os.path.join("data", "Pictures and Videos", "2014")
        self.assertEqual(derive_source_label(path), "Pictures-and-Videos")

    def test_trailing_separator_does_not_break_basename(self):
        self.assertEqual(derive_source_label("/data/PhotosCopy/"), "PhotosCopy")


class TestSourceManifest(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tempdir.name, "manifest.db")

    def tearDown(self):
        self._tempdir.cleanup()

    def test_default_path_is_dotfile_under_root(self):
        path = default_manifest_path("/some/root")
        self.assertTrue(path.endswith(DEFAULT_MANIFEST_FILENAME))

    def test_set_and_lookup_round_trip(self):
        with SourceManifest(self.db_path) as manifest:
            manifest.set("/lib/2014-06-15 10.00.00_1.jpg", "PhotosCopy")
            self.assertEqual(
                manifest.lookup("/lib/2014-06-15 10.00.00_1.jpg"),
                "PhotosCopy",
            )

    def test_lookup_missing_returns_none(self):
        with SourceManifest(self.db_path) as manifest:
            self.assertIsNone(manifest.lookup("/lib/nope.jpg"))

    def test_set_replaces_existing_label(self):
        with SourceManifest(self.db_path) as manifest:
            manifest.set("/lib/a.jpg", "Old")
            manifest.set("/lib/a.jpg", "New")
            self.assertEqual(manifest.lookup("/lib/a.jpg"), "New")

    def test_rename_preserves_label(self):
        with SourceManifest(self.db_path) as manifest:
            manifest.set("/lib/old.jpg", "PhotosCopy")
            moved = manifest.rename("/lib/old.jpg", "/lib/new.jpg")
            self.assertTrue(moved)
            self.assertIsNone(manifest.lookup("/lib/old.jpg"))
            self.assertEqual(manifest.lookup("/lib/new.jpg"), "PhotosCopy")

    def test_rename_missing_returns_false(self):
        with SourceManifest(self.db_path) as manifest:
            moved = manifest.rename("/lib/nope.jpg", "/lib/somewhere.jpg")
            self.assertFalse(moved)

    def test_forget_removes_entry(self):
        with SourceManifest(self.db_path) as manifest:
            manifest.set("/lib/a.jpg", "X")
            manifest.forget("/lib/a.jpg")
            self.assertIsNone(manifest.lookup("/lib/a.jpg"))

    def test_path_keys_are_normalised(self):
        # Forward and back slashes should map to the same row on Windows,
        # mirroring the duplicate_cache canonicalisation.
        with SourceManifest(self.db_path) as manifest:
            manifest.set("D:/lib/a.jpg", "X")
            self.assertEqual(manifest.lookup(os.path.normpath("D:/lib/a.jpg")), "X")

    def test_rename_many_handles_swapping_pairs(self):
        # Plan renames A -> B and B -> C in one batch. A naive per-row UPDATE
        # would crash on UNIQUE when the first step tries to write to B,
        # which still has its own row. rename_many must stage through temp
        # keys to handle this.
        with SourceManifest(self.db_path) as manifest:
            manifest.set("/lib/A.jpg", "label_a")
            manifest.set("/lib/B.jpg", "label_b")
            moved = manifest.rename_many([
                ("/lib/A.jpg", "/lib/B.jpg"),
                ("/lib/B.jpg", "/lib/C.jpg"),
            ])
            self.assertEqual(moved, 2)
            self.assertEqual(manifest.lookup("/lib/B.jpg"), "label_a")
            self.assertEqual(manifest.lookup("/lib/C.jpg"), "label_b")
            self.assertIsNone(manifest.lookup("/lib/A.jpg"))

    def test_rename_many_normalize_bucket_renumber_pattern(self):
        # Reproduces the exact crash from the pilot: a bucket renumber that
        # produces "_2.MOV -> _4.mov" while another row (_4.mov) is itself
        # planned to be renamed to _8.mov later in the same plan. The
        # naive per-row implementation crashes on UNIQUE constraint when
        # step 1 tries to write to _4.mov.
        with SourceManifest(self.db_path) as manifest:
            manifest.set("/lib/X_2.MOV", "master")
            manifest.set("/lib/X_4.mov", "takeout")
            moved = manifest.rename_many([
                ("/lib/X_2.MOV", "/lib/X_4.mov"),
                ("/lib/X_4.mov", "/lib/X_8.mov"),
            ])
            self.assertEqual(moved, 2)
            self.assertEqual(manifest.lookup("/lib/X_4.mov"), "master")
            self.assertEqual(manifest.lookup("/lib/X_8.mov"), "takeout")

    def test_rename_many_missing_source_rows_silently_skipped(self):
        # The plan may include paths that the manifest doesn't know about
        # (e.g. files added after combine). Those should be no-ops, not
        # errors — keeps the manifest tolerant.
        with SourceManifest(self.db_path) as manifest:
            manifest.set("/lib/known.jpg", "X")
            moved = manifest.rename_many([
                ("/lib/known.jpg", "/lib/known-new.jpg"),
                ("/lib/orphan.jpg", "/lib/orphan-new.jpg"),
            ])
            self.assertEqual(moved, 1)
            self.assertEqual(manifest.lookup("/lib/known-new.jpg"), "X")
            self.assertIsNone(manifest.lookup("/lib/orphan-new.jpg"))

    def test_rename_many_empty_is_noop(self):
        with SourceManifest(self.db_path) as manifest:
            self.assertEqual(manifest.rename_many([]), 0)

    def test_all_entries_returns_full_dict(self):
        with SourceManifest(self.db_path) as manifest:
            manifest.set("/lib/a.jpg", "X")
            manifest.set("/lib/b.jpg", "Y")
            entries = manifest.all_entries()
            self.assertEqual(len(entries), 2)
            self.assertIn("X", entries.values())
            self.assertIn("Y", entries.values())


if __name__ == "__main__":
    unittest.main()
