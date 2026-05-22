"""find_duplicate_photos.py — pHash-based duplicate detection with manual-review-safe marking.

Three modes (one per run):

  --scan      Walk the library, fingerprint every image, store in a sidecar
              SQLite cache. Idempotent: only re-hashes files whose size or
              mtime changed since the last scan.

  --report    Emit an HTML side-by-side report of every duplicate group found,
              for at-a-glance review in a browser. Read-only.

  --mark      Rename files within each duplicate group to ``<base>_<idx>_<a|b|c>.<ext>``
              so duplicates sit adjacent in name-sort order. The highest-quality
              file in each group gets ``_a``; others ``_b``, ``_c``, ... Read
              the cache; do not re-hash. Requires --scan to have run.

  --finalize  After you've manually deleted the unwanted ``_b`` / ``_c`` files,
              this strips the ``_a`` suffix from any surviving lone file,
              returning it to canonical form.

Conservative by default — only tiers 1-3 are auto-grouped:
  Tier 1: byte-for-byte identical files
  Tier 2: identical decoded pixels (same image, different EXIF)
  Tier 3: perceptually identical (pHash Hamming distance = 0)

Anything fuzzier (pHash distance 1-10) is intentionally not handled — it would
need eye-on-glass review per pair.
"""
from __future__ import annotations

import argparse
import logging
import os

from photo_lib.duplicate_cache import FingerprintCache, default_cache_path
from photo_lib.duplicate_finder import (
    DEFAULT_PHASH_HAMMING_THRESHOLD,
    FileFingerprint,
    apply_simple_rename_plan,
    fingerprint_file,
    group_duplicates,
    plan_finalize,
    plan_mark,
)
from photo_lib.duplicate_report import (
    render_html_report,
    render_singletons_html_report,
    render_stats_html_report,
)
from photo_lib.extensions import is_media, normalize_extension
from photo_lib.logging_setup import configure_logging
from photo_lib.source_manifest import SourceManifest, default_manifest_path
from photo_lib.tk_picker import resolve_directory

logger = logging.getLogger("photo_lib")


def iter_media_paths(root: str):
    # Canonicalise the root before walking so the resulting paths always
    # have OS-default separators. Otherwise a caller passing "F:/..." would
    # produce cache keys that differ from a later "F:\\..." call for the
    # same file (Windows filesystem ignores the difference; SQLite does not).
    root = os.path.normpath(root)
    for current_dir, _subdirs, filenames in os.walk(root):
        for name in filenames:
            extension = normalize_extension(os.path.splitext(name)[1])
            if is_media(extension):
                yield os.path.join(current_dir, name)


def _count_orphan_rows(cache, walked_paths: list[str]) -> int:
    """How many cache rows point at a path that wasn't walked this scan.

    A nonzero orphan count is *usually* benign — rows left behind by an
    earlier mark/finalize rename that no longer matches anything on disk.
    But a *jump* in orphans across one scan run is the smoking gun for a
    path-canonicalization bug (the kind where bash silently eats ``\\P`` and
    the scan re-hashes everything under a different key form): see the
    abspath fix in ``photo_lib.duplicate_cache`` for the full story.
    """
    walked_keys = {os.path.abspath(p) for p in walked_paths}
    return sum(1 for fp in cache.all_fingerprints()
               if os.path.abspath(fp.path) not in walked_keys)


def scan(root: str, cache_path: str | None = None) -> int:
    cache_path = cache_path or default_cache_path(root)
    paths = list(iter_media_paths(root))
    logger.info("Scanning %d media files under %s (cache: %s)",
                len(paths), root, cache_path)
    with FingerprintCache(cache_path) as cache:
        existing_cache_rows = len(cache.all_fingerprints())
        orphans_at_start = _count_orphan_rows(cache, paths)
    logger.info("Cache contains %d existing rows from prior runs "
                "(reused where path + size + mtime still match); "
                "%d of those are orphans (path no longer on disk - "
                "typically left over from mark/finalize renames)",
                existing_cache_rows, orphans_at_start)
    hashed_count = 0
    cache_hits = 0
    with FingerprintCache(cache_path) as cache:
        for path in paths:
            stat_result = os.stat(path)
            cached = cache.lookup(path, stat_result.st_size, stat_result.st_mtime)
            if cached is not None:
                cache_hits += 1
                continue
            fingerprint = fingerprint_file(path)
            if fingerprint is None:
                continue
            cache.store(fingerprint)
            hashed_count += 1
            if hashed_count % 100 == 0:
                # Include cache_hits in every tick so a user watching the
                # log can tell at a glance whether resume is working —
                # otherwise "hashed 200" leaves you wondering if it's 200
                # of 12k or 200 of 32k.
                logger.info("  hashed %d new files (cache hits so far: %d)",
                            hashed_count, cache_hits)
        orphans_at_end = _count_orphan_rows(cache, paths)
    logger.info("Scan done: %d hashed, %d cache hits", hashed_count, cache_hits)
    # Hard guard: a *jump* in orphans means rows were added under a different
    # path-canonical form than the on-disk files use — that's a
    # canonicalization bug, not normal cache decay. Shout loudly so the
    # user sees it instead of a quietly-bloated cache.
    new_orphans = orphans_at_end - orphans_at_start
    if new_orphans > 0:
        logger.warning(
            "  *** WARNING: %d new orphan cache rows created this scan. "
            "Expected 0 — this typically means the cache stored rows under "
            "a different path-canonical form than what os.walk produced. "
            "Inspect cache rows whose path doesn't match a walked file.",
            new_orphans,
        )
    elif orphans_at_end > 0:
        logger.info("  Orphan count unchanged at %d (no new orphans created).",
                    orphans_at_end)
    return hashed_count


def _load_groups(root: str, cache_path: str | None, phash_threshold: int):
    cache_path = cache_path or default_cache_path(root)
    if not os.path.exists(cache_path):
        raise SystemExit(
            f"Cache file not found: {cache_path}. Run with --scan first."
        )
    with FingerprintCache(cache_path) as cache:
        fingerprints = cache.all_fingerprints()
    # Drop entries whose underlying file no longer exists (mark/finalize may
    # have moved files; the cache is keyed by path).
    fingerprints = [fp for fp in fingerprints if os.path.exists(fp.path)]
    return fingerprints, group_duplicates(
        fingerprints, phash_hamming_threshold=phash_threshold,
    )


def _load_source_labels(root: str) -> dict[str, str]:
    """Return ``{normalized_path -> source_label}`` from the combine manifest,
    or ``{}`` if no manifest exists (library wasn't built via
    ``combine_libraries``).
    """
    manifest_path = default_manifest_path(root)
    if not os.path.exists(manifest_path):
        return {}
    with SourceManifest(manifest_path) as manifest:
        return manifest.all_entries()


def report(root: str, output_path: str, cache_path: str | None = None,
           phash_threshold: int = DEFAULT_PHASH_HAMMING_THRESHOLD) -> int:
    fingerprints, groups = _load_groups(root, cache_path, phash_threshold)
    source_labels = _load_source_labels(root)
    html_text = render_html_report(groups, root, source_labels=source_labels)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(html_text)
    logger.info("Wrote %d-group duplicate report to %s (phash_threshold=%d)",
                len(groups), output_path, phash_threshold)

    grouped_paths = {fp.path for group in groups for fp in group.fingerprints}
    singletons = [fp for fp in fingerprints if fp.path not in grouped_paths]
    report_dir = os.path.dirname(output_path) or "."
    singletons_path = os.path.join(report_dir, "singletons_report.html")
    singletons_html = render_singletons_html_report(
        singletons, root, source_labels=source_labels,
    )
    with open(singletons_path, "w", encoding="utf-8") as fh:
        fh.write(singletons_html)
    logger.info("Wrote %d-singleton report to %s",
                len(singletons), singletons_path)

    stats_path = os.path.join(report_dir, "stats_report.html")
    stats_html = render_stats_html_report(
        fingerprints, groups, root, source_labels=source_labels,
    )
    with open(stats_path, "w", encoding="utf-8") as fh:
        fh.write(stats_html)
    logger.info("Wrote stats report (%d files) to %s",
                len(fingerprints), stats_path)

    return len(groups)


def mark(root: str, dry_run: bool, cache_path: str | None = None,
         phash_threshold: int = DEFAULT_PHASH_HAMMING_THRESHOLD) -> int:
    _fingerprints, groups = _load_groups(root, cache_path, phash_threshold)
    source_labels = _load_source_labels(root)
    plan = plan_mark(groups, source_label_lookup=source_labels)
    prefix = "[DRY-RUN] " if dry_run else ""
    cross_folder = sum(
        1 for old, new, _ in plan
        if os.path.dirname(old) != os.path.dirname(new)
    )
    logger.info("%s%d files would be renamed across %d groups (%d cross-folder)",
                prefix, len(plan), len(groups), cross_folder)
    for old_path, new_path, tier in plan[:25]:
        # Cross-folder rename = a cross-date loser moving into the winner's
        # folder; show the relpath so the destination folder is visible.
        if os.path.dirname(old_path) != os.path.dirname(new_path):
            old_repr = os.path.relpath(old_path, root)
            new_repr = os.path.relpath(new_path, root)
        else:
            old_repr = os.path.basename(old_path)
            new_repr = os.path.basename(new_path)
        logger.info("%s  T%d  %s  ->  %s",
                    prefix, tier, old_repr, new_repr)
    if len(plan) > 25:
        logger.info("%s  ... and %d more", prefix, len(plan) - 25)
    if not dry_run:
        applied = apply_simple_rename_plan([(o, n) for o, n, _ in plan])
        logger.info("Applied %d renames", applied)
        # Update cache: rename the path key so a subsequent ``report`` can
        # read the same hash data under the new filename without a re-scan.
        cache_path = cache_path or default_cache_path(root)
        with FingerprintCache(cache_path) as cache:
            for old_path, new_path, _ in plan:
                cache.rename(old_path, new_path)
        # Keep the source manifest in sync too, so subsequent report/finalize
        # passes can still resolve labels for the renamed files. rename_many
        # is the staged variant — handles plans where one entry's new path
        # collides with another entry's old path.
        manifest_path = default_manifest_path(root)
        if os.path.exists(manifest_path):
            with SourceManifest(manifest_path) as manifest:
                manifest.rename_many([(o, n) for o, n, _ in plan])
    return len(plan)


def finalize(root: str, dry_run: bool, cache_path: str | None = None) -> int:
    plan = plan_finalize(root)
    prefix = "[DRY-RUN] " if dry_run else ""
    cross_folder = sum(
        1 for old, new in plan
        if os.path.dirname(old) != os.path.dirname(new)
    )
    logger.info("%s%d marked files would be returned to canonical form "
                "(%d cross-folder)",
                prefix, len(plan), cross_folder)
    for old_path, new_path in plan[:25]:
        # If the file is moving to another folder (a cross-date loser going
        # home), the destination folder is significant — show its relpath
        # rather than just the basename.
        if os.path.dirname(old_path) != os.path.dirname(new_path):
            dest_repr = os.path.relpath(new_path, root)
        else:
            dest_repr = os.path.basename(new_path)
        logger.info("%s  %s  ->  %s",
                    prefix,
                    os.path.basename(old_path), dest_repr)
    if len(plan) > 25:
        logger.info("%s  ... and %d more", prefix, len(plan) - 25)
    if not dry_run:
        applied = apply_simple_rename_plan(plan)
        logger.info("Applied %d renames", applied)
        cache_path = cache_path or default_cache_path(root)
        if os.path.exists(cache_path):
            with FingerprintCache(cache_path) as cache:
                for old_path, new_path in plan:
                    cache.rename(old_path, new_path)
        manifest_path = default_manifest_path(root)
        if os.path.exists(manifest_path):
            with SourceManifest(manifest_path) as manifest:
                manifest.rename_many(plan)
    return len(plan)


def main(args: argparse.Namespace) -> None:
    target_directory = resolve_directory(args.path, "Select Photos Directory")
    if not target_directory:
        logger.info("No directory selected. Exiting.")
        return
    if args.command == "scan":
        scan(target_directory, args.cache)
    elif args.command == "report":
        output_path = args.output or os.path.join(target_directory, "duplicate_report.html")
        report(target_directory, output_path, args.cache, args.phash_threshold)
    elif args.command == "mark":
        mark(target_directory, args.dry_run, args.cache, args.phash_threshold)
    elif args.command == "finalize":
        finalize(target_directory, args.dry_run, args.cache)
    else:
        raise SystemExit(f"Unknown command: {args.command!r}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Find and mark duplicate photos.")
    parser.add_argument("command", choices=["scan", "report", "mark", "finalize"])
    parser.add_argument(
        "--path",
        help="Directory to operate on (recursively). If omitted, opens the Tk picker.",
    )
    parser.add_argument(
        "--cache",
        help="Path to the SQLite cache file. Defaults to "
             "<path>/.photo_hashes.db (sidecar).",
    )
    parser.add_argument(
        "--output",
        help="(report only) Where to write the HTML report. Defaults to "
             "<path>/duplicate_report.html.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="(mark, finalize) Show the planned renames without touching disk.",
    )
    parser.add_argument(
        "--phash-threshold", type=int, default=DEFAULT_PHASH_HAMMING_THRESHOLD,
        help=f"(report, mark) Hamming distance for fuzzy pHash matching in "
             f"tier 3 (images) and tier 2 (videos). 0 = exact match only; "
             f"default {DEFAULT_PHASH_HAMMING_THRESHOLD} catches takeout "
             f"re-encoding; higher catches more visually-similar shots at the "
             f"cost of false-positive groups.",
    )

    cli_arguments = parser.parse_args()
    configure_logging("find_duplicate_photos")
    main(cli_arguments)
