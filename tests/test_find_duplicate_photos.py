"""End-to-end tests for find_duplicate_photos.py: scan -> report -> mark ->
delete by hand -> finalize.
"""

import io
import os
import shutil
import sys
import tempfile
import unittest

from PIL import Image

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

import find_duplicate_photos  # noqa: E402


def _write_solid_jpg(folder: str, name: str, color: tuple[int, int, int],
                     size_px: tuple[int, int] = (32, 32), comment: bytes | None = None) -> str:
    path = os.path.join(folder, name)
    buffer = io.BytesIO()
    save_kwargs = {"format": "JPEG", "quality": 80}
    if comment is not None:
        save_kwargs["comment"] = comment
    Image.new("RGB", size_px, color=color).save(buffer, **save_kwargs)
    with open(path, "wb") as fh:
        fh.write(buffer.getvalue())
    return path


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_find_dupes_')
        self.year_dir = os.path.join(self.tmpdir, '2026')
        os.makedirs(self.year_dir)
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir, ignore_errors=True))

    def test_scan_then_mark_then_finalize(self):
        # Two pixel-identical files (different EXIF/comment so file bytes differ).
        path_high_quality = _write_solid_jpg(
            self.year_dir, "2026-04-12 09.15.30_1.jpg",
            color=(200, 50, 50), comment=b"copy_a",
        )
        path_low_quality = _write_solid_jpg(
            self.year_dir, "2026-04-12 09.15.30_2.jpg",
            color=(200, 50, 50), comment=b"copy_b",
        )
        # A distinct file — should never be flagged as a duplicate.
        _write_solid_jpg(
            self.year_dir, "2026-04-12 09.16.00_1.jpg",
            color=(50, 50, 200),
        )

        # Scan: hashes all 3 images and stores them in the sidecar cache.
        scanned = find_duplicate_photos.scan(self.tmpdir)
        self.assertEqual(scanned, 3)

        # Mark: rename the duplicate pair with _a / _b.
        find_duplicate_photos.mark(self.tmpdir, dry_run=False)
        names = set(os.listdir(self.year_dir))
        # The bigger / lex-tiebreak winner gets _a (here both have same dimensions,
        # so size + lex break the tie; comment 'copy_a' lex-precedes 'copy_b' on
        # equal size, but they have equal size — so name 'a' first by path).
        self.assertTrue(any("_a.jpg" in n for n in names))
        self.assertTrue(any("_b.jpg" in n for n in names))
        # Distinct file untouched.
        self.assertIn("2026-04-12 09.16.00_1.jpg", names)

        # Simulate the manual review: user deletes the _b copy.
        b_file = next(n for n in os.listdir(self.year_dir) if n.endswith("_b.jpg"))
        os.remove(os.path.join(self.year_dir, b_file))

        # Finalize: strip the _a suffix back to canonical form.
        find_duplicate_photos.finalize(self.tmpdir, dry_run=False)
        names = set(os.listdir(self.year_dir))
        self.assertFalse(any("_a.jpg" in n for n in names))
        self.assertFalse(any("_b.jpg" in n for n in names))

    def test_dry_run_mark_does_not_touch_disk(self):
        _write_solid_jpg(
            self.year_dir, "2026-04-12 09.15.30_1.jpg",
            color=(200, 50, 50), comment=b"copy_a",
        )
        _write_solid_jpg(
            self.year_dir, "2026-04-12 09.15.30_2.jpg",
            color=(200, 50, 50), comment=b"copy_b",
        )
        find_duplicate_photos.scan(self.tmpdir)
        before = set(os.listdir(self.year_dir))
        find_duplicate_photos.mark(self.tmpdir, dry_run=True)
        after = set(os.listdir(self.year_dir))
        self.assertEqual(before, after)

    def test_report_writes_html(self):
        _write_solid_jpg(
            self.year_dir, "2026-04-12 09.15.30_1.jpg",
            color=(200, 50, 50), comment=b"copy_a",
        )
        _write_solid_jpg(
            self.year_dir, "2026-04-12 09.15.30_2.jpg",
            color=(200, 50, 50), comment=b"copy_b",
        )
        find_duplicate_photos.scan(self.tmpdir)
        report_path = os.path.join(self.tmpdir, "report.html")
        groups_reported = find_duplicate_photos.report(self.tmpdir, report_path)
        self.assertEqual(groups_reported, 1)
        with open(report_path, encoding="utf-8") as fh:
            html_text = fh.read()
        self.assertIn("Tier 2", html_text)

    def test_scan_cache_reuse_skips_unchanged_files(self):
        _write_solid_jpg(
            self.year_dir, "2026-04-12 09.15.30_1.jpg",
            color=(200, 50, 50),
        )
        first_scan = find_duplicate_photos.scan(self.tmpdir)
        second_scan = find_duplicate_photos.scan(self.tmpdir)
        self.assertEqual(first_scan, 1)
        self.assertEqual(second_scan, 0)  # cache hit

    def test_scan_idempotent_across_separator_forms(self):
        # Same library, two scan calls with different separator forms for
        # the root: the second call must hit the cache, not re-hash. Before
        # the path-normalisation fix, the second call stored a parallel set
        # of rows under the alt-separator keys and the planner saw every
        # file as its own duplicate.
        _write_solid_jpg(
            self.year_dir, "2026-04-12 09.15.30_1.jpg",
            color=(200, 50, 50),
        )
        # First scan with whatever os.path produces.
        first = find_duplicate_photos.scan(self.tmpdir)
        # Second scan with separators swapped (works on Windows; on POSIX
        # the input is already normalised so this still passes as a no-op).
        alt = self.tmpdir.replace(os.sep, "/" if os.sep == "\\" else os.sep)
        second = find_duplicate_photos.scan(alt)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)  # cache hit, no new rows


if __name__ == '__main__':
    unittest.main()
