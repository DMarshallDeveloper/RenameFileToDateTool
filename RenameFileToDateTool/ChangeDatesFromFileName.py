"""ChangeDatesFromFileName.py — bulk EXIF-from-filename rewriter (recursive).

This is the more aggressive version of ``main.py``'s mode 1. The difference:

  - It RECURSES into subfolders, so you can point it at the whole master library
    (``D:\\Files\\Pictures and Videos\\``) and it'll fix every year folder in one pass.
  - It SKIPS files whose names don't look like dates — no prompt to choose mode.

When to use which:
  - ``main.py`` mode 1 → one folder, with the interactive 0/1 prompt
  - ``ChangeDatesFromFileName.py`` → unattended recursive sweep of the master library

Both share the same writer logic (``photo_lib.exiftool_runner.write_exif_dates_batch``)
and the same TZ detection (``photo_lib.timezone_detection.detect_file_tz``), so they
produce identical results on identical inputs.

Run with ``python ChangeDatesFromFileName.py``.
"""

import argparse
import logging
import os

from photo_lib.exiftool_runner import get_all_metadata, is_metadata_in_sync, write_exif_dates_batch
from photo_lib.extensions import is_image, is_video, MEDIA_EXTENSIONS, normalize_extension
from photo_lib.filename_pattern import (
    apply_placeholder_time_bump,
    maybe_rename_placeholder,
    parse_filename_datetime,
)
from photo_lib.logging_setup import configure_logging
from photo_lib.tag_modes import IMAGE_TAG_MODES, VIDEO_TAG_MODES
from photo_lib.timezone_detection import LOCAL_TIMEZONE, detect_file_tz
from photo_lib.tk_picker import choose_directory, resolve_directory

logger = logging.getLogger("photo_lib")


# Back-compat alias: extract_date_from_filename used to live here; tests still import it.
def extract_date_from_filename(filename):
    """Pull a date+time from the filename. Accepts both ``YYYY-MM-DD HH.MM.SS``
    (main.py format) and ``YYYY-MM-DD_HH-MM-SS`` (older takeout format)."""
    return parse_filename_datetime(filename)


def change_exif_date(directory: str, dry_run: bool = False):
    """Recursively rewrite EXIF/QuickTime dates to match each filename.

    With ``dry_run=True``, prints a summary of the planned writes per kind
    (image/video) without invoking exiftool.
    """
    if not directory:
        logger.info("No directory selected. Exiting.")
        return

    # Collect all candidate files first so we can fetch their existing EXIF in one batch
    candidates = []  # list of (file_path, filename, parsed_date_time, ext)
    for root, _, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            date_time = parse_filename_datetime(file)
            if not date_time:
                continue
            ext = normalize_extension(os.path.splitext(file)[1])
            if ext not in MEDIA_EXTENSIONS:
                continue
            candidates.append((file_path, file, date_time, ext))

    file_paths = [c[0] for c in candidates]
    logger.info("Reading existing EXIF for %d files...", len(file_paths))
    metadata_by_name = get_all_metadata(file_paths)

    image_file_date_map = {}
    video_file_date_map = {}
    skipped_in_sync = 0
    prefix = "[DRY-RUN] " if dry_run else ""

    for file_path, filename, date_time, ext in candidates:
        md = metadata_by_name.get(filename, {})
        bumped = apply_placeholder_time_bump(filename, date_time)

        # If the bump moved the time, also rename so filename ≡ EXIF.
        if bumped != date_time:
            new_path = maybe_rename_placeholder(file_path, dry_run=dry_run)
            if new_path is None:
                logger.warning(
                    "Cannot rename placeholder %s (target exists). Skipping its EXIF "
                    "write to preserve the filename ≡ EXIF invariant.", filename
                )
                continue
            if new_path != file_path:
                logger.info("%s[RENAME] %s -> %s",
                            prefix, os.path.relpath(file_path),
                            os.path.relpath(new_path))
                file_path = new_path
        date_time = bumped
        file_tz = detect_file_tz(md, default_tz=LOCAL_TIMEZONE)

        if is_image(ext):
            tag_modes = IMAGE_TAG_MODES
        elif is_video(ext):
            tag_modes = VIDEO_TAG_MODES
        else:
            continue

        if is_metadata_in_sync(md, date_time, file_tz, tag_modes):
            skipped_in_sync += 1
            continue

        if is_image(ext):
            image_file_date_map[file_path] = (date_time, file_tz)
        else:
            video_file_date_map[file_path] = (date_time, file_tz)

    if dry_run:
        _preview_exif_writes(image_file_date_map, "image")
        _preview_exif_writes(video_file_date_map, "video")
    else:
        if image_file_date_map:
            logger.info("Writing metadata for %d image files...", len(image_file_date_map))
            write_exif_dates_batch(image_file_date_map, IMAGE_TAG_MODES)

        if video_file_date_map:
            logger.info("Writing metadata for %d video files...", len(video_file_date_map))
            write_exif_dates_batch(video_file_date_map, VIDEO_TAG_MODES)

    total = len(image_file_date_map) + len(video_file_date_map)
    verb = "would be updated" if dry_run else "have been updated"
    logger.info("%s%d files %s.", prefix, total, verb)
    if skipped_in_sync:
        logger.info("%s%d files already in sync, skipped.", prefix, skipped_in_sync)


def _preview_exif_writes(date_map, kind):
    """Log the planned EXIF writes for dry-run mode. Shows the first 10 entries
    and a count of any remainder."""
    if not date_map:
        return
    logger.info("[DRY-RUN] Would write metadata for %d %s files:", len(date_map), kind)
    for i, (path, (dt, file_tz)) in enumerate(date_map.items()):
        if i >= 10:
            logger.info("  ... and %d more", len(date_map) - 10)
            break
        logger.info("  %s: dt=%s tz=%s", os.path.relpath(path), dt.isoformat(), file_tz)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Recursively rewrite EXIF/QuickTime dates from filenames."
    )
    parser.add_argument(
        "--path",
        help="Directory to operate on (recursively). If omitted, opens the Tk folder picker."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the planned EXIF writes without invoking exiftool."
    )
    args = parser.parse_args()

    configure_logging("change_dates_from_filename")
    directory = resolve_directory(args.path, "Select Photos Directory")
    change_exif_date(directory, dry_run=args.dry_run)
