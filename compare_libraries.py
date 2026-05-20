"""compare_libraries.py — diff two photo library trees, watertight edition.

Run this against a pre-sweep backup to verify everything in the master library
is accounted for. Designed around the specific transformations Mode 0 / Mode 1
/ the converter / the structural cleanup scripts perform:

  - **same name**: file unchanged.
  - **placeholder rename**: ``YYYY-01-01 00.00.00_N`` → ``YYYY-01-01 13.00.00_N``
    (Mode 1's auto-rename alongside the EXIF bump).
  - **canonical rename**: any parseable filename → ``YYYY-MM-DD HH.MM.SS_N.ext``
    (Mode 0's rename-from-EXIF). Catches old Google Takeout names like
    ``2025-06-10_09-36-20.jpg``.
  - **extension change**: same date+time, different extension. E.g. ``.heic``
    file that was actually JPEG → ``.jpg``, or ``.jpeg`` → ``.jpg``.
  - **transcode**: ``.mpg`` / ``.3gp`` / ``.avi`` original → ``.mp4`` plus the
    original soft-deleted to ``_Inbox/removed_*/``. Verifies BOTH halves.
  - **size tiebreak**: when multiple master candidates share a date+time prefix
    (collisions), the closest-size match wins.

For files where pairing fails the script falls back to:
  - Only in BEFORE: a list of backup files that have no master counterpart —
    the "did we lose data?" signal.
  - Only in AFTER: master files with no backup counterpart — legitimate new
    additions OR unexpected files.

Read-only. Use ``audit_master.py`` for EXIF-level verification — this tool's
job is "is every file accounted for?", not "is the metadata correct?".

Run:
    python compare_libraries.py --before <backup-path> --after <master-path>
"""

import argparse
import logging
import os
import re
import sys
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.join(REPO_ROOT, "RenameFileToDateTool")
sys.path.insert(0, SCRIPT_DIR)

from photo_lib.filename_pattern import (  # noqa: E402
    PLACEHOLDER_FILENAME_RE,
    apply_placeholder_time_bump,
    parse_filename_datetime,
    parse_filename_year,
)
from photo_lib.logging_setup import configure_logging  # noqa: E402
from photo_lib.tk_picker import resolve_directory  # noqa: E402

logger = logging.getLogger("photo_lib")

# Matches either side of the placeholder bump rename pair.
PLACEHOLDER_PAIR_RE = re.compile(r'^(\d{4})-01-01 (00\.00\.00|13\.00\.00)(_\d+\.[^.]+)$')

# Exiftool rewrites usually change file size by < 1 KB; anything > 100 KB is
# worth a look UNLESS it's a known transcode (where the format changed entirely).
SIZE_TOLERANCE_BYTES = 100 * 1024

# Extensions the converter (convert_unwanted_formats.py) turns
# into .mp4. If a backup file with one of these extensions has a master-side
# counterpart with .mp4, treat as a transcode (expected to differ in size).
TRANSCODED_FROM_EXTS = {'.mpg', '.3gp', '.avi', '.gif'}
TRANSCODED_TO_EXT = '.mp4'

# Where the converter's "soft-delete" of originals lives (these match the
# folder names the soft-delete step uses).
SOFT_DELETE_FOLDER_NAMES = ('removed_mpgs', 'removed_3gps')
SOFT_DELETE_INBOX = '_Inbox'

# All pairing strategies the diff can produce, in priority order. The diff
# emits a "match type" per matched pair so the report can show category counts.
MATCH_TYPES = (
    'same_name',
    'placeholder_rename',
    'canonical_rename',
    'extension_change',
    'jpeg_to_jpg',
    'transcode',
    'size_tiebreak',
)


def walk_tree(root):
    """Return ``dict[rel_path -> size_bytes]`` for every regular file under ``root``."""
    files = {}
    root_abs = os.path.abspath(root)
    for dirpath, _, filenames in os.walk(root_abs):
        for f in filenames:
            full = os.path.join(dirpath, f)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            rel = os.path.relpath(full, root_abs)
            files[rel] = size
    return files


def placeholder_counterpart(rel_path):
    """Given a relative path whose basename is a placeholder filename, return
    the counterpart relative path (``00.00.00`` ↔ ``13.00.00``). ``None`` if
    not a placeholder."""
    dirname, basename = os.path.split(rel_path)
    m = PLACEHOLDER_PAIR_RE.match(basename)
    if not m:
        return None
    year, time_part, rest = m.groups()
    other_time = '13.00.00' if time_part == '00.00.00' else '00.00.00'
    return os.path.join(dirname, f'{year}-01-01 {other_time}{rest}')


def _candidates_in_same_dir(after_dict, backup_dir, canonical_prefix, consumed_after=None):
    """Return after-side rel paths in ``backup_dir`` whose basename starts with
    ``canonical_prefix + '_'`` (the ``_N`` counter follows). Already-consumed
    after files are skipped so each after file pairs with at most one backup."""
    matches = []
    consumed_after = consumed_after or set()
    for after_rel in after_dict:
        if after_rel in consumed_after:
            continue
        if os.path.dirname(after_rel) != backup_dir:
            continue
        after_base = os.path.splitext(os.path.basename(after_rel))[0]
        if after_base.startswith(canonical_prefix + '_'):
            matches.append(after_rel)
    return matches


def find_match(backup_rel, backup_size, after_dict, master_root, consumed_after=None):
    """Try the pairing strategies in priority order. Returns
    ``(match_type, after_rel)`` for the chosen pair, or ``None`` if no master
    counterpart can be found.

    ``after_dict``: ``walk_tree(master_root)`` result.
    ``master_root``: absolute path; needed for the soft-delete lookup.
    ``consumed_after``: set of after rel paths already paired in this run.
    Skip them so each after file pairs to exactly one backup file — otherwise
    a second backup file with the same canonical prefix would get falsely
    paired to an already-claimed after file and disappear from ``only_in_before``.
    """
    consumed_after = consumed_after or set()
    # 1. Exact filename match.
    if backup_rel in after_dict and backup_rel not in consumed_after:
        return 'same_name', backup_rel

    backup_dir = os.path.dirname(backup_rel)
    backup_name = os.path.basename(backup_rel)
    backup_ext = os.path.splitext(backup_name)[1].lower()

    # 2. Placeholder rename pair.
    placeholder = placeholder_counterpart(backup_rel)
    if placeholder and placeholder in after_dict and placeholder not in consumed_after:
        return 'placeholder_rename', placeholder

    # 3. Parse datetime from filename — drives canonical-rename / extension-change.
    dt = parse_filename_datetime(backup_name)
    if dt is not None:
        # Apply the same bump Mode 1 applies (in case the backup file was already
        # placeholder-bumped before the sweep, or this is just a regular file).
        is_placeholder = bool(PLACEHOLDER_FILENAME_RE.match(backup_name))
        if is_placeholder:
            dt = apply_placeholder_time_bump(backup_name, dt)
        canonical_prefix = dt.strftime('%Y-%m-%d %H.%M.%S')
        candidates = _candidates_in_same_dir(after_dict, backup_dir, canonical_prefix, consumed_after)

        if len(candidates) == 1:
            cand = candidates[0]
            cand_ext = os.path.splitext(cand)[1].lower()
            if is_placeholder:
                return 'placeholder_rename', cand
            if cand_ext == backup_ext:
                # Same extension, different name structure (e.g. canonical rename).
                return 'canonical_rename', cand
            if {cand_ext, backup_ext} == {'.jpg', '.jpeg'}:
                return 'jpeg_to_jpg', cand
            if backup_ext in TRANSCODED_FROM_EXTS and cand_ext == TRANSCODED_TO_EXT:
                return 'transcode', cand
            # Some other extension swap — still a paired file. Common case is
            # heic→jpg (file with .heic name but JPEG content was renamed).
            return 'extension_change', cand

        if len(candidates) > 1:
            # Multiple master files share the same date+time prefix (collisions).
            # Pick the closest size — that's almost always the rename target.
            best = min(candidates, key=lambda c: abs(after_dict[c] - backup_size))
            return 'size_tiebreak', best

    # 4. Transcode without matching canonical-prefix (e.g. backup file's name
    # doesn't parse — fall back to base-name match against any .mp4).
    if backup_ext in TRANSCODED_FROM_EXTS:
        backup_base = os.path.splitext(backup_name)[0]
        transcoded_rel = os.path.join(backup_dir, backup_base + TRANSCODED_TO_EXT)
        if transcoded_rel in after_dict and transcoded_rel not in consumed_after:
            return 'transcode', transcoded_rel

    return None


def diff_trees(before, after, master_root):
    """Pair up files between two ``walk_tree`` dicts. Returns a results dict
    keyed by match category plus the lists for unmatched and size-anomaly
    reporting.

    Per-pair decisions are emitted at DEBUG level so the on-disk log holds a
    complete pairing record (useful for ``grep``-style verification on a
    specific file) while the console stays at INFO-level summary."""
    consumed_after = set()
    matched_by_type = defaultdict(list)
    size_anomalies = []

    for backup_rel, backup_size in before.items():
        # Skip the soft-delete folders on the backup side — those represent
        # files that existed in the master BEFORE we soft-deleted them, and
        # they're not part of the backup-vs-master diff for the year tree.
        # (If the backup includes the master's _Inbox, that's a flat duplicate.)
        if any(seg in backup_rel.split(os.sep) for seg in SOFT_DELETE_FOLDER_NAMES):
            continue

        result = find_match(backup_rel, backup_size, after, master_root, consumed_after)
        if result is None:
            logger.debug("NO MATCH: %s (backup size=%d)", backup_rel, backup_size)
            continue  # accounted for in only_in_before below
        match_type, after_rel = result
        consumed_after.add(after_rel)
        matched_by_type[match_type].append((backup_rel, after_rel))

        after_size = after.get(after_rel, -1)
        size_diff = after_size - backup_size if after_size >= 0 else 0
        logger.debug("%s: %s -> %s (size diff %+d)",
                     match_type, backup_rel, after_rel, size_diff)

        # Size sanity check, skipped for transcodes (format-level change is
        # expected to shift size by tens of MB).
        if match_type != 'transcode' and after_size >= 0:
            if abs(size_diff) > SIZE_TOLERANCE_BYTES:
                size_anomalies.append(
                    (backup_rel, after_rel, backup_size, after_size, match_type)
                )

    matched_set = {b for paired in matched_by_type.values() for (b, _a) in paired}
    only_in_before = sorted(b for b in before
                            if b not in matched_set
                            and not any(seg in b.split(os.sep) for seg in SOFT_DELETE_FOLDER_NAMES))

    # Master files that didn't get paired — exclude the soft-delete folders
    # (they're not unexpected additions; they're soft-deleted originals).
    only_in_after = sorted(
        a for a in after
        if a not in consumed_after
        and not any(seg in a.split(os.sep) for seg in SOFT_DELETE_FOLDER_NAMES)
    )

    # Transcodes must preserve their original somewhere under _Inbox/removed_*/.
    # If the original isn't there, the converter ran but the soft-delete step
    # didn't — raise it as a separate finding so the migration isn't trusted.
    transcodes_missing_soft_delete = []
    for backup_rel, _after_rel in matched_by_type.get('transcode', []):
        backup_name = os.path.basename(backup_rel)
        soft_deleted_present = any(
            os.path.join(SOFT_DELETE_INBOX, folder_name, backup_name) in after
            for folder_name in SOFT_DELETE_FOLDER_NAMES
        )
        if not soft_deleted_present:
            transcodes_missing_soft_delete.append(backup_rel)

    only_in_after_by_reason = categorize_unmatched_files(
        unmatched_rel_paths=only_in_after,
        opposite_side_rel_paths=before,
        matched_pairs_by_type=matched_by_type,
        opposite_side_is_before=True,
    )
    only_in_before_by_reason = categorize_unmatched_files(
        unmatched_rel_paths=only_in_before,
        opposite_side_rel_paths=after,
        matched_pairs_by_type=matched_by_type,
        opposite_side_is_before=False,
    )

    per_year_breakdown = build_per_year_breakdown(
        before, after, matched_by_type,
        only_in_after_by_reason, only_in_before_by_reason,
    )

    return {
        'matched_by_type': dict(matched_by_type),
        'only_in_before': only_in_before,
        'only_in_after': only_in_after,
        'size_anomalies': size_anomalies,
        'transcodes_missing_soft_delete': transcodes_missing_soft_delete,
        'only_in_after_by_reason': only_in_after_by_reason,
        'only_in_before_by_reason': only_in_before_by_reason,
        'per_year_breakdown': per_year_breakdown,
    }


def _year_for_rel_path(rel_path):
    """Return the year string for ``rel_path``: prefer the canonical date prefix
    in the basename, fall back to the first 4-digit folder segment (matches the
    library's ``YYYY/...`` layout), else ``'unknown'``.

    Splits on both forward and back slashes so paths produced on either OS are
    handled — relative paths get fed in with whatever separator the source used.
    """
    year = parse_filename_year(os.path.basename(rel_path))
    if year is not None:
        return str(year)
    for segment in re.split(r'[\\/]', rel_path):
        if re.fullmatch(r'\d{4}', segment):
            return segment
    return 'unknown'


def _timestamp_prefix_for_rel_path(rel_path):
    parsed_datetime = parse_filename_datetime(os.path.basename(rel_path))
    return parsed_datetime.strftime('%Y-%m-%d %H.%M.%S') if parsed_datetime else None


def _date_prefix_for_rel_path(rel_path):
    parsed_datetime = parse_filename_datetime(os.path.basename(rel_path))
    return parsed_datetime.strftime('%Y-%m-%d') if parsed_datetime else None


def categorize_unmatched_files(unmatched_rel_paths, opposite_side_rel_paths,
                               matched_pairs_by_type, opposite_side_is_before):
    """Partition unmatched files by their relationship to matched pairs.

    For each unmatched file we ask: is its timestamp/date already represented
    among the matched pairs (suggesting an extra-copy or near-duplicate), or
    is it genuinely isolated (no counterpart anywhere on the other side)?

    Categories — for ``only_in_after`` files (``opposite_side_is_before=True``):
      - ``same_timestamp_extra``: timestamp matches a paired backup file's timestamp.
        Most likely an additional copy at that exact moment.
      - ``same_date_extra``: date appears in the opposite side but the exact time
        does not — a different photo on a known-shared date.
      - ``isolated_extra``: nothing on this date exists on the opposite side —
        a file added after the takeout, or never uploaded.

    Symmetric categories for ``only_in_before`` files use ``*_missing`` names.
    """
    if opposite_side_is_before:
        category_names = ('same_timestamp_extra', 'same_date_extra', 'isolated_extra')
    else:
        category_names = ('same_timestamp_missing', 'same_date_missing', 'isolated_missing')

    matched_timestamps = set()
    matched_dates = set()
    for paired_list in matched_pairs_by_type.values():
        for backup_rel, after_rel in paired_list:
            for rel in (backup_rel, after_rel):
                timestamp_prefix = _timestamp_prefix_for_rel_path(rel)
                if timestamp_prefix:
                    matched_timestamps.add(timestamp_prefix)
                date_prefix = _date_prefix_for_rel_path(rel)
                if date_prefix:
                    matched_dates.add(date_prefix)

    opposite_side_dates = set()
    for rel in opposite_side_rel_paths:
        date_prefix = _date_prefix_for_rel_path(rel)
        if date_prefix:
            opposite_side_dates.add(date_prefix)

    buckets = {name: [] for name in category_names}
    for rel in unmatched_rel_paths:
        timestamp_prefix = _timestamp_prefix_for_rel_path(rel)
        date_prefix = _date_prefix_for_rel_path(rel)
        if timestamp_prefix and timestamp_prefix in matched_timestamps:
            buckets[category_names[0]].append(rel)
        elif date_prefix and (date_prefix in matched_dates or date_prefix in opposite_side_dates):
            buckets[category_names[1]].append(rel)
        else:
            buckets[category_names[2]].append(rel)
    return buckets


def _empty_per_year_row():
    return {
        'before': 0,
        'after': 0,
        'matched': 0,
        'same_timestamp_extra': 0,
        'same_date_extra': 0,
        'isolated_extra': 0,
        'same_timestamp_missing': 0,
        'same_date_missing': 0,
        'isolated_missing': 0,
    }


def build_per_year_breakdown(before, after, matched_pairs_by_type,
                             only_in_after_by_reason, only_in_before_by_reason):
    """Build ``{year: {before, after, matched, *_extra, *_missing}}`` so each year
    can be shown side-by-side with deltas and the categorized leftovers."""
    per_year = {}

    for rel in before:
        year = _year_for_rel_path(rel)
        per_year.setdefault(year, _empty_per_year_row())['before'] += 1
    for rel in after:
        year = _year_for_rel_path(rel)
        per_year.setdefault(year, _empty_per_year_row())['after'] += 1

    for paired_list in matched_pairs_by_type.values():
        for backup_rel, after_rel in paired_list:
            # The two halves of a pair are almost always the same year, but be defensive:
            # count the year of the backup side (the "before" perspective).
            year = _year_for_rel_path(backup_rel) or _year_for_rel_path(after_rel)
            per_year.setdefault(year, _empty_per_year_row())['matched'] += 1

    for category_name, rels in only_in_after_by_reason.items():
        for rel in rels:
            year = _year_for_rel_path(rel)
            per_year.setdefault(year, _empty_per_year_row())[category_name] += 1
    for category_name, rels in only_in_before_by_reason.items():
        for rel in rels:
            year = _year_for_rel_path(rel)
            per_year.setdefault(year, _empty_per_year_row())[category_name] += 1

    return per_year


def _log_sample(level, header, items, formatter, console_cap=30):
    """Console (INFO+) sees the first ``console_cap`` entries plus an
    "...and N more" summary; the log file (DEBUG+) gets every entry. Run
    ``grep -F`` on the log file when you want the exhaustive list."""
    if not items:
        return
    logger.log(level, "")
    logger.log(level, header)
    for index, item in enumerate(items):
        if index < console_cap:
            logger.log(level, "  %s", formatter(item))
        else:
            # DEBUG goes to the file handler only — full list lands on disk.
            logger.debug("  %s", formatter(item))
    if len(items) > console_cap:
        logger.log(level, "  ... and %d more (full list in compare_libraries.log)",
                   len(items) - console_cap)


def _log_per_year_table(per_year):
    """Side-by-side per-year tally so the user can immediately see which years
    are imbalanced and why — same_timestamp_extra/missing usually means duplicate
    copies, same_date means a sibling photo on the same day, isolated means a
    photo with no counterpart on its date at all."""
    if not per_year:
        return
    logger.info("")
    logger.info("PER-YEAR BREAKDOWN")
    logger.info("=" * 100)
    header = (
        f"{'Year':<8}{'Before':>8}{'After':>8}{'Delta':>8}{'Matched':>10}"
        f"{'B-only':>8}{'B-tsM':>8}{'B-dtM':>8}{'B-isoM':>8}"
        f"{'A-tsX':>8}{'A-dtX':>8}{'A-isoX':>8}"
    )
    logger.info(header)
    logger.info("-" * len(header))
    for year in sorted(per_year):
        row = per_year[year]
        delta = row['after'] - row['before']
        b_only = row['same_timestamp_missing'] + row['same_date_missing'] + row['isolated_missing']
        logger.info(
            f"{year:<8}{row['before']:>8}{row['after']:>8}{delta:>+8}{row['matched']:>10}"
            f"{b_only:>8}{row['same_timestamp_missing']:>8}{row['same_date_missing']:>8}{row['isolated_missing']:>8}"
            f"{row['same_timestamp_extra']:>8}{row['same_date_extra']:>8}{row['isolated_extra']:>8}"
        )
    logger.info("")
    logger.info("Column key:")
    logger.info("  B-tsM  same_timestamp_missing — Before has a file whose timestamp matches a paired After file (likely an extra copy on Before)")
    logger.info("  B-dtM  same_date_missing      — Before file whose date appears on After but the exact time does not")
    logger.info("  B-isoM isolated_missing       — Before file whose date does not appear on After at all (data loss?)")
    logger.info("  A-tsX  same_timestamp_extra   — After has a file whose timestamp matches a paired Before file (likely an extra copy on After)")
    logger.info("  A-dtX  same_date_extra        — After file whose date appears on Before but the exact time does not")
    logger.info("  A-isoX isolated_extra         — After file whose date does not appear on Before at all (post-takeout addition?)")


def report(result, before_count, after_count):
    matched_by_type = result['matched_by_type']
    only_in_before = result['only_in_before']
    only_in_after = result['only_in_after']
    size_anomalies = result['size_anomalies']
    transcodes_missing_soft_delete = result.get('transcodes_missing_soft_delete', [])
    per_year_breakdown = result.get('per_year_breakdown', {})
    only_in_after_by_reason = result.get('only_in_after_by_reason', {})
    only_in_before_by_reason = result.get('only_in_before_by_reason', {})

    logger.info("")
    logger.info("=" * 72)
    logger.info("PAIRING SUMMARY")
    logger.info("=" * 72)
    logger.info("Files in BEFORE: %d", before_count)
    logger.info("Files in AFTER:  %d", after_count)
    logger.info("")
    logger.info("Matched by category:")
    total_matched = 0
    for match_type in MATCH_TYPES:
        count = len(matched_by_type.get(match_type, []))
        total_matched += count
        logger.info("  %-20s %5d", match_type, count)
    logger.info("  %-20s %5d", 'TOTAL MATCHED', total_matched)
    logger.info("")
    logger.info("Unmatched:")
    logger.info("  only in BEFORE (potential data loss):       %d", len(only_in_before))
    logger.info("  only in AFTER  (additions or unexplained):  %d", len(only_in_after))
    logger.info("Size anomalies (>%d KB, non-transcode):       %d",
                SIZE_TOLERANCE_BYTES // 1024, len(size_anomalies))
    logger.info("Transcodes with no soft-deleted original:     %d",
                len(transcodes_missing_soft_delete))

    _log_sample(
        logging.WARNING,
        "Files in BEFORE but missing from AFTER (data loss?):",
        only_in_before,
        lambda x: f"- {x}",
    )
    _log_sample(
        logging.WARNING,
        "Files in AFTER with no BEFORE counterpart (unexpected additions?):",
        only_in_after,
        lambda x: f"+ {x}",
    )
    _log_sample(
        logging.WARNING,
        f"Size anomalies (> {SIZE_TOLERANCE_BYTES // 1024} KB diff):",
        size_anomalies,
        lambda x: f"{x[0]}: before={x[2]} after={x[3]} diff={x[3] - x[2]:+d} ({x[4]})",
    )
    _log_sample(
        logging.WARNING,
        "Transcodes WITHOUT a soft-deleted original under _Inbox/removed_*/:",
        transcodes_missing_soft_delete,
        lambda x: f"! {x}",
    )

    _log_per_year_table(per_year_breakdown)

    if only_in_after_by_reason:
        logger.info("")
        logger.info("UNMATCHED AFTER-SIDE FILES BY REASON")
        logger.info("-" * 72)
        for category_name in ('same_timestamp_extra', 'same_date_extra', 'isolated_extra'):
            logger.info("  %-25s %d", category_name, len(only_in_after_by_reason.get(category_name, [])))
    if only_in_before_by_reason:
        logger.info("")
        logger.info("UNMATCHED BEFORE-SIDE FILES BY REASON")
        logger.info("-" * 72)
        for category_name in ('same_timestamp_missing', 'same_date_missing', 'isolated_missing'):
            logger.info("  %-25s %d", category_name, len(only_in_before_by_reason.get(category_name, [])))

    clean = not (only_in_before or only_in_after or size_anomalies or transcodes_missing_soft_delete)
    logger.info("")
    if clean:
        logger.info("VERDICT: every file in BEFORE accounted for; no surprises in AFTER.")
    else:
        logger.warning("VERDICT: review the anomalies above before trusting the migration.")
        logger.warning("Per-pair decisions are in the DEBUG log: "
                       "logs/compare_libraries.log")


def main(before_root, after_root):
    logger.info("Walking BEFORE: %s", before_root)
    before = walk_tree(before_root)
    logger.info("  %d files", len(before))
    logger.info("Walking AFTER:  %s", after_root)
    after = walk_tree(after_root)
    logger.info("  %d files", len(after))

    result = diff_trees(before, after, os.path.abspath(after_root))
    report(result, len(before), len(after))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Diff two photo library trees (watertight).")
    parser.add_argument("--before", help="Pre-sweep backup library path. Picker if omitted.")
    parser.add_argument("--after", help="Post-sweep master library path. Picker if omitted.")
    args = parser.parse_args()

    configure_logging("compare_libraries")
    before_root = resolve_directory(args.before, "Select BEFORE (backup) library")
    after_root = resolve_directory(args.after, "Select AFTER (master) library")
    if before_root and after_root:
        main(before_root, after_root)
    else:
        logger.error("Both paths required. Aborting.")
