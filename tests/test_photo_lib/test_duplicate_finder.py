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
        # The winner's _N (_1) becomes the SHARED group prefix, so both files
        # sort adjacent in File Explorer. Winner keeps _1 with _a; the dup at
        # _2 takes the winner's _1 with _b instead of staying at _2.
        self.assertEqual(
            plan_by_old["2026-04-12 09.15.30_1.jpg"],
            "2026-04-12 09.15.30_1_a.jpg",
        )
        self.assertEqual(
            plan_by_old["2026-04-12 09.15.30_2.jpg"],
            "2026-04-12 09.15.30_1_b.jpg",
        )

    def test_loser_uses_winner_idx_for_adjacency(self):
        # The whole point of the marked form: a dup at _14 doesn't stay at
        # _14_b (which would sort far away from the winner's _1_a). It uses
        # the winner's _1 so it sorts adjacent.
        group = duplicate_finder.DuplicateGroup(
            tier=1,
            fingerprints=[
                self._bare_fingerprint("/lib/2014-01-01 13.00.00_1.jpg", 100_000),
                self._bare_fingerprint("/lib/2014-01-01 13.00.00_14.jpg", 50_000),
                self._bare_fingerprint("/lib/2014-01-01 13.00.00_29.jpg", 50_000),
            ],
        )
        plan = duplicate_finder.plan_mark([group])
        new_names = sorted(os.path.basename(n) for _, n, _ in plan)
        self.assertEqual(new_names, [
            "2014-01-01 13.00.00_1_a.jpg",
            "2014-01-01 13.00.00_1_b.jpg",
            "2014-01-01 13.00.00_1_c.jpg",
        ])

    def test_each_file_keeps_its_own_extension_in_group(self):
        # A group can mix extensions (e.g., a video and its sidecar were
        # accidentally pixel-hashed the same way). Each file uses the
        # winner's _N as the shared prefix but its own extension on output.
        group = duplicate_finder.DuplicateGroup(
            tier=2,
            fingerprints=[
                self._bare_fingerprint("/lib/2014-01-01 13.00.00_1.heic", 100_000, width=200, height=200),
                self._bare_fingerprint("/lib/2014-01-01 13.00.00_5.mp4", 100_000, width=100, height=100),
            ],
        )
        plan = duplicate_finder.plan_mark([group])
        new_names = sorted(os.path.basename(n) for _, n, _ in plan)
        self.assertEqual(new_names, [
            "2014-01-01 13.00.00_1_a.heic",
            "2014-01-01 13.00.00_1_b.mp4",
        ])

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
        # _2 has the bigger size with equal dimensions — it wins _a and its
        # _2 becomes the shared group prefix.
        self.assertEqual(
            plan_by_old["2026-04-12 09.15.30_2.jpg"],
            "2026-04-12 09.15.30_2_a.jpg",
        )
        self.assertEqual(
            plan_by_old["2026-04-12 09.15.30_1.jpg"],
            "2026-04-12 09.15.30_2_b.jpg",
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


class TestPlanMarkCrossDate(unittest.TestCase):
    """When duplicates have different timestamps, the loser moves into the
    winner's folder and its filename carries an ``__from_<loser_base>`` marker
    so the original date stays visible during review."""

    def _bare_fingerprint(self, path: str, size: int, width: int = 100,
                          height: int = 100) -> duplicate_finder.FileFingerprint:
        return duplicate_finder.FileFingerprint(
            path=path, size=size, mtime=0.0,
            media_kind="image",
            file_sha256="x", pixel_sha256="y", phash_hex="z",
            frame_phashes_hex=None,
            width=width, height=height,
        )

    def test_cross_date_loser_moves_to_winner_folder_with_marker(self):
        # Winner is in 2014 folder; loser sits in 2015 folder under a
        # different timestamp. After mark, both files should be in the 2014
        # folder, with the loser's name carrying its original 2015 timestamp.
        group = duplicate_finder.DuplicateGroup(
            tier=2,
            fingerprints=[
                self._bare_fingerprint(
                    "/lib/2014/2014-06-15 10.00.00_1.jpg",
                    100_000, width=200, height=200,
                ),
                self._bare_fingerprint(
                    "/lib/2015/2015-08-20 14.30.00_3.jpg",
                    50_000, width=100, height=100,
                ),
            ],
        )
        plan = duplicate_finder.plan_mark([group])
        by_old = {old: (new, tier) for old, new, tier in plan}

        winner_new, _ = by_old["/lib/2014/2014-06-15 10.00.00_1.jpg"]
        loser_new, _ = by_old["/lib/2015/2015-08-20 14.30.00_3.jpg"]

        self.assertEqual(
            winner_new,
            os.path.join("/lib/2014", "2014-06-15 10.00.00_1_a.jpg"),
        )
        self.assertEqual(
            loser_new,
            os.path.join(
                "/lib/2014",
                "2014-06-15 10.00.00_1_b__from_2015-08-20 14.30.00.jpg",
            ),
        )

    def test_cross_date_loser_keeps_own_extension(self):
        # Same as above but loser is a .mov, winner a .heic — the marker form
        # must preserve each file's extension.
        group = duplicate_finder.DuplicateGroup(
            tier=2,
            fingerprints=[
                self._bare_fingerprint(
                    "/lib/2014/2014-06-15 10.00.00_1.heic",
                    100_000, width=200, height=200,
                ),
                self._bare_fingerprint(
                    "/lib/2015/2015-08-20 14.30.00_3.mov",
                    50_000, width=100, height=100,
                ),
            ],
        )
        plan = duplicate_finder.plan_mark([group])
        new_names = sorted(os.path.basename(n) for _, n, _ in plan)
        self.assertEqual(new_names, [
            "2014-06-15 10.00.00_1_a.heic",
            "2014-06-15 10.00.00_1_b__from_2015-08-20 14.30.00.mov",
        ])

    def test_three_distinct_dates_each_loser_marked_with_own_origin(self):
        # Winner from 2014, losers from 2015 and 2007 — three different
        # year folders end up consolidated into the winner's folder, each
        # loser carrying its own __from_<base> marker.
        group = duplicate_finder.DuplicateGroup(
            tier=1,
            fingerprints=[
                self._bare_fingerprint(
                    "/lib/2014/2014-06-15 10.00.00_1.jpg",
                    100_000, width=300, height=300,
                ),
                self._bare_fingerprint(
                    "/lib/2015/2015-08-20 14.30.00_2.jpg",
                    80_000, width=200, height=200,
                ),
                self._bare_fingerprint(
                    "/lib/2007/2007-03-11 18.25.00_5.jpg",
                    60_000, width=100, height=100,
                ),
            ],
        )
        plan = duplicate_finder.plan_mark([group])
        # All three end up in /lib/2014, sharing the winner's _1 prefix.
        for _, new_path, _ in plan:
            self.assertEqual(os.path.dirname(new_path), "/lib/2014")
        new_names = sorted(os.path.basename(n) for _, n, _ in plan)
        self.assertEqual(new_names, [
            "2014-06-15 10.00.00_1_a.jpg",
            "2014-06-15 10.00.00_1_b__from_2015-08-20 14.30.00.jpg",
            "2014-06-15 10.00.00_1_c__from_2007-03-11 18.25.00.jpg",
        ])

    def test_same_base_group_does_not_get_origin_marker(self):
        # Regression: when all members share <base>, the marker must NOT be
        # added — current behavior is preserved for same-timestamp groups.
        group = duplicate_finder.DuplicateGroup(
            tier=1,
            fingerprints=[
                self._bare_fingerprint("/lib/2014/2014-01-01 13.00.00_1.jpg", 100_000),
                self._bare_fingerprint("/lib/2014/2014-01-01 13.00.00_14.jpg", 50_000),
            ],
        )
        plan = duplicate_finder.plan_mark([group])
        for _, new_path, _ in plan:
            self.assertNotIn("__from_", os.path.basename(new_path))


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

    def test_lone_non_a_survivor_also_demoted(self):
        # If the user kept _b and deleted _a, the survivor still gets demoted
        # to the canonical idx — we don't require the survivor to be _a.
        self._touch("2026-04-12 09.15.30_1_b.jpg")
        plan = duplicate_finder.plan_finalize(self.tmpdir)
        self.assertEqual(len(plan), 1)
        self.assertEqual(os.path.basename(plan[0][1]),
                         "2026-04-12 09.15.30_1.jpg")

    def test_multi_survivor_each_gets_distinct_canonical_idx(self):
        # User kept _a and _b — they're not duplicates after all.
        # _a takes the group's original idx (1); _b gets the next free (2).
        self._touch("2026-04-12 09.15.30_1_a.jpg")
        self._touch("2026-04-12 09.15.30_1_b.jpg")
        plan = duplicate_finder.plan_finalize(self.tmpdir)
        by_old = {
            os.path.basename(old): os.path.basename(new) for old, new in plan
        }
        self.assertEqual(by_old["2026-04-12 09.15.30_1_a.jpg"],
                         "2026-04-12 09.15.30_1.jpg")
        self.assertEqual(by_old["2026-04-12 09.15.30_1_b.jpg"],
                         "2026-04-12 09.15.30_2.jpg")

    def test_multi_survivor_bumps_past_existing_canonical(self):
        # If _1, _2 are already canonical files in the folder, the surviving
        # _1_a and _1_b need to land beyond them.
        self._touch("2026-04-12 09.15.30_2.jpg")  # unrelated canonical file
        self._touch("2026-04-12 09.15.30_1_a.jpg")
        self._touch("2026-04-12 09.15.30_1_b.jpg")
        plan = duplicate_finder.plan_finalize(self.tmpdir)
        by_old = {
            os.path.basename(old): os.path.basename(new) for old, new in plan
        }
        # _a wants _1: free, takes it.
        # _b wants _2: taken by existing canonical, bumps to _3.
        self.assertEqual(by_old["2026-04-12 09.15.30_1_a.jpg"],
                         "2026-04-12 09.15.30_1.jpg")
        self.assertEqual(by_old["2026-04-12 09.15.30_1_b.jpg"],
                         "2026-04-12 09.15.30_3.jpg")

    def test_a_survivors_processed_before_b_overflow_across_groups(self):
        # Two adjacent groups both have _a and _b. Without two-pass ordering,
        # group _1's _b would grab the _2 slot before group _2's _a got a
        # chance to claim it. With two-pass, every _a is processed first.
        self._touch("2026-04-12 09.15.30_1_a.jpg")
        self._touch("2026-04-12 09.15.30_1_b.jpg")
        self._touch("2026-04-12 09.15.30_2_a.jpg")
        self._touch("2026-04-12 09.15.30_2_b.jpg")
        plan = duplicate_finder.plan_finalize(self.tmpdir)
        by_old = {
            os.path.basename(old): os.path.basename(new) for old, new in plan
        }
        # Both _a survivors get their preferred indices.
        self.assertEqual(by_old["2026-04-12 09.15.30_1_a.jpg"],
                         "2026-04-12 09.15.30_1.jpg")
        self.assertEqual(by_old["2026-04-12 09.15.30_2_a.jpg"],
                         "2026-04-12 09.15.30_2.jpg")
        # _b overflows have to share _3 and _4 (whichever comes first in
        # the deterministic processing order).
        b_targets = {
            by_old["2026-04-12 09.15.30_1_b.jpg"],
            by_old["2026-04-12 09.15.30_2_b.jpg"],
        }
        self.assertEqual(b_targets, {
            "2026-04-12 09.15.30_3.jpg",
            "2026-04-12 09.15.30_4.jpg",
        })

    def test_canonical_target_exists_demotes_to_next_free(self):
        # Pre-change: this case was skipped. New: bump to next free idx.
        self._touch("2026-04-12 09.15.30_1_a.jpg")
        self._touch("2026-04-12 09.15.30_1.jpg")  # already canonical at the target name
        plan = duplicate_finder.plan_finalize(self.tmpdir)
        self.assertEqual(len(plan), 1)
        self.assertEqual(os.path.basename(plan[0][1]),
                         "2026-04-12 09.15.30_2.jpg")


class TestPlanFinalizeCrossDate(unittest.TestCase):
    """Cross-date surviving losers (``_b/_c`` with ``__from_<base>`` marker)
    are sent back to their original year folder during finalize."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_finalize_xd_')
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _touch_in(self, subfolder: str, name: str) -> str:
        folder = os.path.join(self.tmpdir, subfolder) if subfolder else self.tmpdir
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "wb") as fh:
            fh.write(name.encode())
        return path

    def test_cross_date_loser_returns_to_origin_year_folder(self):
        # User reviewed and kept the _b, deciding it WASN'T a duplicate.
        # finalize should send it back to the 2015 folder under its original
        # base, allocating the lowest free idx (1, since no canonical files
        # exist in /2015 yet).
        self._touch_in("2014", "2014-06-15 10.00.00_1_a.jpg")
        self._touch_in(
            "2014",
            "2014-06-15 10.00.00_1_b__from_2015-08-20 14.30.00.jpg",
        )
        plan = duplicate_finder.plan_finalize(self.tmpdir)
        by_old = {
            os.path.relpath(old, self.tmpdir).replace("\\", "/"):
            os.path.relpath(new, self.tmpdir).replace("\\", "/")
            for old, new in plan
        }
        self.assertEqual(
            by_old["2014/2014-06-15 10.00.00_1_a.jpg"],
            "2014/2014-06-15 10.00.00_1.jpg",
        )
        self.assertEqual(
            by_old[
                "2014/2014-06-15 10.00.00_1_b__from_2015-08-20 14.30.00.jpg"
            ],
            "2015/2015-08-20 14.30.00_1.jpg",
        )

    def test_cross_date_returning_loser_bumps_past_taken_idx(self):
        # The 2015 folder already has a canonical file at _1, so the
        # returning _b has to take _2 instead.
        self._touch_in("2014", "2014-06-15 10.00.00_1_a.jpg")
        self._touch_in(
            "2014",
            "2014-06-15 10.00.00_1_b__from_2015-08-20 14.30.00.jpg",
        )
        self._touch_in("2015", "2015-08-20 14.30.00_1.jpg")
        plan = duplicate_finder.plan_finalize(self.tmpdir)
        by_old = {os.path.basename(old): new for old, new in plan}
        returned_path = by_old[
            "2014-06-15 10.00.00_1_b__from_2015-08-20 14.30.00.jpg"
        ]
        self.assertEqual(
            os.path.relpath(returned_path, self.tmpdir).replace("\\", "/"),
            "2015/2015-08-20 14.30.00_2.jpg",
        )

    def test_two_returning_losers_share_destination_bucket(self):
        # Two different cross-date groups both have a _b surviving with the
        # SAME origin base — they're going to the same destination bucket
        # and must get distinct idx.
        self._touch_in("2014", "2014-06-15 10.00.00_1_a.jpg")
        self._touch_in(
            "2014",
            "2014-06-15 10.00.00_1_b__from_2015-08-20 14.30.00.jpg",
        )
        self._touch_in("2014", "2014-09-09 12.00.00_1_a.jpg")
        self._touch_in(
            "2014",
            "2014-09-09 12.00.00_1_b__from_2015-08-20 14.30.00.jpg",
        )
        plan = duplicate_finder.plan_finalize(self.tmpdir)
        returned = sorted(
            os.path.relpath(new, self.tmpdir).replace("\\", "/")
            for old, new in plan
            if "__from_" in os.path.basename(old)
        )
        self.assertEqual(returned, [
            "2015/2015-08-20 14.30.00_1.jpg",
            "2015/2015-08-20 14.30.00_2.jpg",
        ])

    def test_bundled_early_year_routes_to_bundled_folder(self):
        # A 2005 origin should land in the "2000 - 2010" bundled folder, not
        # in a /2005/ folder, because the master library convention bundles
        # years 2000-2010.
        self._touch_in("2014", "2014-06-15 10.00.00_1_a.jpg")
        self._touch_in(
            "2014",
            "2014-06-15 10.00.00_1_b__from_2005-03-11 18.25.00.jpg",
        )
        plan = duplicate_finder.plan_finalize(self.tmpdir)
        returned = [
            new for old, new in plan
            if "__from_" in os.path.basename(old)
        ]
        self.assertEqual(len(returned), 1)
        self.assertEqual(
            os.path.relpath(returned[0], self.tmpdir).replace("\\", "/"),
            "2000 - 2010/2005-03-11 18.25.00_1.jpg",
        )

    def test_multi_survivor_mixed_a_stays_b_returns_home(self):
        # User kept BOTH the _a (in winner's folder) and the _b (cross-date
        # loser). _a strips to canonical and stays in 2014; _b is sent home
        # to 2015.
        self._touch_in("2014", "2014-06-15 10.00.00_1_a.jpg")
        self._touch_in(
            "2014",
            "2014-06-15 10.00.00_1_b__from_2015-08-20 14.30.00.jpg",
        )
        plan = duplicate_finder.plan_finalize(self.tmpdir)
        by_old = {
            os.path.basename(old):
            os.path.relpath(new, self.tmpdir).replace("\\", "/")
            for old, new in plan
        }
        self.assertEqual(
            by_old["2014-06-15 10.00.00_1_a.jpg"],
            "2014/2014-06-15 10.00.00_1.jpg",
        )
        self.assertEqual(
            by_old[
                "2014-06-15 10.00.00_1_b__from_2015-08-20 14.30.00.jpg"
            ],
            "2015/2015-08-20 14.30.00_1.jpg",
        )

    def test_origin_folder_missing_creates_on_apply(self):
        # /2015/ doesn't exist yet; finalize plans the rename, apply creates
        # the folder and moves the file in.
        self._touch_in(
            "2014",
            "2014-06-15 10.00.00_1_b__from_2015-08-20 14.30.00.jpg",
        )
        plan = duplicate_finder.plan_finalize(self.tmpdir)
        self.assertEqual(len(plan), 1)
        duplicate_finder.apply_simple_rename_plan(plan)
        expected = os.path.join(
            self.tmpdir, "2015", "2015-08-20 14.30.00_1.jpg",
        )
        self.assertTrue(
            os.path.exists(expected),
            f"Expected file at {expected}; tree: "
            f"{[os.path.relpath(os.path.join(d, f), self.tmpdir) for d, _, fs in os.walk(self.tmpdir) for f in fs]}",
        )

    def test_same_base_groups_in_subfolders_still_work(self):
        # Regression: existing same-base behavior must keep working when
        # marked files live in subfolders (the new tree-walk shouldn't
        # change the outcome for plain marks).
        self._touch_in("2014", "2014-06-15 10.00.00_1_a.jpg")
        self._touch_in("2014", "2014-06-15 10.00.00_1_b.jpg")
        plan = duplicate_finder.plan_finalize(self.tmpdir)
        by_old = {
            os.path.basename(old): os.path.basename(new)
            for old, new in plan
        }
        self.assertEqual(by_old["2014-06-15 10.00.00_1_a.jpg"],
                         "2014-06-15 10.00.00_1.jpg")
        self.assertEqual(by_old["2014-06-15 10.00.00_1_b.jpg"],
                         "2014-06-15 10.00.00_2.jpg")


class TestHammingDistance(unittest.TestCase):
    def test_identical_hashes_distance_zero(self):
        self.assertEqual(
            duplicate_finder.hamming_distance_hex("abcd1234", "abcd1234"), 0,
        )

    def test_single_bit_flip(self):
        # 0x00 vs 0x01 differ in 1 bit
        self.assertEqual(
            duplicate_finder.hamming_distance_hex("00", "01"), 1,
        )

    def test_all_bits_different(self):
        # 0xff vs 0x00 = 8 bits
        self.assertEqual(
            duplicate_finder.hamming_distance_hex("ff", "00"), 8,
        )


class TestLowEntropyPhash(unittest.TestCase):
    def test_all_zeros_is_low_entropy(self):
        self.assertTrue(duplicate_finder.is_low_entropy_phash("0000000000000000"))

    def test_all_ones_is_low_entropy(self):
        self.assertTrue(duplicate_finder.is_low_entropy_phash("ffffffffffffffff"))

    def test_balanced_phash_is_not_low_entropy(self):
        # 32 set bits out of 64 — perfectly balanced
        self.assertFalse(duplicate_finder.is_low_entropy_phash("00000000ffffffff"))

    def test_just_above_threshold_is_not_low_entropy(self):
        # 8 set bits — at the lower bound, should NOT be flagged
        self.assertFalse(duplicate_finder.is_low_entropy_phash("00000000000000ff"))

    def test_just_below_threshold_is_low_entropy(self):
        # 7 set bits — below the lower bound
        self.assertTrue(duplicate_finder.is_low_entropy_phash("000000000000007f"))


class TestFuzzyPhashGrouping(unittest.TestCase):
    """Threshold > 0 enables Hamming-distance pHash matching for tier 3."""

    def _fp(self, path: str, phash_hex: str) -> duplicate_finder.FileFingerprint:
        return duplicate_finder.FileFingerprint(
            path=path, size=1000, mtime=0.0, media_kind="image",
            file_sha256=f"file_{path}", pixel_sha256=f"pix_{path}",
            phash_hex=phash_hex, frame_phashes_hex=None,
            width=100, height=100,
        )

    def test_threshold_zero_requires_exact_match(self):
        # 4 bits apart (4 vs b in the last hex = 0100 vs 1011) — must NOT group at threshold 0.
        groups = duplicate_finder.group_duplicates(
            [
                self._fp("/lib/a.jpg", "abcd1234abcd1234"),
                self._fp("/lib/b.jpg", "abcd1234abcd123b"),
            ],
            phash_hamming_threshold=0,
        )
        self.assertEqual(groups, [])

    def test_threshold_above_distance_groups(self):
        # 4 bits apart, threshold 5 — must group.
        groups = duplicate_finder.group_duplicates(
            [
                self._fp("/lib/a.jpg", "abcd1234abcd1234"),
                self._fp("/lib/b.jpg", "abcd1234abcd123b"),
            ],
            phash_hamming_threshold=5,
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].tier, 3)
        self.assertEqual(len(groups[0].fingerprints), 2)

    def test_threshold_below_distance_no_group(self):
        # 8 bits apart (34 vs cb = 00110100 vs 11001011), threshold 3 — no group.
        groups = duplicate_finder.group_duplicates(
            [
                self._fp("/lib/a.jpg", "abcd1234abcd1234"),
                self._fp("/lib/b.jpg", "abcd1234abcd12cb"),
            ],
            phash_hamming_threshold=3,
        )
        self.assertEqual(groups, [])

    def test_low_entropy_phashes_excluded_from_fuzzy_match(self):
        # Two all-zero pHashes would trivially match at any threshold, but the
        # low-entropy filter should drop them so they don't form a fake group.
        groups = duplicate_finder.group_duplicates(
            [
                self._fp("/lib/black1.jpg", "0000000000000000"),
                self._fp("/lib/black2.jpg", "0000000000000000"),
            ],
            phash_hamming_threshold=8,
        )
        # Exact-equality tier 3 catches this pair regardless of low-entropy
        # filter (exact match is unambiguous), so we expect 1 group of 2.
        # The low-entropy filter only kicks in for FUZZY (distance > 0).
        self.assertEqual(len(groups), 1)

    def test_low_entropy_phashes_excluded_when_distance_is_nonzero(self):
        # Two low-entropy pHashes 2 bits apart at threshold 8 — must NOT group.
        groups = duplicate_finder.group_duplicates(
            [
                self._fp("/lib/black1.jpg", "0000000000000000"),
                self._fp("/lib/black2.jpg", "0000000000000003"),  # 2 bits different
            ],
            phash_hamming_threshold=8,
        )
        self.assertEqual(groups, [])

    def test_transitive_clustering_via_union_find(self):
        # A vs B distance 4, B vs C distance 4. Threshold 5 unions all three
        # even though A vs C may be larger.
        groups = duplicate_finder.group_duplicates(
            [
                self._fp("/lib/a.jpg", "abcd1234abcd1234"),
                self._fp("/lib/b.jpg", "abcd1234abcd123b"),  # 4 from a
                self._fp("/lib/c.jpg", "abcd1234abcd12c4"),  # 4 from a (last 8 bits 34 -> c4)
            ],
            phash_hamming_threshold=5,
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].fingerprints), 3)

    def test_exact_match_claims_files_before_fuzzy_pass(self):
        # A and B share an exact pHash; C is fuzzy-distance 4 away.
        # Exact-match pass groups A+B first; fuzzy pass only looks at
        # unclaimed remainders, so C ends up alone.
        groups = duplicate_finder.group_duplicates(
            [
                self._fp("/lib/a.jpg", "abcd1234abcd1234"),
                self._fp("/lib/b.jpg", "abcd1234abcd1234"),
                self._fp("/lib/c.jpg", "abcd1234abcd123b"),
            ],
            phash_hamming_threshold=5,
        )
        self.assertEqual(len(groups), 1)
        paths = {fp.path for fp in groups[0].fingerprints}
        self.assertEqual(paths, {"/lib/a.jpg", "/lib/b.jpg"})


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
