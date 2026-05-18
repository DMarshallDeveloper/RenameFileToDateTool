"""Shared EXIF-from-filename writer used by ``write_exif_from_filename.py`` Mode 1 and
``ChangeDatesFromFileName.py``.

Both callers do the same thing — for each candidate file, parse the date out of
the filename, apply the placeholder bump (renaming the file if it kicks in),
detect the per-file timezone, check whether the EXIF is already in sync, and
batch-write what isn't. They only differ in how they collect the candidate
files (one folder vs. recursive walk).

This module factors the post-collection pipeline into a single function so the
two scripts can't drift. Callers pass a list of file paths; the helper handles
the rest.
"""

import logging
import os

from photo_lib.exiftool_runner import (
    get_all_metadata,
    is_metadata_in_sync,
    write_exif_dates_batch,
)
from photo_lib.extensions import is_image, is_video, normalize_extension
from photo_lib.filename_pattern import (
    apply_placeholder_time_bump,
    maybe_rename_placeholder,
    parse_filename_datetime,
)
from photo_lib.tag_modes import IMAGE_TAG_MODES, VIDEO_TAG_MODES
from photo_lib.timezone_detection import LOCAL_TIMEZONE, detect_file_tz

logger = logging.getLogger("photo_lib")


def write_exif_for_files(file_paths, dry_run: bool = False, path_for_log=None) -> dict:
    """For each path in ``file_paths``, parse its filename, apply the placeholder
    bump (and matching rename if it kicks in), detect the per-file timezone,
    check whether the EXIF is already in sync, and batch-write what isn't.

    ``path_for_log``: callable mapping a full path to a display string for log
    lines. Defaults to ``os.path.basename`` (cleaner output for single-folder
    callers like ``write_exif_from_filename.py``). Pass ``os.path.relpath`` for recursive callers
    that need to disambiguate same-named files in different folders.

    Files whose filename doesn't parse as a date, or whose extension isn't
    image/video, are skipped (logged at warning level). Files whose EXIF is
    already in sync are also skipped (counted but not logged individually).

    Returns ``{'written': N, 'skipped_in_sync': M}``.
    """
    if path_for_log is None:
        path_for_log = os.path.basename

    metadata_by_filename = get_all_metadata(file_paths)

    image_date_map = {}    # full_path -> (datetime, tzinfo) for images that need writes
    video_date_map = {}    # full_path -> (datetime, tzinfo) for videos that need writes
    files_skipped_already_in_sync = 0
    log_prefix = "[DRY-RUN] " if dry_run else ""

    for file_path in file_paths:
        filename = os.path.basename(file_path)
        file_metadata = metadata_by_filename.get(filename, {})

        date_time_from_filename = parse_filename_datetime(filename)
        if date_time_from_filename is None:
            logger.warning("Error parsing datetime from filename: %s. Skipping.", filename)
            continue

        # Bump placeholder Jan-1-midnight dates to 1pm to avoid Dec-31-previous-year
        # rollover in UTC-respecting viewers. If the bump moved the time, also
        # rename the file on disk so the filename ≡ EXIF invariant holds.
        bumped_date_time = apply_placeholder_time_bump(filename, date_time_from_filename)
        if bumped_date_time != date_time_from_filename:
            renamed_path = maybe_rename_placeholder(file_path, dry_run=dry_run)
            if renamed_path is None:
                logger.warning(
                    "Cannot rename placeholder %s (target exists). Skipping its EXIF "
                    "write to preserve the filename ≡ EXIF invariant.", filename
                )
                continue
            if renamed_path != file_path:
                logger.info("%s[RENAME] %s -> %s",
                            log_prefix,
                            path_for_log(file_path),
                            path_for_log(renamed_path))
                file_path = renamed_path
                filename = os.path.basename(file_path)
        target_date_time = bumped_date_time

        file_timezone = detect_file_tz(file_metadata, default_tz=LOCAL_TIMEZONE)
        file_extension = normalize_extension(
            file_metadata.get("FileTypeExtension", os.path.splitext(filename)[1])
        )

        if is_image(file_extension):
            tag_modes_for_file = IMAGE_TAG_MODES
        elif is_video(file_extension):
            tag_modes_for_file = VIDEO_TAG_MODES
        else:
            logger.warning("Invalid file type: %s. Skipping.", filename)
            continue

        if is_metadata_in_sync(file_metadata, target_date_time, file_timezone, tag_modes_for_file):
            files_skipped_already_in_sync += 1
            continue

        if is_image(file_extension):
            image_date_map[file_path] = (target_date_time, file_timezone)
        else:
            video_date_map[file_path] = (target_date_time, file_timezone)

    if dry_run:
        _log_dry_run_preview(image_date_map, "image", path_for_log)
        _log_dry_run_preview(video_date_map, "video", path_for_log)
    else:
        if image_date_map:
            logger.info("Writing metadata for %d image files...", len(image_date_map))
            write_exif_dates_batch(image_date_map, IMAGE_TAG_MODES)
        if video_date_map:
            logger.info("Writing metadata for %d video files...", len(video_date_map))
            write_exif_dates_batch(video_date_map, VIDEO_TAG_MODES)

    total_files_written = len(image_date_map) + len(video_date_map)
    verb = "would be updated" if dry_run else "have been updated"
    logger.info("%s%d files %s.", log_prefix, total_files_written, verb)
    if files_skipped_already_in_sync:
        logger.info("%s%d files already in sync, skipped.",
                    log_prefix, files_skipped_already_in_sync)

    return {
        "written": total_files_written,
        "skipped_in_sync": files_skipped_already_in_sync,
    }


def _log_dry_run_preview(date_map, kind, path_for_log) -> None:
    """First 10 planned writes + a remainder count — keeps the dry-run output
    bounded when sweeping the whole master library."""
    if not date_map:
        return
    logger.info("[DRY-RUN] Would write metadata for %d %s files:", len(date_map), kind)
    for index, (file_path, (date_time, file_timezone)) in enumerate(date_map.items()):
        if index >= 10:
            logger.info("  ... and %d more", len(date_map) - 10)
            break
        logger.info("  %s: dt=%s tz=%s",
                    path_for_log(file_path), date_time.isoformat(), file_timezone)
