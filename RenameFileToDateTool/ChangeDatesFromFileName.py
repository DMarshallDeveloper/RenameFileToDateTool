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
import os

from photo_lib.exiftool_runner import get_all_metadata, write_exif_dates_batch
from photo_lib.extensions import is_image, is_video, MEDIA_EXTENSIONS, normalize_extension
from photo_lib.filename_pattern import (
    apply_placeholder_time_bump,
    parse_filename_datetime,
)
from photo_lib.tag_modes import IMAGE_TAG_MODES, VIDEO_TAG_MODES
from photo_lib.timezone_detection import LOCAL_TIMEZONE, detect_file_tz
from photo_lib.tk_picker import choose_directory


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
        print("No directory selected. Exiting.")
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
    print(f"Reading existing EXIF for {len(file_paths)} files...")
    metadata_by_name = get_all_metadata(file_paths)

    image_file_date_map = {}
    video_file_date_map = {}
    prefix = "[DRY-RUN] " if dry_run else ""

    # Runtime logs live in a sibling logs/ folder (gitignored).
    logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, "logging_file.txt")
    with open(log_path, 'w', encoding='utf-8') as logging_file:
        for file_path, filename, date_time, ext in candidates:
            md = metadata_by_name.get(filename, {})
            date_time = apply_placeholder_time_bump(filename, date_time)
            file_tz = detect_file_tz(md, default_tz=LOCAL_TIMEZONE)

            if is_image(ext):
                image_file_date_map[file_path] = (date_time, file_tz)
            elif is_video(ext):
                video_file_date_map[file_path] = (date_time, file_tz)

        if dry_run:
            _preview_exif_writes(image_file_date_map, "image")
            _preview_exif_writes(video_file_date_map, "video")
        else:
            if image_file_date_map:
                print(f"Writing metadata for {len(image_file_date_map)} image files...")
                write_exif_dates_batch(image_file_date_map, IMAGE_TAG_MODES, logging_file)

            if video_file_date_map:
                print(f"Writing metadata for {len(video_file_date_map)} video files...")
                write_exif_dates_batch(video_file_date_map, VIDEO_TAG_MODES, logging_file)

        total = len(image_file_date_map) + len(video_file_date_map)
        verb = "would be updated" if dry_run else "have been updated"
        if not dry_run:
            logging_file.write(f"{total} files have been updated.\n")
        print(f"{prefix}{total} files {verb}.")


def _preview_exif_writes(date_map, kind):
    """Print the planned EXIF writes for dry-run mode. Shows the first 10 entries
    and a count of any remainder."""
    if not date_map:
        return
    print(f"[DRY-RUN] Would write metadata for {len(date_map)} {kind} files:")
    for i, (path, (dt, file_tz)) in enumerate(date_map.items()):
        if i >= 10:
            print(f"  ... and {len(date_map) - 10} more")
            break
        print(f"  {os.path.relpath(path)}: dt={dt.isoformat()} tz={file_tz}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Recursively rewrite EXIF/QuickTime dates from filenames."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the planned EXIF writes without invoking exiftool."
    )
    args = parser.parse_args()

    directory = choose_directory("Select Photos Directory")
    change_exif_date(directory, dry_run=args.dry_run)
