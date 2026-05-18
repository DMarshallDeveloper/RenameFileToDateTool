"""rename_files_from_exif.py — rename files in a folder to canonical names
based on their embedded EXIF / QuickTime dates.

This is one of the two workhorses for keeping the master library tidy. It
takes a folder, asks exiftool for each file's capture date, and renames the
file to ``YYYY-MM-DD HH.MM.SS_N.ext``. Re-running on an already-renamed folder
is a no-op — files keep their existing names.

The companion script ``write_exif_from_filename.py`` does the opposite: leaves
filenames alone but rewrites the embedded metadata to match.

Run with::

    python rename_files_from_exif.py --path <folder>
    python rename_files_from_exif.py --path <folder> --dry-run

If ``--path`` is omitted, a Tk folder picker opens.

Why timezone matters: video files (mov/mp4) store their dates as UTC by spec,
but image files (jpg/heic) store local time. Renaming a video without knowing
its timezone would land the filename 12 hours off (NZ is UTC+12/+13). And if
the video was shot overseas, the photo's local timezone might not even be NZ.
``photo_lib.timezone_detection`` handles this by reading the actual TZ offset
from the file's metadata when present.

Where to read next:
  - ``photo_lib/exiftool_runner.py`` for the batched exiftool invocations
  - ``photo_lib/timezone_detection.py`` for the per-file TZ-detection priority
  - ``audit_master.py`` for the diagnostic that tells you when EXIF and
    filename have drifted apart
"""

import argparse
import logging
import os
from datetime import datetime

from photo_lib.binaries import EXIFTOOL  # re-exported for back-compat below
from photo_lib.exiftool_runner import get_all_metadata
from photo_lib.extensions import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    is_image,
    is_video,
    normalize_extension,
)
from photo_lib.logging_setup import configure_logging
from photo_lib.timezone_detection import (
    LOCAL_TIMEZONE,
    detect_file_tz,
    parse_exif_datetime,
)
from photo_lib.tk_picker import resolve_directory

logger = logging.getLogger("photo_lib")

# Back-compat aliases — older tests reach for these names.
EXE = EXIFTOOL
IMAGE_FILE_EXTENSIONS = IMAGE_EXTENSIONS
VIDEO_FILE_EXTENSIONS = VIDEO_EXTENSIONS

# Read-priority order for ``extract_best_date`` — DateTimeOriginal is the
# capture-time tag the camera itself writes, so it wins. The others are
# fallbacks for files that have been through various tools that may have
# overwritten DateTimeOriginal.
IMAGE_FILE_DATE_ATTRIBUTES = ["DateTimeOriginal", "CreateDate", "DateCreated", "ModifyDate"]
VIDEO_FILE_DATE_ATTRIBUTES = ["MediaCreateDate", "MediaModifyDate", "TrackCreateDate",
                              "TrackModifyDate", "CreateDate", "ModifyDate"]


def extract_best_date(file_metadata, file_path):
    """Pick the best date for a file by walking a priority list of EXIF/QuickTime tags.

    ``file_metadata`` is a dict (one entry per tag) as produced by exiftool's
    JSON output. The function tries ``DateTimeOriginal`` first, then
    ``CreateDate``, and so on; the first tag that parses cleanly wins. If none
    parse, falls back to the file's last-modified timestamp on disk.

    For images, the tag is read as-is (image EXIF dates are local time by
    convention). For videos, the stored UTC value is converted back to local
    time using the photo's own TZ offset if it has one — so a video shot in
    Melbourne (+10:00) renames to its Melbourne local time, not NZ time.
    """
    file_extension = normalize_extension(file_metadata.get("FileTypeExtension", ""))

    if is_image(file_extension):
        attribute_list = IMAGE_FILE_DATE_ATTRIBUTES
        treat_naive_as_utc = False
        target_timezone = LOCAL_TIMEZONE  # images are already naive local — TZ doesn't matter here
    elif is_video(file_extension):
        attribute_list = VIDEO_FILE_DATE_ATTRIBUTES
        treat_naive_as_utc = True
        target_timezone = detect_file_tz(file_metadata, default_tz=LOCAL_TIMEZONE)
    else:
        return None

    for tag_name in attribute_list:
        date_time_string = file_metadata.get(tag_name)
        if date_time_string:
            parsed_date_time = parse_exif_datetime(
                date_time_string,
                treat_naive_as_utc=treat_naive_as_utc,
                target_tz=target_timezone,
            )
            if parsed_date_time:
                return parsed_date_time

    try:
        return datetime.fromtimestamp(os.stat(file_path).st_mtime)
    except Exception:
        return None


def rename_photos(directory, dry_run: bool = False):
    """Rename every file in ``directory`` to ``YYYY-MM-DD HH.MM.SS_N.ext``.

    The date comes from the file's embedded metadata (see ``extract_best_date``).
    Files that share a timestamp get unique ``_1``, ``_2`` suffixes. Re-running
    on an already-renamed folder is a no-op — files keep their existing names.

    With ``dry_run=True``, prints the planned renames without touching disk.
    """
    if not directory:
        logger.info("No directory selected. Exiting.")
        return

    filenames = sorted(
        f for f in os.listdir(directory)
        if os.path.isfile(os.path.join(directory, f))
    )
    if not filenames:
        logger.info("No files found.")
        return

    # Read all metadata in one exiftool call rather than spawning N subprocesses.
    file_paths = [os.path.join(directory, f) for f in filenames]
    metadata_by_filename = get_all_metadata(file_paths)

    # Seed the ``_N`` counter from existing filenames so re-runs on a partially-
    # renamed folder don't collide. ``existing_filenames`` tracks names already
    # on disk OR planned in this run.
    existing_filenames = set(os.listdir(directory))
    files_renamed_count = 0
    log_prefix = "[DRY-RUN] " if dry_run else ""

    for filename in filenames:
        file_path = os.path.join(directory, filename)
        file_metadata = metadata_by_filename.get(filename, {})
        capture_date_time = extract_best_date(file_metadata, file_path)

        if not capture_date_time:
            logger.warning("Unable to extract date from %s. Skipping.", filename)
            continue

        new_filename_base = capture_date_time.strftime('%Y-%m-%d %H.%M.%S')
        new_file_extension = normalize_extension(
            file_metadata.get('FileTypeExtension', os.path.splitext(filename)[1])
        )

        counter_suffix = 1
        while True:
            candidate_filename = f"{new_filename_base}_{counter_suffix}.{new_file_extension}"
            if candidate_filename == filename:
                existing_filenames.add(candidate_filename)
                break
            if candidate_filename not in existing_filenames:
                if dry_run:
                    logger.info("%s%s -> %s", log_prefix, filename, candidate_filename)
                else:
                    new_path = os.path.join(directory, candidate_filename)
                    os.rename(file_path, new_path)
                existing_filenames.discard(filename)
                existing_filenames.add(candidate_filename)
                files_renamed_count += 1
                break
            counter_suffix += 1

        if files_renamed_count and files_renamed_count % 50 == 0:
            logger.info("Files renamed: %d", files_renamed_count)

    verb = "would be renamed" if dry_run else "have been renamed"
    logger.info("%s%d files %s.", log_prefix, files_renamed_count, verb)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Rename files in a folder to canonical YYYY-MM-DD HH.MM.SS_N.ext "
                    "based on their EXIF / QuickTime capture date."
    )
    parser.add_argument(
        "--path",
        help="Directory to operate on. If omitted, opens the Tk folder picker.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the planned renames without touching disk.",
    )
    cli_arguments = parser.parse_args()

    configure_logging("rename_files_from_exif")
    target_directory = resolve_directory(cli_arguments.path, "Select Photos Directory")
    rename_photos(target_directory, dry_run=cli_arguments.dry_run)
