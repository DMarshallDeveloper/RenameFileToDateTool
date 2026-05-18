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

from photo_lib.exif_writer import write_exif_for_files
from photo_lib.extensions import MEDIA_EXTENSIONS, normalize_extension
from photo_lib.filename_pattern import parse_filename_datetime
from photo_lib.logging_setup import configure_logging
from photo_lib.tk_picker import choose_directory, resolve_directory

logger = logging.getLogger("photo_lib")


# Back-compat alias: extract_date_from_filename used to live here; tests still import it.
def extract_date_from_filename(filename):
    """Pull a date+time from the filename. Accepts both ``YYYY-MM-DD HH.MM.SS``
    (main.py format) and ``YYYY-MM-DD_HH-MM-SS`` (older takeout format)."""
    return parse_filename_datetime(filename)


def change_exif_date(directory: str, dry_run: bool = False):
    """Recursively rewrite EXIF/QuickTime dates to match each filename.

    Pre-filters to "looks like a media file with a parseable date in the name"
    so the helper isn't spammed with skip warnings for every random file in a
    big tree. Then delegates the actual work to
    ``photo_lib.exif_writer.write_exif_for_files``.

    With ``dry_run=True``, prints a summary of the planned writes per kind
    (image/video) without invoking exiftool.
    """
    if not directory:
        logger.info("No directory selected. Exiting.")
        return

    candidate_file_paths = []
    for current_dir, _subdirs, filenames in os.walk(directory):
        for filename in filenames:
            if parse_filename_datetime(filename) is None:
                continue
            file_extension = normalize_extension(os.path.splitext(filename)[1])
            if file_extension not in MEDIA_EXTENSIONS:
                continue
            candidate_file_paths.append(os.path.join(current_dir, filename))

    logger.info("Reading existing EXIF for %d files...", len(candidate_file_paths))
    write_exif_for_files(
        candidate_file_paths,
        dry_run=dry_run,
        path_for_log=os.path.relpath,
    )


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
