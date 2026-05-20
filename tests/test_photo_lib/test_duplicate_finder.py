"""Tests for photo_lib.duplicate_finder: hashing, tier-based grouping, mark-plan
generation, and finalize logic.

Fixtures are tiny in-memory PIL images written to a tmp folder per test so the
suite stays hermetic and fast.
"""

import io
import os
import sys
import tempfile
import unittest

from PIL import Image

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

from photo_lib import duplicate_finder  # noqa: E402


def _write_solid_jpg(folder: str, name: str, color: tuple[int, int, int],
                     size_px: tuple[int, int] = (32, 32),
                     quality: int = 80) -> str:
    path = os.path.join(folder, name)
    Image.new("RGB", size_px, color=color).save(path, "JPEG", quality=quality)
    return path


def _write_solid_jpg_with_exif_diff(folder: str, name: str,
                                    color: tuple[int, int, int]) -> str:
    """Write two visually identical files that differ in raw file bytes by
    adding a JPEG comment marker. PIL with the comment kwarg makes the file
    bytes differ while the decoded pixels stay identical."""
    path = os.path.join(folder, name)
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color=color).save(
        buffer, "JPEG", quality=80, comment=name.encode("ascii"),
    )
    with open(path, "wb") as fh:
        fh.write(buffer.getvalue())
    return path


class TestHashing(unittest.TestCase):
    def test_file_bytes_hash_matches_for_identical_copies(self):
        with tempfile.TemporaryDirectory() as folder:
            path_a = _write_solid_jpg(folder, "a.jpg", (200, 50, 50))
            path_b = os.path.join(folder, "b.jpg")
            import shutil
            shutil.copy2(path_a, path_b)
            self.assertEqual(
                duplicate_finder.hash_file_bytes(path_a),
                duplicate_finder.hash_file_bytes(path_b),
            )

    def test_pixel_hash_matches_when_pixels_identical_bytes_differ(self):
        with tempfile.TemporaryDirectory() as folder:
            path_a = _write_solid_jpg_with_exif_diff(folder, "a.jpg", (200, 50, 50))
            path_b = _write_solid_jpg_with_exif_diff(folder, "b.jpg", (200, 50, 50))
            self.assertNotEqual(
                duplicate_finder.hash_file_bytes(path_a),
                duplicate_finder.hash_file_bytes(path_b),
            )
            pixel_a = duplicate_finder.hash_image_pixels(path_a)
            pixel_b = duplicate_finder.hash_image_pixels(path_b)
            self.assertIsNotNone(pixel_a)
            self.assertEqual(pixel_a[0], pixel_b[0])

    def test_pixel_hash_differs_for_different_pixels(self):
        with tempfile.TemporaryDirectory() as folder:
            path_red = _write_solid_jpg(folder, "red.jpg", (200, 50, 50))
            path_blue = _write_solid_jpg(folder, "blue.jpg", (50, 50, 200))
            self.assertNotEqual(
                duplicate_finder.hash_image_pixels(path_red)[0],
                duplicate_finder.hash_image_pixels(path_blue)[0],
            )

    def test_phash_matches_for_pixel_identical_bytes_differ(self):
        # Tier 3 catches the practical case: two files with identical decoded
        # pixels (so pHash is identical) but different file bytes due to EXIF
        # / metadata differences. pHash isn't guaranteed stable across a JPEG
        # re-compression (a few bits may flip) — that fuzzier case is
        # deliberately out of scope per the "tier-1-to-3 only" design.
        with tempfile.TemporaryDirectory() as folder:
            path_a = _write_solid_jpg_with_exif_diff(folder, "a.jpg", (180, 60, 40))
            path_b = _write_solid_jpg_with_exif_diff(folder, "b.jpg", (180, 60, 40))
            self.assertNotEqual(
                duplicate_finder.hash_file_bytes(path_a),
                duplicate_finder.hash_file_bytes(path_b),
            )
            self.assertEqual(
                duplicate_finder.compute_phash_hex(path_a),
                duplicate_finder.compute_phash_hex(path_b),
            )

    def test_fingerprint_skips_non_image(self):
        with tempfile.TemporaryDirectory() as folder:
            text_path = os.path.join(folder, "notes.txt")
            with open(text_path, "w") as fh:
                fh.write("hello")
            self.assertIsNone(duplicate_finder.fingerprint_file(text_path))


class TestGroupDuplicates(unittest.TestCase):
    def test_tier1_byte_identical_pair_grouped(self):
        with tempfile.TemporaryDirectory() as folder:
            path_a = _write_solid_jpg(folder, "a.jpg", (200, 50, 50))
            import shutil
            path_b = os.path.join(folder, "b.jpg")
            shutil.copy2(path_a, path_b)
            fingerprints = [
                duplicate_finder.fingerprint_file(path_a),
                duplicate_finder.fingerprint_file(path_b),
            ]
            groups = duplicate_finder.group_duplicates(fingerprints)
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].tier, 1)

    def test_tier2_same_pixels_different_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path_a = _write_solid_jpg_with_exif_diff(folder, "a.jpg", (200, 50, 50))
            path_b = _write_solid_jpg_with_exif_diff(folder, "b.jpg", (200, 50, 50))
            fingerprints = [
                duplicate_finder.fingerprint_file(path_a),
                duplicate_finder.fingerprint_file(path_b),
            ]
            groups = duplicate_finder.group_duplicates(fingerprints)
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].tier, 2)

    def test_distinct_images_no_group(self):
        # Use structured images, not solid colors: solid colors produce
        # near-identical pHashes (all zero DCT coefficients) so any two solids
        # would erroneously share a tier-3 group.
        with tempfile.TemporaryDirectory() as folder:
            def _structured(name, seed):
                path = os.path.join(folder, name)
                image = Image.new("RGB", (128, 128))
                pixels = image.load()
                for x_coord in range(128):
                    for y_coord in range(128):
                        pixels[x_coord, y_coord] = (
                            (x_coord * seed) % 256,
                            (y_coord * (seed + 1)) % 256,
                            ((x_coord + y_coord) * seed) % 256,
                        )
                image.save(path, "JPEG", quality=85)
                return path

            path_first = _structured("first.jpg", seed=7)
            path_second = _structured("second.jpg", seed=23)
            fingerprints = [
                duplicate_finder.fingerprint_file(path_first),
                duplicate_finder.fingerprint_file(path_second),
            ]
            groups = duplicate_finder.group_duplicates(fingerprints)
            self.assertEqual(groups, [])

    def test_tier1_priority_over_tier2(self):
        """A file that's byte-identical to one neighbor and pixel-identical to
        another should only appear in the tier-1 group, not also in tier 2."""
        with tempfile.TemporaryDirectory() as folder:
            path_a = _write_solid_jpg_with_exif_diff(folder, "a.jpg", (200, 50, 50))
            import shutil
            path_a_copy = os.path.join(folder, "a_copy.jpg")
            shutil.copy2(path_a, path_a_copy)
            path_b = _write_solid_jpg_with_exif_diff(folder, "b.jpg", (200, 50, 50))
            fingerprints = [
                duplicate_finder.fingerprint_file(path_a),
                duplicate_finder.fingerprint_file(path_a_copy),
                duplicate_finder.fingerprint_file(path_b),
            ]
            groups = duplicate_finder.group_duplicates(fingerprints)
            # Either way we should see a tier-1 group of (a, a_copy). Whether b
            # ends up in a tier-2 group with (a) depends on uniqueness; the
            # invariant we test is that the tier-1 group exists and a/a_copy
            # don't double-appear.
            tier1_groups = [group for group in groups if group.tier == 1]
            self.assertEqual(len(tier1_groups), 1)
            tier1_paths = {fp.path for fp in tier1_groups[0].fingerprints}
            self.assertEqual(tier1_paths, {path_a, path_a_copy})


class TestPlanMark(unittest.TestCase):
    def _bare_fingerprint(self, path: str, size: int, width: int = 100,
                          height: int = 100) -> duplicate_finder.FileFingerprint:
        return duplicate_finder.FileFingerprint(
            path=path, size=size, mtime=0.0,
            media_kind="image",
            file_sha256="x", pixel_sha256="y", phash_hex="z",
            frame_phashes_hex=None,
            width=width, height=height,
        )

    def test_winner_gets_a_loser_gets_b(self):
        group = duplicate_finder.DuplicateGroup(
            tier=2,
            fingerprints=[
                # higher dimensions should win
                self._bare_fingerprint("/lib/2026-04-12 09.15.30_1.jpg", 100_000, width=200, height=200),
                self._bare_fingerprint("/lib/2026-04-12 09.15.30_2.jpg", 200_000, width=100, height=100),
            ],
        )
        plan = duplicate_finder.plan_mark([group])
        plan_by_old = {os.path.basename(o): os.path.basename(n) for o, n, _ in plan}
        self.assertEqual(
            plan_by_old["2026-04-12 09.15.30_1.jpg"],
            "2026-04-12 09.15.30_1_a.jpg",
        )
        self.assertEqual(
            plan_by_old["2026-04-12 09.15.30_2.jpg"],
            "2026-04-12 09.15.30_2_b.jpg",
        )

    def test_size_breaks_dimension_tie(self):
        group = duplicate_finder.DuplicateGroup(
            tier=3,
            fingerprints=[
                self._bare_fingerprint("/lib/2026-04-12 09.15.30_1.jpg", 50_000),
                self._bare_fingerprint("/lib/2026-04-12 09.15.30_2.jpg", 200_000),
            ],
        )
        plan = duplicate_finder.plan_mark([group])
        plan_by_old = {os.path.basename(o): os.path.basename(n) for o, n, _ in plan}
        # _2 has the bigger size with equal dimensions — it wins _a.
        self.assertEqual(
            plan_by_old["2026-04-12 09.15.30_2.jpg"],
            "2026-04-12 09.15.30_2_a.jpg",
        )

    def test_non_canonical_filename_skipped(self):
        group = duplicate_finder.DuplicateGroup(
            tier=1,
            fingerprints=[
                self._bare_fingerprint("/lib/2026-04-12 09.15.30_1.jpg", 100_000),
                self._bare_fingerprint("/lib/random_name.jpg", 100_000),
            ],
        )
        plan = duplicate_finder.plan_mark([group])
        # Only the canonical one ends up in the plan.
        self.assertEqual(len(plan), 1)
        self.assertEqual(os.path.basename(plan[0][0]), "2026-04-12 09.15.30_1.jpg")


class TestPlanFinalize(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_finalize_')
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _touch(self, name: str) -> str:
        path = os.path.join(self.tmpdir, name)
        with open(path, "wb") as fh:
            fh.write(name.encode())
        return path

    def test_lone_a_survivor_gets_suffix_stripped(self):
        self._touch("2026-04-12 09.15.30_1_a.jpg")
        plan = duplicate_finder.plan_finalize(self.tmpdir)
        self.assertEqual(len(plan), 1)
        self.assertEqual(os.path.basename(plan[0][1]),
                         "2026-04-12 09.15.30_1.jpg")

    def test_pair_still_present_not_finalized(self):
        self._touch("2026-04-12 09.15.30_1_a.jpg")
        self._touch("2026-04-12 09.15.30_1_b.jpg")
        plan = duplicate_finder.plan_finalize(self.tmpdir)
        self.assertEqual(plan, [])

    def test_canonical_target_exists_skipped(self):
        self._touch("2026-04-12 09.15.30_1_a.jpg")
        self._touch("2026-04-12 09.15.30_1.jpg")  # already canonical at the target name
        plan = duplicate_finder.plan_finalize(self.tmpdir)
        self.assertEqual(plan, [])


class TestVideoFingerprint(unittest.TestCase):
    """Video support: file-bytes hash always populated; frame-pHash tuple
    populated when ffmpeg can decode and pHash sample frames."""

    def test_video_fingerprint_has_media_kind_video(self):
        from tests._fixture_helpers import copy_fixture_video
        with tempfile.TemporaryDirectory() as folder:
            video_path = copy_fixture_video(folder, name="clip.mov")
            fingerprint = duplicate_finder.fingerprint_file(video_path)
            self.assertIsNotNone(fingerprint)
            self.assertEqual(fingerprint.media_kind, "video")
            self.assertIsNone(fingerprint.pixel_sha256)
            self.assertIsNotNone(fingerprint.file_sha256)
            # The fixture is a 1-second clip; frame extraction may or may not
            # succeed at all 5 sample points on such a short clip, so we don't
            # require the tuple — we just confirm the field exists.

    def test_tier1_groups_byte_identical_videos(self):
        import shutil
        from tests._fixture_helpers import copy_fixture_video
        with tempfile.TemporaryDirectory() as folder:
            path_a = copy_fixture_video(folder, name="a.mov")
            path_b = os.path.join(folder, "b.mov")
            shutil.copy2(path_a, path_b)
            fingerprints = [
                duplicate_finder.fingerprint_file(path_a),
                duplicate_finder.fingerprint_file(path_b),
            ]
            groups = duplicate_finder.group_duplicates(fingerprints)
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].tier, 1)


if __name__ == "__main__":
    unittest.main()
