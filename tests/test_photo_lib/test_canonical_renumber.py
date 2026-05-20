"""Tests for photo_lib.canonical_renumber: extension canonicalization +
per-timestamp bucket renumbering, including the two-phase rename and the
gap-closing / cross-extension-uniqueness invariants.
"""

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

from photo_lib import canonical_renumber  # noqa: E402


def _touch(folder: str, name: str) -> str:
    path = os.path.join(folder, name)
    with open(path, "wb") as fh:
        fh.write(name.encode())  # distinct content per file so we can verify identity post-rename
    return path


def _names(folder: str) -> set[str]:
    return {entry.name for entry in os.scandir(folder) if entry.is_file()}


class TestPlanRenamesForFolder(unittest.TestCase):
    def test_noop_on_already_canonical_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            _touch(folder, "2026-04-12 09.15.30_1.jpg")
            _touch(folder, "2026-04-12 09.15.30_2.jpg")
            plan = canonical_renumber.plan_renames_for_folder(folder)
            self.assertEqual(plan, [])

    def test_jpeg_renames_to_jpg(self):
        with tempfile.TemporaryDirectory() as folder:
            _touch(folder, "2026-04-12 09.15.30_1.jpeg")
            plan = canonical_renumber.plan_renames_for_folder(folder)
            self.assertEqual(len(plan), 1)
            self.assertEqual(os.path.basename(plan[0].new_path),
                             "2026-04-12 09.15.30_1.jpg")
            self.assertIn("ext jpeg->jpg", plan[0].reason)

    def test_uppercase_extensions_lowercase(self):
        with tempfile.TemporaryDirectory() as folder:
            _touch(folder, "2026-04-12 09.15.30_1.JPG")
            _touch(folder, "2026-04-12 09.15.30_2.HEIC")
            plan = canonical_renumber.plan_renames_for_folder(folder)
            new_names = sorted(os.path.basename(p.new_path) for p in plan)
            self.assertEqual(new_names, [
                "2026-04-12 09.15.30_1.jpg",
                "2026-04-12 09.15.30_2.heic",
            ])

    def test_global_counter_across_extensions(self):
        """A bucket can't hold both _1.jpg and _1.mp4 — the second gets _2."""
        with tempfile.TemporaryDirectory() as folder:
            _touch(folder, "2026-04-12 09.15.30_1.jpg")
            _touch(folder, "2026-04-12 09.15.30_1.mp4")
            plan = canonical_renumber.plan_renames_for_folder(folder)
            self.assertEqual(len(plan), 1)
            # Sort order is (idx, ext) so .jpg comes before .mp4; .jpg keeps
            # _1 and the .mp4 is bumped to _2.
            self.assertEqual(os.path.basename(plan[0].old_path),
                             "2026-04-12 09.15.30_1.mp4")
            self.assertEqual(os.path.basename(plan[0].new_path),
                             "2026-04-12 09.15.30_2.mp4")

    def test_jpeg_jpg_collision_resolves_with_bump(self):
        """The 2011 case: _1.jpeg + _1.jpg in the same folder. After canonicalization
        both want to be .jpg — the .jpeg sorts first (alphabetical), keeps _1,
        and the original .jpg gets bumped to _2."""
        with tempfile.TemporaryDirectory() as folder:
            _touch(folder, "2011-01-01 13.00.00_1.jpeg")
            _touch(folder, "2011-01-01 13.00.00_1.jpg")
            plan = canonical_renumber.plan_renames_for_folder(folder)
            self.assertEqual(len(plan), 2)
            renames_by_old = {
                os.path.basename(p.old_path): os.path.basename(p.new_path)
                for p in plan
            }
            self.assertEqual(renames_by_old["2011-01-01 13.00.00_1.jpeg"],
                             "2011-01-01 13.00.00_1.jpg")
            self.assertEqual(renames_by_old["2011-01-01 13.00.00_1.jpg"],
                             "2011-01-01 13.00.00_2.jpg")

    def test_gap_closing(self):
        """Deleting _3 leaves a gap: _1, _2, _4. Next run pulls _4 down to _3."""
        with tempfile.TemporaryDirectory() as folder:
            _touch(folder, "2026-04-12 09.15.30_1.jpg")
            _touch(folder, "2026-04-12 09.15.30_2.jpg")
            _touch(folder, "2026-04-12 09.15.30_4.jpg")
            plan = canonical_renumber.plan_renames_for_folder(folder)
            self.assertEqual(len(plan), 1)
            self.assertEqual(os.path.basename(plan[0].old_path),
                             "2026-04-12 09.15.30_4.jpg")
            self.assertEqual(os.path.basename(plan[0].new_path),
                             "2026-04-12 09.15.30_3.jpg")

    def test_non_canonical_files_left_alone(self):
        """A file that doesn't match CANONICAL_FILENAME_PARTS_RE is skipped."""
        with tempfile.TemporaryDirectory() as folder:
            _touch(folder, "2026-04-12 09.15.30_1.jpg")
            _touch(folder, "random.txt")
            _touch(folder, "IMG_4521.jpg")
            plan = canonical_renumber.plan_renames_for_folder(folder)
            self.assertEqual(plan, [])

    def test_different_timestamps_are_independent_buckets(self):
        """A _1.jpg at one timestamp doesn't compete with _1.jpg at another."""
        with tempfile.TemporaryDirectory() as folder:
            _touch(folder, "2026-04-12 09.15.30_1.jpg")
            _touch(folder, "2026-04-12 09.15.31_1.jpg")
            plan = canonical_renumber.plan_renames_for_folder(folder)
            self.assertEqual(plan, [])


class TestApplyRenamePlan(unittest.TestCase):
    def test_two_phase_rename_handles_cycle(self):
        """If applying the plan naively would clobber a file (A's new name = B's
        old name), the two-phase staging avoids it."""
        with tempfile.TemporaryDirectory() as folder:
            _touch(folder, "2026-04-12 09.15.30_1.jpeg")  # -> _1.jpg
            _touch(folder, "2026-04-12 09.15.30_1.jpg")   # -> _2.jpg
            _touch(folder, "2026-04-12 09.15.30_2.jpg")   # -> _3.jpg (cascades)
            plan = canonical_renumber.plan_renames_for_folder(folder)
            applied = canonical_renumber.apply_rename_plan(plan)
            self.assertEqual(applied, 3)
            self.assertEqual(_names(folder), {
                "2026-04-12 09.15.30_1.jpg",
                "2026-04-12 09.15.30_2.jpg",
                "2026-04-12 09.15.30_3.jpg",
            })

    def test_no_leftover_temp_files(self):
        with tempfile.TemporaryDirectory() as folder:
            _touch(folder, "2026-04-12 09.15.30_1.JPG")
            plan = canonical_renumber.plan_renames_for_folder(folder)
            canonical_renumber.apply_rename_plan(plan)
            leftovers = [n for n in _names(folder) if "__renaming__" in n]
            self.assertEqual(leftovers, [])

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            _touch(folder, "2026-04-12 09.15.30_1.jpeg")
            _touch(folder, "2026-04-12 09.15.30_1.jpg")
            first_plan = canonical_renumber.plan_renames_for_folder(folder)
            canonical_renumber.apply_rename_plan(first_plan)
            second_plan = canonical_renumber.plan_renames_for_folder(folder)
            self.assertEqual(second_plan, [])


class TestPlanRenamesRecursive(unittest.TestCase):
    def test_recursive_walk(self):
        with tempfile.TemporaryDirectory() as root:
            year_2011 = os.path.join(root, "2011")
            year_2026 = os.path.join(root, "2026")
            os.makedirs(year_2011)
            os.makedirs(year_2026)
            _touch(year_2011, "2011-01-01 13.00.00_1.jpeg")
            _touch(year_2026, "2026-04-12 09.15.30_1.JPG")
            plans = canonical_renumber.plan_renames_recursive(root)
            self.assertEqual(set(plans.keys()), {year_2011, year_2026})

    def test_recursive_skips_empty_folders(self):
        with tempfile.TemporaryDirectory() as root:
            empty = os.path.join(root, "empty")
            os.makedirs(empty)
            year_2026 = os.path.join(root, "2026")
            os.makedirs(year_2026)
            _touch(year_2026, "2026-04-12 09.15.30_1.jpeg")
            plans = canonical_renumber.plan_renames_recursive(root)
            self.assertEqual(set(plans.keys()), {year_2026})


if __name__ == "__main__":
    unittest.main()
