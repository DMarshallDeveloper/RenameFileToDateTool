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


def _candidates_in_same_dir(after_dict, backup_dir, canonical_prefix):
    """Return after-side rel paths in ``backup_dir`` whose basename starts with
    ``canonical_prefix + '_'`` (the ``_N`` counter follows)."""
    matches = []
    for after_rel in after_dict:
        if os.path.dirname(after_rel) != backup_dir:
            continue
        after_base = os.path.splitext(os.path.basename(after_rel))[0]
        if after_base.startswith(canonical_prefix + '_'):
            matches.append(after_rel)
    return matches


def _soft_delete_path(master_root, basename):
    """If ``basename`` was soft-deleted under any of the known
    ``_Inbox/removed_*/`` folders, return its relative path. Else ``None``."""
    for folder in SOFT_DELETE_FOLDER_NAMES:
        candidate_rel = os.path.join(SOFT_DELETE_INBOX, folder, basename)
        if os.path.isfile(os.path.join(master_root, candidate_rel)):
            return candidate_rel
    return None


def find_match(backup_rel, backup_size, after_dict, master_root):
    """Try the pairing strategies in priority order. Returns
    ``(match_type, after_rel)`` for the chosen pair, or ``None`` if no master
    counterpart can be found.

    ``after_dict``: ``walk_tree(master_root)`` result.
    ``master_root``: absolute path; needed for the soft-delete lookup.
    """
    # 1. Exact filename match.
    if backup_rel in after_dict:
        return 'same_name', backup_rel

    backup_dir = os.path.dirname(backup_rel)
    backup_name = os.path.basename(backup_rel)
    backup_ext = os.path.splitext(backup_name)[1].lower()

    # 2. Placeholder rename pair.
    placeholder = placeholder_counterpart(backup_rel)
    if placeholder and placeholder in after_dict:
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
        candidates = _candidates_in_same_dir(after_dict, backup_dir, canonical_prefix)

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
        if transcoded_rel in after_dict:
            return 'transcode', transcoded_rel

    return None


def _verify_soft_delete(backup_rel, master_root):
    """For transcoded files, verify the original is preserved in a soft-delete
    folder. Returns True if the soft-deleted copy exists, False otherwise."""
    basename = os.path.basename(backup_rel)
    return _soft_delete_path(master_root, basename) is not None


def diff_trees(before, after, master_root):
    """Pair up files between two ``walk_tree`` dicts. Returns a results dict
    keyed by match category plus the lists for unmatched and size-anomaly
    reporting."""
    consumed_after = set()
    matched_by_type = defaultdict(list)
    transcodes_missing_soft_delete = []
    size_anomalies = []

    for backup_rel, backup_size in before.items():
        # Skip the soft-delete folders on the backup side — those represent
        # files that existed in the master BEFORE we soft-deleted them, and
        # they're not part of the backup-vs-master diff for the year tree.
        # (If the backup includes the master's _Inbox, that's a flat duplicate.)
        if any(seg in backup_rel.split(os.sep) for seg in SOFT_DELETE_FOLDER_NAMES):
            continue

        result = find_match(backup_rel, backup_size, after, master_root)
        if result is None:
            continue  # accounted for in only_in_before below
        match_type, after_rel = result
        consumed_after.add(after_rel)
        matched_by_type[match_type].append((backup_rel, after_rel))

        after_size = after.get(after_rel, -1)
        # Size sanity check, skipped for transcodes (format-level change).
        if match_type != 'transcode' and after_size >= 0:
            if abs(after_size - backup_size) > SIZE_TOLERANCE_BYTES:
                size_anomalies.append(
                    (backup_rel, after_rel, backup_size, after_size, match_type)
                )

        if match_type == 'transcode':
            if not _verify_soft_delete(backup_rel, master_root):
                transcodes_missing_soft_delete.append((backup_rel, after_rel))

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

    return {
        'matched_by_type': dict(matched_by_type),
        'only_in_before': only_in_before,
        'only_in_after': only_in_after,
        'size_anomalies': size_anomalies,
        'transcodes_missing_soft_delete': transcodes_missing_soft_delete,
    }


def _log_sample(level, header, items, formatter, cap=30):
    if not items:
        return
    logger.log(level, "")
    logger.log(level, header)
    for item in items[:cap]:
        logger.log(level, "  %s", formatter(item))
    if len(items) > cap:
        logger.log(level, "  ... and %d more", len(items) - cap)


def report(result, before_count, after_count):
    matched_by_type = result['matched_by_type']
    only_in_before = result['only_in_before']
    only_in_after = result['only_in_after']
    size_anomalies = result['size_anomalies']
    transcodes_missing_soft_delete = result['transcodes_missing_soft_delete']

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
    logger.info("Transcodes missing soft-delete backup:        %d",
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
        "Transcodes whose soft-deleted original is missing (data loss?):",
        transcodes_missing_soft_delete,
        lambda x: f"{x[0]} -> {x[1]} (no copy in _Inbox/removed_*/)",
    )

    clean = not (only_in_before or only_in_after or size_anomalies
                 or transcodes_missing_soft_delete)
    logger.info("")
    if clean:
        logger.info("VERDICT: every file in BEFORE accounted for; no surprises in AFTER.")
    else:
        logger.warning("VERDICT: review the anomalies above before trusting the migration.")


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
