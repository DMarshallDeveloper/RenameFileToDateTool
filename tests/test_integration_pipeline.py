"""Property-based integration tests for the dedup pipeline.

Generates random LibrarySpec values via hypothesis, materializes them as
real JPEG files in tmpdirs, then runs the full multi-script workflow:

    combine_libraries -> normalize_canonical_names -> find_duplicate_photos
    (scan -> mark -> simulate "user keeps all" -> finalize)

Then checks invariants:

  1. No data loss: every unique pixel-content hash from the inputs survives
     somewhere in the output (since manual review is skipped, ALL unique
     contents must survive, not just a superset).
  2. All canonical: every final filename matches CANONICAL_FILENAME_RE.
  3. Year-folder placement: each file's folder matches its filename year
     (with the BUNDLED_EARLY_FOLDER exception for years 2000-2010).
  4. Idempotent: a second pipeline run produces the same final layout.

Bounded sizes (1-3 sources, 1-6 files per source) keep each example to a
few seconds. ``max_examples=10`` per test caps total runtime around 1 min.

These tests cover the same surface area as the hand-run synth verification
from session notes section 18 — but with random inputs instead of one
chosen scenario, so they keep catching new failure shapes as the pipeline
evolves.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass

from hypothesis import HealthCheck, given, settings, strategies as st
from PIL import Image

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

import combine_libraries  # noqa: E402
import find_duplicate_photos  # noqa: E402
import normalize_canonical_names  # noqa: E402
from photo_lib.config import (  # noqa: E402
    BUNDLED_EARLY_FOLDER,
    BUNDLED_EARLY_YEAR_RANGE,
)
from photo_lib.filename_pattern import CANONICAL_FILENAME_RE  # noqa: E402

BUNDLED_EARLY_YEARS = set(range(*BUNDLED_EARLY_YEAR_RANGE))


@dataclass(frozen=True)
class FileSpec:
    """Declarative description of one file in a synth source library.

    Files sharing the same ``content_id`` are byte-identical duplicates by
    construction — the dedup workflow should group them at tier 1. Different
    ``content_id`` values produce visually distinct images with different
    pHashes, so they should NOT group.
    """
    year: int        # 2000..2026
    month: int       # 1..12
    day: int         # 1..28 (avoid month-end edge cases)
    hour: int        # 0..23
    minute: int      # 0..59
    second: int      # 0..59
    content_id: int  # files with same content_id are byte-identical
    extension: str   # "jpg" or "JPG" (case is randomised)


@dataclass(frozen=True)
class SourceSpec:
    name: str
    files: tuple[FileSpec, ...]


@dataclass(frozen=True)
class LibrarySpec:
    sources: tuple[SourceSpec, ...]


def _structured_image_bytes(content_id: int) -> bytes:
    """Deterministic JPEG bytes for a given content_id.

    Different content_ids produce visually distinct images (different
    pHashes). Same content_id always produces identical bytes, so the
    dedup workflow groups them as tier 1.
    """
    image = Image.new("RGB", (96, 96))
    pixels = image.load()
    seed = max(1, (content_id * 17 + 3) % 256)
    for x in range(96):
        for y in range(96):
            pixels[x, y] = (
                (x * seed) % 256,
                (y * (seed + 11)) % 256,
                ((x + y) * seed) % 256,
            )
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def _year_folder(year: int) -> str:
    if year in BUNDLED_EARLY_YEARS:
        return BUNDLED_EARLY_FOLDER
    return str(year)


def materialize(spec: LibrarySpec, root: str) -> None:
    """Write the spec to disk under ``root`` as source subfolders.

    Filenames collide within a single source's year folder if their
    (year, month, day, hour, minute, second, extension) tuple matches —
    we bump the canonical ``_N`` suffix until we find a free slot. That
    mirrors what the takeout ingest does at the source.
    """
    for source in spec.sources:
        source_root = os.path.join(root, source.name)
        for file_spec in source.files:
            folder = os.path.join(source_root, _year_folder(file_spec.year))
            os.makedirs(folder, exist_ok=True)
            base = (
                f"{file_spec.year:04d}-{file_spec.month:02d}-{file_spec.day:02d} "
                f"{file_spec.hour:02d}.{file_spec.minute:02d}.{file_spec.second:02d}"
            )
            idx = 1
            while True:
                candidate = f"{base}_{idx}.{file_spec.extension}"
                candidate_path = os.path.join(folder, candidate)
                if not os.path.exists(candidate_path):
                    break
                idx += 1
            with open(candidate_path, "wb") as fh:
                fh.write(_structured_image_bytes(file_spec.content_id))


def _pixel_sha256(path: str) -> str | None:
    """SHA-256 of decoded RGB pixel bytes — same digest for bit-perfect
    duplicates regardless of file metadata, but differs across visually
    different images."""
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            digest = hashlib.sha256()
            digest.update(f"{rgb.size}".encode("ascii"))
            digest.update(rgb.tobytes())
            return digest.hexdigest()
    except Exception:
        return None


def collect_pixel_hashes(root: str) -> set[str]:
    """Return the set of pixel-SHA256 values for every JPEG under ``root``.

    This is the data-loss signal: the dedup pipeline may collapse
    byte-identical duplicates into a single survivor, but every UNIQUE
    pixel hash in the input must survive somewhere in the output.
    """
    hashes: set[str] = set()
    for current_dir, _, filenames in os.walk(root):
        for name in filenames:
            if name.lower().endswith((".jpg", ".jpeg")):
                digest = _pixel_sha256(os.path.join(current_dir, name))
                if digest is not None:
                    hashes.add(digest)
    return hashes


def collect_canonical_files(root: str) -> list[str]:
    """All files under ``root`` that look like media (exclude .db, .html)."""
    paths: list[str] = []
    for current_dir, _, filenames in os.walk(root):
        for name in filenames:
            if name.endswith((".db", ".html")):
                continue
            paths.append(os.path.join(current_dir, name))
    return sorted(paths)


def run_pipeline(spec: LibrarySpec, work_root: str) -> str:
    """Materialize the spec into ``work_root/sources/`` and run the full
    dedup pipeline into ``work_root/combined/``. Returns the dest path.
    """
    sources_root = os.path.join(work_root, "sources")
    os.makedirs(sources_root, exist_ok=True)
    materialize(spec, sources_root)

    source_paths = [
        os.path.join(sources_root, source.name) for source in spec.sources
    ]
    dest = os.path.join(work_root, "combined")

    combine_libraries.combine(source_paths, dest, dry_run=False)
    normalize_canonical_names.normalize_tree(dest, dry_run=False)
    find_duplicate_photos.scan(dest)
    find_duplicate_photos.mark(dest, dry_run=False, phash_threshold=0)
    # Manual review is skipped (= "user keeps all"). Finalize sends each
    # marked file back to canonical form — same-base survivors strip their
    # suffix in place, cross-date survivors return to their origin year.
    find_duplicate_photos.finalize(dest, dry_run=False)
    return dest


# --- Hypothesis strategies ---------------------------------------------

# Pool size of distinct contents: smaller pool => more duplicate groups
# generated, which exercises the cross-source dedup paths more.
_CONTENT_POOL = 4


_filespec = st.builds(
    FileSpec,
    year=st.integers(min_value=2000, max_value=2026),
    month=st.integers(min_value=1, max_value=12),
    day=st.integers(min_value=1, max_value=28),
    hour=st.integers(min_value=0, max_value=23),
    minute=st.integers(min_value=0, max_value=59),
    second=st.integers(min_value=0, max_value=59),
    content_id=st.integers(min_value=0, max_value=_CONTENT_POOL - 1),
    extension=st.sampled_from(["jpg", "JPG"]),
)

_sourcespec = st.builds(
    SourceSpec,
    name=st.sampled_from(["master", "takeout", "usb", "extra"]),
    files=st.lists(_filespec, min_size=1, max_size=6).map(tuple),
)


def _libraries_with_unique_source_names():
    """LibrarySpec where every source has a distinct name — required by
    materialize() to keep each source's tree at a separate path."""
    return st.lists(_sourcespec, min_size=1, max_size=3).map(tuple).filter(
        lambda srcs: len({s.name for s in srcs}) == len(srcs)
    ).map(lambda srcs: LibrarySpec(sources=srcs))


_PIPELINE_SETTINGS = settings(
    # Each case materializes 1-18 JPEGs and runs the full pipeline; ~200ms
    # per case at this size. 25 examples per test (= 100 across all 4)
    # gives meaningful coverage in ~25-30s of added suite time.
    max_examples=25,
    deadline=None,  # File I/O + PIL hashing dominates; default deadline trips.
    suppress_health_check=[
        HealthCheck.data_too_large,
        HealthCheck.too_slow,
        HealthCheck.function_scoped_fixture,
    ],
)


class TestPipelineInvariants(unittest.TestCase):
    """Each test re-runs the full dedup workflow on a fresh random
    LibrarySpec and asserts one invariant."""

    def setUp(self):
        # Suppress per-step logging from the scripts; otherwise each test
        # case dumps several screens of "X files would be renamed..." noise.
        logging.getLogger("photo_lib").setLevel(logging.WARNING)
        self.tmp_roots: list[str] = []

    def tearDown(self):
        for root in self.tmp_roots:
            shutil.rmtree(root, ignore_errors=True)

    def _fresh_tmpdir(self) -> str:
        root = tempfile.mkdtemp(prefix="test_pipeline_")
        self.tmp_roots.append(root)
        return root

    @_PIPELINE_SETTINGS
    @given(spec=_libraries_with_unique_source_names())
    def test_no_unique_content_lost_when_user_keeps_all(self, spec: LibrarySpec):
        # When the manual-review step is skipped (no _b/_c deletions), every
        # unique pixel content from the sources must survive somewhere in
        # the final tree.
        work_root = self._fresh_tmpdir()
        sources_root = os.path.join(work_root, "sources")
        os.makedirs(sources_root, exist_ok=True)
        materialize(spec, sources_root)
        source_hashes = collect_pixel_hashes(sources_root)

        # Reuse run_pipeline's tail — but we've already materialized, so
        # call the post-materialize steps inline.
        source_paths = [
            os.path.join(sources_root, source.name) for source in spec.sources
        ]
        dest = os.path.join(work_root, "combined")
        combine_libraries.combine(source_paths, dest, dry_run=False)
        normalize_canonical_names.normalize_tree(dest, dry_run=False)
        find_duplicate_photos.scan(dest)
        find_duplicate_photos.mark(dest, dry_run=False, phash_threshold=0)
        find_duplicate_photos.finalize(dest, dry_run=False)

        dest_hashes = collect_pixel_hashes(dest)
        self.assertEqual(
            source_hashes, dest_hashes,
            f"unique-content set differs: missing from dest: "
            f"{source_hashes - dest_hashes}; unexpected in dest: "
            f"{dest_hashes - source_hashes}",
        )

    @_PIPELINE_SETTINGS
    @given(spec=_libraries_with_unique_source_names())
    def test_all_filenames_canonical_after_finalize(self, spec: LibrarySpec):
        # Every media file in the final tree must match the strict
        # canonical pattern. No marked filenames (_a/_b/_c) should remain
        # and no __from_ markers either.
        work_root = self._fresh_tmpdir()
        run_pipeline(spec, work_root)
        dest = os.path.join(work_root, "combined")

        non_canonical: list[str] = []
        for path in collect_canonical_files(dest):
            name = os.path.basename(path)
            if not CANONICAL_FILENAME_RE.match(name):
                non_canonical.append(os.path.relpath(path, dest))
        self.assertEqual(non_canonical, [],
                         f"non-canonical filenames remain: {non_canonical}")

    @_PIPELINE_SETTINGS
    @given(spec=_libraries_with_unique_source_names())
    def test_every_file_lives_in_its_correct_year_folder(self, spec: LibrarySpec):
        # The parent folder of each file must match the year encoded in
        # its filename (with the BUNDLED_EARLY exception for 2000-2010).
        work_root = self._fresh_tmpdir()
        run_pipeline(spec, work_root)
        dest = os.path.join(work_root, "combined")

        misplaced: list[str] = []
        for path in collect_canonical_files(dest):
            name = os.path.basename(path)
            if not CANONICAL_FILENAME_RE.match(name):
                continue  # invariant 2 owns this case
            year = int(name[:4])
            expected_folder = _year_folder(year)
            actual_folder = os.path.basename(os.path.dirname(path))
            if expected_folder != actual_folder:
                misplaced.append(
                    f"{os.path.relpath(path, dest)} (expected {expected_folder}/)"
                )
        self.assertEqual(misplaced, [],
                         f"files in wrong year folder: {misplaced}")

    @_PIPELINE_SETTINGS
    @given(spec=_libraries_with_unique_source_names())
    def test_re_running_pipeline_lands_in_same_layout(self, spec: LibrarySpec):
        # Running the dedup workflow a second time (combine has already
        # happened, normalize/scan/mark/finalize re-applied) must end up
        # in the same final file layout. mark + finalize collapse the
        # cross-date dupes and split them back home; the next iteration
        # does the same and lands at the same place.
        work_root = self._fresh_tmpdir()
        run_pipeline(spec, work_root)
        dest = os.path.join(work_root, "combined")
        first_layout = {
            os.path.relpath(p, dest)
            for p in collect_canonical_files(dest)
        }

        # Second pass: rescan (so the cache knows about the post-finalize
        # paths), mark, finalize again.
        find_duplicate_photos.scan(dest)
        find_duplicate_photos.mark(dest, dry_run=False, phash_threshold=0)
        find_duplicate_photos.finalize(dest, dry_run=False)

        second_layout = {
            os.path.relpath(p, dest)
            for p in collect_canonical_files(dest)
        }
        self.assertEqual(first_layout, second_layout,
                         "second pipeline pass changed the layout")


if __name__ == "__main__":
    unittest.main()
