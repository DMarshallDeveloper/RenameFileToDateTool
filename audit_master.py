"""audit_master.py — read-only diagnostic: where has the library drifted?

The master library should hold these invariants:
  - Every file's filename matches its embedded EXIF/QuickTime dates.
  - Filename extensions match the file's actual content (no ``.png`` files
    that are JPEG bytes, no ``.heic`` files that are JPEG bytes, etc).
  - Every file lives in the year folder its filename names.
  - Every media filename matches ``YYYY-MM-DD HH.MM.SS_N.ext``.

This script samples a few files per (year, extension) for the EXIF check
(``check_file``) and walks every file for the structural checks
(``check_extension_mismatches``, ``check_year_folder_mismatches``,
``check_non_canonical_filenames``). It prints a per-folder verdict plus a
list of structural anomalies you can act on with ``write_exif_from_filename.py``,
``convert_unwanted_formats.py``, or a one-off rename.

**Read-only — modifies nothing.** Safe to run anytime.

Two subtleties the EXIF check knows about (matching the writer's behavior):

  1. Per-file timezone. If a video's ``CreationDate`` carries an explicit ``+10:00``
     (because it was shot in Melbourne), the audit checks its UTC tags against
     "filename time minus 10h", NOT "filename time minus NZ offset". Without this,
     every overseas photo would falsely show as ``NEEDS FIX`` and a user trying to
     "re-fix" it would actually corrupt the metadata.
  2. Jan-1 placeholder bump. Filenames like ``2000-01-01 00.00.00_1.jpg`` are
     written by ``write_exif_from_filename.py`` with EXIF time = 13:00 (so the
     date doesn't roll back to Dec 31 in UTC viewers) AND the file is renamed
     to match. The audit applies the same bump before comparing legacy files.

Run with ``python audit_master.py``. Edit ``photo_lib/config.py`` (set
``MASTER_ROOT``) if your master library lives elsewhere.
"""
import logging
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.join(REPO_ROOT, "RenameFileToDateTool")
sys.path.insert(0, SCRIPT_DIR)

from photo_lib.binaries import EXIFTOOL
from photo_lib.config import (
    AUDIT_SAMPLES_PER_TYPE,
    BUNDLED_EARLY_FOLDER,
    BUNDLED_EARLY_YEAR_RANGE,
    MASTER_ROOT,
)
from photo_lib.exiftool_runner import get_metadata_for_tags
from photo_lib.extensions import is_media, normalize_extension
from photo_lib.filename_pattern import (
    CANONICAL_FILENAME_RE,
    FILENAME_PARTS_RE,
    apply_placeholder_time_bump,
    parse_filename_year,
)
from photo_lib.logging_setup import configure_logging
from photo_lib.tag_modes import IMAGE_TAG_MODES, VIDEO_TAG_MODES, FILESYSTEM_TAGS
from photo_lib.timezone_detection import LOCAL_TIMEZONE, detect_file_tz

logger = logging.getLogger("photo_lib")

LOCAL_TZ = LOCAL_TIMEZONE  # back-compat alias for older test imports

SAMPLES_PER_TYPE = AUDIT_SAMPLES_PER_TYPE

# Derive audit tag groupings from the canonical TAG_MODES dicts so adding a tag in
# one place automatically updates audit coverage.
#
# DateCreated is excluded from audit comparison: exiftool stores it as date-only
# ("2000:01:01") in some XMP/IPTC namespaces, even when written with full time.
# The writer still writes it (for completeness), but a strict equality check
# produces false bads.
_AUDIT_EXCLUDED_IMAGE_TAGS = {"DateCreated"}
IMAGE_LOCAL_TAGS = [
    tag for tag, mode in IMAGE_TAG_MODES.items()
    if mode == 'local' and tag not in FILESYSTEM_TAGS and tag not in _AUDIT_EXCLUDED_IMAGE_TAGS
]
VIDEO_UTC_TAGS = [
    tag for tag, mode in VIDEO_TAG_MODES.items() if mode == 'utc'
]
VIDEO_TZ_TAGS = [
    tag for tag, mode in VIDEO_TAG_MODES.items() if mode == 'local_with_tz'
]
# Filesystem tags are checked for both images and videos; strip TZ before comparison.
FILESYSTEM_TAGS = list(FILESYSTEM_TAGS)
# Tags that carry the photo's true TZ — fetched so detect_file_tz can read them.
TZ_HINT_TAGS = ["OffsetTimeOriginal", "OffsetTime", "OffsetTimeDigitized"]

# audit-specific extension classification (sets, matching the IMAGE/VIDEO read above)
from photo_lib.extensions import IMAGE_EXTENSIONS as IMAGE_EXTS  # noqa: E402
from photo_lib.extensions import VIDEO_EXTENSIONS as VIDEO_EXTS  # noqa: E402


def parse_filename_datetime(filename):
    """Parse the YYYY-MM-DD HH.MM.SS portion of a filename into a naive datetime."""
    match = FILENAME_PARTS_RE.match(filename)
    if not match:
        return None
    try:
        return datetime(*map(int, match.groups()))
    except ValueError:
        return None


def strip_tz_suffix(s):
    """Drop a trailing ``+HH:MM`` or ``-HH:MM`` offset for naive comparison."""
    if not s:
        return s
    return re.sub(r'[+-]\d{2}:\d{2}$', '', s).strip()


def to_utc_str(dt, file_tz):
    """Convert a naive local datetime to UTC string format using the file's TZ."""
    aware = dt.replace(tzinfo=file_tz).astimezone(timezone.utc)
    return aware.strftime("%Y:%m:%d %H:%M:%S")


def to_local_with_tz_str(dt, file_tz):
    """Convert a naive local datetime to local string with the file's TZ offset."""
    aware = dt.replace(tzinfo=file_tz)
    offset = aware.strftime("%z")
    return aware.strftime("%Y:%m:%d %H:%M:%S") + f"{offset[:3]}:{offset[3:]}"


def to_local_str(dt):
    return dt.strftime("%Y:%m:%d %H:%M:%S")


def check_file(filename, metadata, expected_dt, is_video):
    """Return list of ``(tag, expected, actual, ok)`` tuples.

    Applies the same placeholder-bump and TZ-detection logic as
    ``write_exif_from_filename.py`` so files written correctly by the writer
    audit clean.
    """
    results = []
    # Apply the same Jan-1 placeholder bump the writer applies. Without this,
    # a 2000-01-01 file with EXIF correctly bumped to 13:00 would be flagged as bad.
    expected_dt = apply_placeholder_time_bump(filename, expected_dt)
    # Detect the photo's true TZ from its existing metadata (CreationDate offset,
    # OffsetTimeOriginal, etc). Falls back to NZ if nothing else is available.
    file_tz = detect_file_tz(metadata, default_tz=LOCAL_TIMEZONE)

    if is_video:
        utc_expected = to_utc_str(expected_dt, file_tz)
        tz_expected = to_local_with_tz_str(expected_dt, file_tz)

        for tag in VIDEO_UTC_TAGS:
            actual = strip_tz_suffix(metadata.get(tag, ""))
            results.append((tag, utc_expected, actual, actual == utc_expected))
        for tag in VIDEO_TZ_TAGS:
            actual = metadata.get(tag, "")
            results.append((tag, tz_expected, actual, actual == tz_expected))
    else:
        local_expected = to_local_str(expected_dt)
        for tag in IMAGE_LOCAL_TAGS:
            actual = strip_tz_suffix(metadata.get(tag, ""))
            # treat missing tag as "not present"; only flag if present and wrong
            if actual:
                results.append((tag, local_expected, actual, actual == local_expected))

    local_expected = to_local_str(expected_dt)
    for tag in FILESYSTEM_TAGS:
        actual = strip_tz_suffix(metadata.get(tag, ""))
        results.append((tag, local_expected, actual, actual == local_expected))

    return results


# ---------------------------------------------------------------------------
# Structural checks (independent of EXIF date comparison).
#
# These walk every file in the year folders (no sampling) and flag structural
# problems that the per-file EXIF check can't catch directly, or only catches
# as opaque "tag empty" symptoms. Cheap to run alongside the existing audit
# because they share the same exiftool batch read (for the extension check)
# or no exiftool at all (for the structural checks).
# ---------------------------------------------------------------------------


def _allowed_years_for_folder(folder_name: str):
    """Return the set of filename years that legitimately belong in this folder.

    Most year folders match a single year (``"2024"`` → ``{2024}``). The early
    bundle folder (``"2000 - 2010"`` by default) covers a configured range.
    Returns ``None`` for folders we don't recognise (e.g. ``"_Inbox"``) —
    callers should skip those.
    """
    try:
        return {int(folder_name)}
    except ValueError:
        if folder_name == BUNDLED_EARLY_FOLDER:
            return set(range(*BUNDLED_EARLY_YEAR_RANGE))
        return None


def check_extension_mismatches(year_folders, master_root):
    """Return list of (folder, filename, claimed_ext, actual_ext) for files whose
    filename extension doesn't match the format exiftool detects inside.

    Catches things like ``.png`` files that are actually JPEGs (a common iOS
    screenshot / Drive sync artifact) — which silently break Mode 1 writes
    because exiftool refuses to write to a container that doesn't match its
    declared format.
    """
    paths = []
    for year in year_folders:
        folder = os.path.join(master_root, year)
        for f in sorted(os.listdir(folder)):
            p = os.path.join(folder, f)
            if os.path.isfile(p):
                paths.append(p)

    if not paths:
        return []

    metadata_list = get_metadata_for_tags(paths, ['FileTypeExtension'])

    mismatches = []
    for entry in metadata_list:
        src = entry.get('SourceFile', '').replace('/', os.sep)
        name_ext = os.path.splitext(src)[1].lower().lstrip('.')
        actual = (entry.get('FileTypeExtension') or '').lower()
        if not actual or name_ext == actual:
            continue
        # Treat .jpg and .jpeg as equivalent — they ARE the same format,
        # only the spelling differs and most tooling handles both.
        if {name_ext, actual} == {'jpg', 'jpeg'}:
            continue
        folder = os.path.basename(os.path.dirname(src))
        filename = os.path.basename(src)
        mismatches.append((folder, filename, name_ext, actual))
    return mismatches


def check_year_folder_mismatches(year_folders, master_root):
    """Return list of (folder, filename, parsed_year) for files whose filename
    year doesn't match the folder they live in.

    Catches files moved into the wrong year folder by mistake (e.g. a
    ``2023-…`` file ended up under ``2024/``). Skips files whose names don't
    carry a parseable year — those are handled by the canonical-name check.
    """
    bad = []
    for folder_name in year_folders:
        allowed = _allowed_years_for_folder(folder_name)
        if allowed is None:
            continue
        folder = os.path.join(master_root, folder_name)
        for f in sorted(os.listdir(folder)):
            p = os.path.join(folder, f)
            if not os.path.isfile(p):
                continue
            file_year = parse_filename_year(f)
            if file_year is None:
                continue
            if file_year not in allowed:
                bad.append((folder_name, f, file_year))
    return bad


def check_non_canonical_filenames(year_folders, master_root):
    """Return list of (folder, filename) for media files whose names don't
    match the canonical ``YYYY-MM-DD HH.MM.SS_N.ext`` pattern.

    Non-media files (``.pdf``, leftover ``.txt``s, etc.) are intentionally
    ignored — only flag files that *should* match the convention.
    """
    bad = []
    for folder_name in year_folders:
        folder = os.path.join(master_root, folder_name)
        for f in sorted(os.listdir(folder)):
            p = os.path.join(folder, f)
            if not os.path.isfile(p):
                continue
            ext = normalize_extension(os.path.splitext(f)[1])
            if not is_media(ext):
                continue
            if not CANONICAL_FILENAME_RE.match(f):
                bad.append((folder_name, f))
    return bad


def _report_structural_findings(title, items, formatter, level=logging.WARNING):
    """Helper to print one of the structural-issue sections, capped at 20 examples."""
    if not items:
        return
    logger.log(level, "")
    logger.log(level, "=" * 72)
    logger.log(level, title)
    logger.log(level, "=" * 72)
    logger.log(level, "%d files:", len(items))
    for item in items[:20]:
        logger.log(level, "  %s", formatter(item))
    if len(items) > 20:
        logger.log(level, "  ... and %d more", len(items) - 20)


def main(master_root: str = MASTER_ROOT):
    """Walk ``master_root``'s year folders and print a per-folder audit report.

    ``master_root`` defaults to the user's master library path. Tests pass a temp tree.
    """
    year_folders = []
    for entry in sorted(os.listdir(master_root)):
        full = os.path.join(master_root, entry)
        if os.path.isdir(full):
            year_folders.append(entry)

    # Collect samples: (year_folder, ext) → list of (filename, full_path)
    samples_by_year_ext = defaultdict(list)
    for year in year_folders:
        folder = os.path.join(master_root, year)
        files_by_ext = defaultdict(list)
        for filename in sorted(os.listdir(folder)):
            full_path = os.path.join(folder, filename)
            if not os.path.isfile(full_path):
                continue
            ext = os.path.splitext(filename)[1].lower().lstrip('.')
            files_by_ext[ext].append((filename, full_path))
        for ext, items in files_by_ext.items():
            samples_by_year_ext[(year, ext)] = items[:SAMPLES_PER_TYPE]

    all_paths = [p for items in samples_by_year_ext.values() for (_, p) in items]
    logger.info("Reading metadata for %d sample files...", len(all_paths))

    all_tags = (IMAGE_LOCAL_TAGS + VIDEO_UTC_TAGS + VIDEO_TZ_TAGS
                + FILESYSTEM_TAGS + TZ_HINT_TAGS)
    metadata_list = get_metadata_for_tags(all_paths, all_tags)
    metadata_by_path = {m.get('SourceFile', '').replace('/', os.sep): m for m in metadata_list}

    folder_verdicts = defaultdict(lambda: {"types_ok": [], "types_bad": [], "examples": []})
    for (year, ext), items in sorted(samples_by_year_ext.items()):
        is_video_ext = ext in VIDEO_EXTS
        is_image_ext = ext in IMAGE_EXTS
        if not (is_video_ext or is_image_ext):
            continue

        all_match = True
        first_mismatch_example = None
        sampled_count = 0
        for filename, full_path in items:
            expected = parse_filename_datetime(filename)
            if expected is None:
                continue
            sampled_count += 1
            md = metadata_by_path.get(full_path, {})
            checks = check_file(filename, md, expected, is_video_ext)
            mismatches = [c for c in checks if not c[3]]
            if mismatches:
                all_match = False
                if first_mismatch_example is None:
                    first_mismatch_example = (filename, mismatches)

        if sampled_count == 0:
            continue

        label = f".{ext} ({sampled_count})"
        if all_match:
            folder_verdicts[year]["types_ok"].append(label)
        else:
            folder_verdicts[year]["types_bad"].append(label)
            if first_mismatch_example:
                folder_verdicts[year]["examples"].append(
                    (ext, first_mismatch_example[0], first_mismatch_example[1])
                )

    logger.info("")
    logger.info("=" * 72)
    logger.info("PER-FOLDER VERDICT")
    logger.info("=" * 72)
    needs_fix = []
    clean = []
    for year in year_folders:
        v = folder_verdicts.get(year)
        if not v:
            continue
        if v["types_bad"]:
            needs_fix.append(year)
            logger.warning("%s  [NEEDS FIX]", year)
            logger.warning("  Clean: %s", ', '.join(v['types_ok']) or '(none)')
            logger.warning("  Bad:   %s", ', '.join(v['types_bad']))
            for ext, ex_name, mismatches in v["examples"][:1]:
                logger.warning("  Example .%s: %s", ext, ex_name)
                for tag, expected, actual, _ok in mismatches[:6]:
                    logger.warning("    %s: expected %r, got %r", tag, expected, actual)
        else:
            clean.append(year)
            logger.info("%s  [OK]  (%s)", year, ', '.join(v['types_ok']))

    # Structural checks (every-file, not sampled): extension/content mismatches,
    # files in the wrong year folder, names that don't match the canonical pattern.
    ext_mismatches = check_extension_mismatches(year_folders, master_root)
    year_mismatches = check_year_folder_mismatches(year_folders, master_root)
    non_canonical = check_non_canonical_filenames(year_folders, master_root)

    _report_structural_findings(
        "EXTENSION / CONTENT MISMATCHES",
        ext_mismatches,
        lambda x: f"{x[0]}/{x[1]}  (named .{x[2]}, actually .{x[3]})",
    )
    _report_structural_findings(
        "WRONG-YEAR-FOLDER FILES",
        year_mismatches,
        lambda x: f"{x[0]}/{x[1]}  (filename year {x[2]}, folder {x[0]})",
    )
    _report_structural_findings(
        "NON-CANONICAL FILENAMES",
        non_canonical,
        lambda x: f"{x[0]}/{x[1]}",
    )

    logger.info("")
    logger.info("=" * 72)
    logger.info("SUMMARY")
    logger.info("=" * 72)
    logger.info("Folders to run write_exif_from_filename.py on: %d", len(needs_fix))
    for y in needs_fix:
        logger.info("  - %s", y)
    logger.info("Folders already correct: %d", len(clean))
    for y in clean:
        logger.info("  - %s", y)
    logger.info("")
    logger.info("Structural issues (zero is the goal):")
    logger.info("  Extension / content mismatches: %d", len(ext_mismatches))
    logger.info("  Wrong-year-folder files:        %d", len(year_mismatches))
    logger.info("  Non-canonical filenames:        %d", len(non_canonical))


if __name__ == "__main__":
    configure_logging("audit_master")
    main()
