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
)
from photo_lib.extensions import is_media, normalize_extension
from photo_lib.logging_setup import configure_logging
from photo_lib.tk_picker import resolve_directory

logger = logging.getLogger("photo_lib")


def iter_media_paths(root: str):
    for current_dir, _subdirs, filenames in os.walk(root):
        for name in filenames:
            extension = normalize_extension(os.path.splitext(name)[1])
            if is_media(extension):
                yield os.path.join(current_dir, name)


def scan(root: str, cache_path: str | None = None) -> int:
    cache_path = cache_path or default_cache_path(root)
    paths = list(iter_media_paths(root))
    logger.info("Scanning %d media files under %s (cache: %s)",
                len(paths), root, cache_path)
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
                logger.info("  hashed %d new files", hashed_count)
    logger.info("Scan done: %d hashed, %d cache hits", hashed_count, cache_hits)
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


def report(root: str, output_path: str, cache_path: str | None = None,
           phash_threshold: int = DEFAULT_PHASH_HAMMING_THRESHOLD) -> int:
    fingerprints, groups = _load_groups(root, cache_path, phash_threshold)
    html_text = render_html_report(groups, root)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(html_text)
    logger.info("Wrote %d-group duplicate report to %s (phash_threshold=%d)",
                len(groups), output_path, phash_threshold)

    grouped_paths = {fp.path for group in groups for fp in group.fingerprints}
    singletons = [fp for fp in fingerprints if fp.path not in grouped_paths]
    singletons_path = os.path.join(
        os.path.dirname(output_path) or ".",
        "singletons_report.html",
    )
    singletons_html = render_singletons_html_report(singletons, root)
    with open(singletons_path, "w", encoding="utf-8") as fh:
        fh.write(singletons_html)
    logger.info("Wrote %d-singleton report to %s",
                len(singletons), singletons_path)

    return len(groups)


def mark(root: str, dry_run: bool, cache_path: str | None = None,
         phash_threshold: int = DEFAULT_PHASH_HAMMING_THRESHOLD) -> int:
    _fingerprints, groups = _load_groups(root, cache_path, phash_threshold)
    plan = plan_mark(groups)
    prefix = "[DRY-RUN] " if dry_run else ""
    logger.info("%s%d files would be renamed across %d groups",
                prefix, len(plan), len(groups))
    for old_path, new_path, tier in plan[:25]:
        logger.info("%s  T%d  %s  ->  %s",
                    prefix, tier,
                    os.path.basename(old_path), os.path.basename(new_path))
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
    return len(plan)


def finalize(root: str, dry_run: bool, cache_path: str | None = None) -> int:
    plan = plan_finalize(root)
    prefix = "[DRY-RUN] " if dry_run else ""
    logger.info("%s%d lone-survivor files would have their suffix stripped",
                prefix, len(plan))
    for old_path, new_path in plan[:25]:
        logger.info("%s  %s  ->  %s",
                    prefix,
                    os.path.basename(old_path), os.path.basename(new_path))
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
