"""compare_libraries.py — diff two photo library trees.

After a Mode 1 sweep modifies the master library, run this against a pre-sweep
backup to verify nothing unexpected happened. It answers:

  - Did any file disappear? (file in backup but not in master)
  - Did any file appear? (file in master but not in backup)
  - Did any file's size change dramatically? (potential corruption — exiftool
    only rewrites container metadata, so size diffs > a few KB are suspicious)

The script understands the placeholder-bump rename pairing: a file named
``YYYY-01-01 00.00.00_N.ext`` in the backup gets paired with
``YYYY-01-01 13.00.00_N.ext`` in the master, because that's exactly the rename
Mode 1 performs alongside the EXIF bump.

Intentionally read-only and *not* exhaustive on EXIF tag diffs — use
``audit_master.py`` for that. This tool's job is "did anything unexpected
happen?", not "is the metadata correct?".

Run:
    python compare_libraries.py --before <backup-path> --after <master-path>

Both flags are optional; a folder picker opens for any omitted path.
"""

import argparse
import logging
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.join(REPO_ROOT, "RenameFileToDateTool")
sys.path.insert(0, SCRIPT_DIR)

from photo_lib.logging_setup import configure_logging
from photo_lib.tk_picker import resolve_directory

logger = logging.getLogger("photo_lib")

# Matches either side of the placeholder bump rename pair.
# Captures (year, time-part, _N.ext) so the swap is straightforward.
PLACEHOLDER_PAIR_RE = re.compile(r'^(\d{4})-01-01 (00\.00\.00|13\.00\.00)(_\d+\.[^.]+)$')

# Exiftool rewrites usually change file size by < 1 KB. Anything > 100 KB
# warrants a look. A photo or video losing tens of MB after the sweep is
# the "did something break?" signal this tool exists to catch.
SIZE_TOLERANCE_BYTES = 100 * 1024


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
    """Given a relative path whose basename is a placeholder filename, return the
    counterpart relative path (``00.00.00`` <-> ``13.00.00``). Returns ``None``
    if the basename isn't a placeholder.
    """
    dirname, basename = os.path.split(rel_path)
    m = PLACEHOLDER_PAIR_RE.match(basename)
    if not m:
        return None
    year, time_part, rest = m.groups()
    other_time = '13.00.00' if time_part == '00.00.00' else '00.00.00'
    return os.path.join(dirname, f'{year}-01-01 {other_time}{rest}')


def diff_trees(before, after):
    """Pair up files between two ``walk_tree`` dicts. Returns a results dict
    with counts and the lists needed to drive the report."""
    consumed_before = set()
    matched_same = []      # (rel, before_size, after_size)
    matched_renamed = []   # (after_rel, before_rel, before_size, after_size)
    only_in_after = []

    for rel, after_size in after.items():
        if rel in before:
            consumed_before.add(rel)
            matched_same.append((rel, before[rel], after_size))
            continue
        counterpart = placeholder_counterpart(rel)
        if counterpart and counterpart in before:
            consumed_before.add(counterpart)
            matched_renamed.append((rel, counterpart, before[counterpart], after_size))
            continue
        only_in_after.append(rel)

    only_in_before = sorted(rel for rel in before if rel not in consumed_before)

    size_anomalies = []
    for rel, b_size, a_size in matched_same:
        if abs(a_size - b_size) > SIZE_TOLERANCE_BYTES:
            size_anomalies.append((rel, b_size, a_size, "same-name"))
    for a_rel, _b_rel, b_size, a_size in matched_renamed:
        if abs(a_size - b_size) > SIZE_TOLERANCE_BYTES:
            size_anomalies.append((a_rel, b_size, a_size, "renamed"))

    return {
        "matched_same": matched_same,
        "matched_renamed": matched_renamed,
        "only_in_before": only_in_before,
        "only_in_after": sorted(only_in_after),
        "size_anomalies": size_anomalies,
    }


def report(result, before_count, after_count):
    """Print a per-section summary to the shared logger."""
    matched_same = len(result["matched_same"])
    matched_renamed = len(result["matched_renamed"])
    only_in_before = result["only_in_before"]
    only_in_after = result["only_in_after"]
    size_anomalies = result["size_anomalies"]

    logger.info("")
    logger.info("=" * 72)
    logger.info("SUMMARY")
    logger.info("=" * 72)
    logger.info("Files in BEFORE: %d", before_count)
    logger.info("Files in AFTER:  %d", after_count)
    logger.info("  Matched by same name:        %d", matched_same)
    logger.info("  Matched by placeholder rename (00.00.00 <-> 13.00.00): %d", matched_renamed)
    logger.info("  Only in BEFORE (missing):    %d", len(only_in_before))
    logger.info("  Only in AFTER  (new):        %d", len(only_in_after))
    logger.info("  Size anomalies (>%d KB):    %d", SIZE_TOLERANCE_BYTES // 1024,
                len(size_anomalies))

    if only_in_before:
        logger.warning("")
        logger.warning("Files in BEFORE but missing from AFTER (possible data loss):")
        for rel in only_in_before[:30]:
            logger.warning("  - %s", rel)
        if len(only_in_before) > 30:
            logger.warning("  ... and %d more", len(only_in_before) - 30)

    if only_in_after:
        logger.warning("")
        logger.warning("Files in AFTER but missing from BEFORE (unexpected additions):")
        for rel in only_in_after[:30]:
            logger.warning("  + %s", rel)
        if len(only_in_after) > 30:
            logger.warning("  ... and %d more", len(only_in_after) - 30)

    if size_anomalies:
        logger.warning("")
        logger.warning("Size anomalies (>%d KB diff — investigate):", SIZE_TOLERANCE_BYTES // 1024)
        for rel, b, a, kind in size_anomalies[:30]:
            logger.warning("  %s: before=%d after=%d diff=%+d (%s)", rel, b, a, a - b, kind)
        if len(size_anomalies) > 30:
            logger.warning("  ... and %d more", len(size_anomalies) - 30)

    if not (only_in_before or only_in_after or size_anomalies):
        logger.info("")
        logger.info("No anomalies. Every file accounted for, no size surprises.")


def main(before_root, after_root):
    logger.info("Walking BEFORE: %s", before_root)
    before = walk_tree(before_root)
    logger.info("  %d files", len(before))
    logger.info("Walking AFTER:  %s", after_root)
    after = walk_tree(after_root)
    logger.info("  %d files", len(after))

    result = diff_trees(before, after)
    report(result, len(before), len(after))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Diff two photo library trees.")
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
