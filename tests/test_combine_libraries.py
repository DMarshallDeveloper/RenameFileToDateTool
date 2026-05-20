"""Tests for combine_libraries.py: multi-source copy with collision-driven _N bump."""

import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

import combine_libraries  # noqa: E402


def _touch(folder: str, name: str, content: bytes | None = None) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "wb") as fh:
        fh.write(content if content is not None else name.encode())
    return path


def _names_recursive(root: str) -> set[str]:
    out: set[str] = set()
    for current_dir, _subdirs, filenames in os.walk(root):
        for name in filenames:
            out.add(os.path.relpath(os.path.join(current_dir, name), root))
    return out


class TestCombine(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='test_combine_')
        self.source_a = os.path.join(self.workdir, 'A')
        self.source_b = os.path.join(self.workdir, 'B')
        self.dest = os.path.join(self.workdir, 'dest')
        self.addCleanup(lambda: shutil.rmtree(self.workdir, ignore_errors=True))

    def test_no_collision_copies_through(self):
        _touch(os.path.join(self.source_a, '2014'), '2014-01-01 13.00.00_1.jpg', b"A1")
        _touch(os.path.join(self.source_b, '2014'), '2014-01-02 13.00.00_1.jpg', b"B1")
        combine_libraries.combine([self.source_a, self.source_b], self.dest)
        self.assertEqual(
            _names_recursive(self.dest),
            {
                os.path.join('2014', '2014-01-01 13.00.00_1.jpg'),
                os.path.join('2014', '2014-01-02 13.00.00_1.jpg'),
            },
        )

    def test_collision_bumps_canonical_n(self):
        _touch(os.path.join(self.source_a, '2014'), '2014-01-01 13.00.00_1.jpg', b"A1")
        _touch(os.path.join(self.source_b, '2014'), '2014-01-01 13.00.00_1.jpg', b"B1")
        combine_libraries.combine([self.source_a, self.source_b], self.dest)
        names = _names_recursive(self.dest)
        self.assertEqual(
            names,
            {
                os.path.join('2014', '2014-01-01 13.00.00_1.jpg'),
                os.path.join('2014', '2014-01-01 13.00.00_2.jpg'),
            },
        )

    def test_collision_finds_next_free_slot(self):
        _touch(os.path.join(self.source_a, '2014'), '2014-01-01 13.00.00_1.jpg', b"A1")
        _touch(os.path.join(self.source_a, '2014'), '2014-01-01 13.00.00_2.jpg', b"A2")
        _touch(os.path.join(self.source_b, '2014'), '2014-01-01 13.00.00_1.jpg', b"B1")
        combine_libraries.combine([self.source_a, self.source_b], self.dest)
        # Source B's _1 must land at _3 because _1 and _2 are already taken by A.
        names = _names_recursive(self.dest)
        self.assertEqual(
            names,
            {
                os.path.join('2014', '2014-01-01 13.00.00_1.jpg'),
                os.path.join('2014', '2014-01-01 13.00.00_2.jpg'),
                os.path.join('2014', '2014-01-01 13.00.00_3.jpg'),
            },
        )

    def test_non_canonical_collision_falls_back_to_dup_suffix(self):
        _touch(os.path.join(self.source_a, '2014'), 'IMG_0001.jpg', b"A1")
        _touch(os.path.join(self.source_b, '2014'), 'IMG_0001.jpg', b"B1")
        combine_libraries.combine([self.source_a, self.source_b], self.dest)
        names = _names_recursive(self.dest)
        self.assertEqual(
            names,
            {
                os.path.join('2014', 'IMG_0001.jpg'),
                os.path.join('2014', 'IMG_0001_dup1.jpg'),
            },
        )

    def test_dry_run_does_not_touch_disk(self):
        _touch(os.path.join(self.source_a, '2014'), '2014-01-01 13.00.00_1.jpg')
        combine_libraries.combine([self.source_a], self.dest, dry_run=True)
        self.assertFalse(os.path.exists(self.dest))

    def test_subfolder_structure_preserved(self):
        _touch(os.path.join(self.source_a, '2014'), 'foo.jpg')
        _touch(os.path.join(self.source_a, '2026'), 'bar.jpg')
        combine_libraries.combine([self.source_a], self.dest)
        self.assertEqual(
            _names_recursive(self.dest),
            {os.path.join('2014', 'foo.jpg'), os.path.join('2026', 'bar.jpg')},
        )


class TestContentPreservation(unittest.TestCase):
    """The data-safety invariant: whatever's at the destination matches the
    bytes of whatever source was assigned that slot. No silent overwrites, no
    cross-wired contents."""

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='test_combine_content_')
        self.source_a = os.path.join(self.workdir, 'A')
        self.source_b = os.path.join(self.workdir, 'B')
        self.dest = os.path.join(self.workdir, 'dest')
        self.addCleanup(lambda: shutil.rmtree(self.workdir, ignore_errors=True))

    def _read(self, *parts: str) -> bytes:
        with open(os.path.join(self.dest, *parts), "rb") as fh:
            return fh.read()

    def test_first_source_wins_original_slot(self):
        """When A and B both have ``_1.jpg``, A keeps ``_1.jpg`` with A's bytes
        and B's bytes land at ``_2.jpg``. No cross-contamination."""
        _touch(os.path.join(self.source_a, '2014'), '2014-01-01 13.00.00_1.jpg', b"FROM_A")
        _touch(os.path.join(self.source_b, '2014'), '2014-01-01 13.00.00_1.jpg', b"FROM_B")
        combine_libraries.combine([self.source_a, self.source_b], self.dest)
        self.assertEqual(self._read('2014', '2014-01-01 13.00.00_1.jpg'), b"FROM_A")
        self.assertEqual(self._read('2014', '2014-01-01 13.00.00_2.jpg'), b"FROM_B")

    def test_three_sources_each_bytes_preserved(self):
        """Three-source combine — all three byte-distinct copies make it
        through with the right content at the right slot."""
        _touch(os.path.join(self.source_a, '2014'), '2014-01-01 13.00.00_1.jpg', b"AA")
        _touch(os.path.join(self.source_b, '2014'), '2014-01-01 13.00.00_1.jpg', b"BB")
        source_c = os.path.join(self.workdir, 'C')
        _touch(os.path.join(source_c, '2014'), '2014-01-01 13.00.00_1.jpg', b"CC")
        combine_libraries.combine(
            [self.source_a, self.source_b, source_c], self.dest,
        )
        self.assertEqual(self._read('2014', '2014-01-01 13.00.00_1.jpg'), b"AA")
        self.assertEqual(self._read('2014', '2014-01-01 13.00.00_2.jpg'), b"BB")
        self.assertEqual(self._read('2014', '2014-01-01 13.00.00_3.jpg'), b"CC")

    def test_cascading_collisions_preserve_content(self):
        """A has _1..3, B has _1..3 — B's files cascade onto _4..6 with their
        original bytes intact."""
        for index in (1, 2, 3):
            _touch(os.path.join(self.source_a, '2014'),
                   f'2014-01-01 13.00.00_{index}.jpg', f"A{index}".encode())
            _touch(os.path.join(self.source_b, '2014'),
                   f'2014-01-01 13.00.00_{index}.jpg', f"B{index}".encode())
        combine_libraries.combine([self.source_a, self.source_b], self.dest)
        # A keeps _1..3, B lands at _4..6 (preserving its original ordering).
        self.assertEqual(self._read('2014', '2014-01-01 13.00.00_1.jpg'), b"A1")
        self.assertEqual(self._read('2014', '2014-01-01 13.00.00_2.jpg'), b"A2")
        self.assertEqual(self._read('2014', '2014-01-01 13.00.00_3.jpg'), b"A3")
        self.assertEqual(self._read('2014', '2014-01-01 13.00.00_4.jpg'), b"B1")
        self.assertEqual(self._read('2014', '2014-01-01 13.00.00_5.jpg'), b"B2")
        self.assertEqual(self._read('2014', '2014-01-01 13.00.00_6.jpg'), b"B3")

    def test_preexisting_dest_content_respected(self):
        """If the dest already contains _1, the source's _1 lands at _2."""
        _touch(os.path.join(self.dest, '2014'),
               '2014-01-01 13.00.00_1.jpg', b"ALREADY_HERE")
        _touch(os.path.join(self.source_a, '2014'),
               '2014-01-01 13.00.00_1.jpg', b"FROM_A")
        combine_libraries.combine([self.source_a], self.dest)
        self.assertEqual(self._read('2014', '2014-01-01 13.00.00_1.jpg'),
                         b"ALREADY_HERE")
        self.assertEqual(self._read('2014', '2014-01-01 13.00.00_2.jpg'),
                         b"FROM_A")

    def test_cross_extension_no_collision(self):
        """Same timestamp, different extension — both keep their _1 slot."""
        _touch(os.path.join(self.source_a, '2014'),
               '2014-01-01 13.00.00_1.jpg', b"IMG_BYTES")
        _touch(os.path.join(self.source_a, '2014'),
               '2014-01-01 13.00.00_1.mp4', b"VID_BYTES")
        combine_libraries.combine([self.source_a], self.dest)
        self.assertEqual(self._read('2014', '2014-01-01 13.00.00_1.jpg'), b"IMG_BYTES")
        self.assertEqual(self._read('2014', '2014-01-01 13.00.00_1.mp4'), b"VID_BYTES")


class TestCaseInsensitiveCollisions(unittest.TestCase):
    """Regression: a naive case-sensitive plan with `_1.JPG` + `_1.jpg` would
    plan both as separate dest paths; shutil.copy2 then silently overwrites
    one with the other on Windows NTFS / macOS HFS+. The combine MUST detect
    case-different-but-filesystem-equivalent names as collisions.

    This bug ate 286 master files in the 2014 pilot run before being caught.
    """

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='test_combine_case_')
        self.source_a = os.path.join(self.workdir, 'A')
        self.source_b = os.path.join(self.workdir, 'B')
        self.dest = os.path.join(self.workdir, 'dest')
        self.addCleanup(lambda: shutil.rmtree(self.workdir, ignore_errors=True))

    def test_uppercase_jpg_vs_lowercase_jpg_collide(self):
        # Master-style (uppercase) and PhotosCopy-style (lowercase) at the
        # same base — must be detected as a collision so the second source's
        # file lands at _2 instead of overwriting the first.
        _touch(os.path.join(self.source_a, '2014'),
               '2014-01-01 13.00.00_1.JPG', b"MASTER_UPPER")
        _touch(os.path.join(self.source_b, '2014'),
               '2014-01-01 13.00.00_1.jpg', b"COPY_LOWER")
        plan = combine_libraries.plan_combine(
            [self.source_a, self.source_b], self.dest,
        )
        dest_basenames = [os.path.basename(dst) for _, dst in plan]
        # The plan must allocate two distinct case-folded names. Source A
        # keeps its uppercase _1.JPG; source B bumps to _2.jpg.
        self.assertEqual(len(plan), 2)
        self.assertEqual(dest_basenames[0], '2014-01-01 13.00.00_1.JPG')
        self.assertEqual(dest_basenames[1], '2014-01-01 13.00.00_2.jpg')

    def test_uppercase_jpg_vs_lowercase_jpg_files_both_preserved(self):
        # End-to-end on a real case-insensitive filesystem: after apply,
        # both files exist in the destination with distinct names.
        _touch(os.path.join(self.source_a, '2014'),
               '2014-01-01 13.00.00_1.JPG', b"MASTER_UPPER")
        _touch(os.path.join(self.source_b, '2014'),
               '2014-01-01 13.00.00_1.jpg', b"COPY_LOWER")
        combine_libraries.combine([self.source_a, self.source_b], self.dest)
        # Case-insensitively, look up both files.
        names_lower = {n.lower() for n in os.listdir(os.path.join(self.dest, '2014'))}
        self.assertIn('2014-01-01 13.00.00_1.jpg', names_lower)
        self.assertIn('2014-01-01 13.00.00_2.jpg', names_lower)
        # Read the bytes through case-insensitive lookup
        dest_files = [
            os.path.join(self.dest, '2014', n)
            for n in os.listdir(os.path.join(self.dest, '2014'))
        ]
        contents = set()
        for path in dest_files:
            with open(path, 'rb') as fh:
                contents.add(fh.read())
        self.assertEqual(contents, {b"MASTER_UPPER", b"COPY_LOWER"})

    def test_pre_existing_uppercase_in_dest_blocks_lowercase_source(self):
        # Dest already contains an uppercase JPG; a lowercase source must
        # detect the case-insensitive collision and bump.
        _touch(os.path.join(self.dest, '2014'),
               '2014-01-01 13.00.00_1.JPG', b"PRE_EXISTING")
        _touch(os.path.join(self.source_a, '2014'),
               '2014-01-01 13.00.00_1.jpg', b"FROM_A")
        combine_libraries.combine([self.source_a], self.dest)
        names_lower = {n.lower() for n in os.listdir(os.path.join(self.dest, '2014'))}
        self.assertIn('2014-01-01 13.00.00_1.jpg', names_lower)
        self.assertIn('2014-01-01 13.00.00_2.jpg', names_lower)

    def test_uppercase_extension_variations_all_collide(self):
        # .JPG, .Jpg, .jPg, .jpg — all the same on a case-insensitive FS.
        _touch(os.path.join(self.source_a, '2014'),
               '2014-01-01 13.00.00_1.JPG', b"a")
        _touch(os.path.join(self.source_b, '2014'),
               '2014-01-01 13.00.00_1.Jpg', b"b")
        source_c = os.path.join(self.workdir, 'C')
        _touch(os.path.join(source_c, '2014'),
               '2014-01-01 13.00.00_1.jpg', b"c")
        plan = combine_libraries.plan_combine(
            [self.source_a, self.source_b, source_c], self.dest,
        )
        # All three must be distinct dest paths case-insensitively.
        dest_names_lower = [os.path.basename(dst).lower() for _, dst in plan]
        self.assertEqual(len(dest_names_lower), 3)
        self.assertEqual(len(set(dest_names_lower)), 3)


class TestMtimePreservation(unittest.TestCase):
    """shutil.copy2 must preserve mtime — the dedup cache uses (path, size,
    mtime) as its invalidation key, so a combine that bumped every file's
    mtime to 'now' would defeat caching."""

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='test_combine_mtime_')
        self.addCleanup(lambda: shutil.rmtree(self.workdir, ignore_errors=True))

    def test_mtime_preserved_on_copy(self):
        source = os.path.join(self.workdir, 'A')
        dest = os.path.join(self.workdir, 'dest')
        src_path = _touch(os.path.join(source, '2014'),
                          '2014-01-01 13.00.00_1.jpg', b"hello")
        # Force a known mtime so we can compare exactly.
        original_mtime = 1_500_000_000.5
        os.utime(src_path, (original_mtime, original_mtime))

        combine_libraries.combine([source], dest)

        dst_path = os.path.join(dest, '2014', '2014-01-01 13.00.00_1.jpg')
        copied_mtime = os.path.getmtime(dst_path)
        # Most filesystems store sub-second precision; round to 1ms for
        # FAT/exFAT robustness.
        self.assertAlmostEqual(copied_mtime, original_mtime, places=2)


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='test_combine_edge_')
        self.source_a = os.path.join(self.workdir, 'A')
        self.dest = os.path.join(self.workdir, 'dest')
        self.addCleanup(lambda: shutil.rmtree(self.workdir, ignore_errors=True))

    def test_empty_source_is_noop(self):
        os.makedirs(self.source_a)  # source exists but contains no files
        combine_libraries.combine([self.source_a], self.dest)
        self.assertFalse(os.listdir(self.dest) if os.path.exists(self.dest) else False)

    def test_files_at_source_root_land_at_dest_root(self):
        _touch(self.source_a, '2014-01-01 13.00.00_1.jpg', b"loose")
        combine_libraries.combine([self.source_a], self.dest)
        self.assertEqual(
            _names_recursive(self.dest),
            {'2014-01-01 13.00.00_1.jpg'},
        )

    def test_mixed_canonical_and_non_canonical_in_same_folder(self):
        _touch(os.path.join(self.source_a, '2014'), '2014-01-01 13.00.00_1.jpg', b"canonical_a")
        _touch(os.path.join(self.source_a, '2014'), 'IMG_0001.jpg', b"non_canonical_a")
        source_b = os.path.join(self.workdir, 'B')
        _touch(os.path.join(source_b, '2014'), '2014-01-01 13.00.00_1.jpg', b"canonical_b")
        _touch(os.path.join(source_b, '2014'), 'IMG_0001.jpg', b"non_canonical_b")
        combine_libraries.combine([self.source_a, source_b], self.dest)
        self.assertEqual(
            _names_recursive(self.dest),
            {
                os.path.join('2014', '2014-01-01 13.00.00_1.jpg'),
                os.path.join('2014', '2014-01-01 13.00.00_2.jpg'),
                os.path.join('2014', 'IMG_0001.jpg'),
                os.path.join('2014', 'IMG_0001_dup1.jpg'),
            },
        )


if __name__ == '__main__':
    unittest.main()
