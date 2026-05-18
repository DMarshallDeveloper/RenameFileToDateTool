"""main.py — interactive entry point for the rename-and-tag workflow.

This is the script you run when you want to either:
  (0) Rename every file in a folder to a date-stamped name like
      ``2026-04-09 19.52.51_1.jpg``, where the date is pulled from the file's
      embedded metadata (EXIF for images, QuickTime for videos), OR
  (1) Do the opposite: leave filenames alone, but rewrite the embedded metadata
      so it matches whatever the filename already says. Useful when files have
      been correctly named but their EXIF dates got lost or corrupted (e.g. after
      a Google Takeout round-trip).

Run it with ``python main.py``. A folder picker dialog opens; you select the
folder, then type 0 or 1 at the prompt.

Why timezone matters here: video files (mov/mp4) store their dates as UTC by spec,
but image files (jpg/heic) store local time. If you just ran rename_photos on a
video without knowing the user's timezone, the filename would be off by 12 hours
(NZ is UTC+12/+13). And if the photo was taken overseas, the *photo's* local
timezone might not even be NZ. The helpers in ``photo_lib.timezone_detection``
handle this by reading the actual TZ offset from the file's metadata when present.

Where to read next:
  - ``photo_lib/exiftool_runner.py`` for the batched exiftool invocations
  - ``photo_lib/timezone_detection.py`` for the per-file TZ-detection priority order
  - ``photo_lib/tag_modes.py`` for *which* EXIF/QuickTime tags get written and how
  - ``ChangeDatesFromFileName.py`` for a simpler recursive variant of mode 1
  - ``audit_master.py`` for the diagnostic that tells you which folders need re-running
"""

import argparse
import os
from datetime import datetime

from photo_lib.binaries import EXIFTOOL  # re-exported for back-compat below
from photo_lib.exiftool_runner import get_all_metadata, write_exif_dates_batch
from photo_lib.extensions import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    is_image,
    is_video,
    normalize_extension,
)
from photo_lib.filename_pattern import (
    PLACEHOLDER_FILENAME_RE,
    apply_placeholder_time_bump,
    parse_filename_datetime,
)
from photo_lib.tag_modes import (
    IMAGE_TAG_MODES,
    VIDEO_TAG_MODES,
    format_date_for_mode,
)
from photo_lib.timezone_detection import (
    EXIF_DATE_RE,
    LOCAL_TIMEZONE,
    TZ_OFFSET_RE,
    detect_file_tz,
    parse_exif_datetime,
    parse_tz_offset,
)
from photo_lib.tk_picker import choose_directory, resolve_directory

# Back-compat aliases — older tests and external callers reach for these names.
EXE = EXIFTOOL
IMAGE_FILE_EXTENSIONS = IMAGE_EXTENSIONS
VIDEO_FILE_EXTENSIONS = VIDEO_EXTENSIONS
# Read-priority for ``extract_best_date`` — order matters (DateTimeOriginal first).
IMAGE_FILE_DATE_ATTRIBUTES = ["DateTimeOriginal", "CreateDate", "DateCreated", "ModifyDate"]
VIDEO_FILE_DATE_ATTRIBUTES = ["MediaCreateDate", "MediaModifyDate", "TrackCreateDate",
                              "TrackModifyDate", "CreateDate", "ModifyDate"]


def extract_best_date(file_metadata, file_path):
    """Pick the best date for a file by walking a priority list of EXIF/QuickTime tags.

    ``file_metadata`` is a dict (one entry per tag) as produced by exiftool's JSON
    output. We try ``DateTimeOriginal`` first, then ``CreateDate``, and so on; the
    first tag that parses cleanly wins. If none parse, we fall back to the file's
    last-modified timestamp on disk.

    For images we read the tag as-is (image EXIF dates are local time by convention).
    For videos we convert the stored UTC value back to local time using the photo's
    own TZ offset if it has one — so a video shot in Melbourne (+10:00) renames
    to its Melbourne local time, not NZ time.
    """
    ext = normalize_extension(file_metadata.get("FileTypeExtension", ""))

    if is_image(ext):
        attribute_list = IMAGE_FILE_DATE_ATTRIBUTES
        treat_naive_as_utc = False
        target_tz = LOCAL_TIMEZONE  # images are already naive local — TZ doesn't matter here
    elif is_video(ext):
        attribute_list = VIDEO_FILE_DATE_ATTRIBUTES
        treat_naive_as_utc = True
        target_tz = detect_file_tz(file_metadata, default_tz=LOCAL_TIMEZONE)
    else:
        return None

    for attribute in attribute_list:
        date_time_string = file_metadata.get(attribute)
        if date_time_string:
            dt = parse_exif_datetime(
                date_time_string,
                treat_naive_as_utc=treat_naive_as_utc,
                target_tz=target_tz,
            )
            if dt:
                return dt

    try:
        return datetime.fromtimestamp(os.stat(file_path).st_mtime)
    except Exception:
        return None


def rename_photos(directory, dry_run: bool = False):
    """Mode 0: rename every file in ``directory`` to ``YYYY-MM-DD HH.MM.SS_N.ext``.

    The date comes from the file's embedded metadata (see ``extract_best_date``).
    Files that share a timestamp get unique ``_1``, ``_2`` suffixes. Re-running on
    an already-renamed folder is a no-op — files keep their existing names.

    With ``dry_run=True``, prints the planned renames without touching disk.
    """
    if not directory:
        print("No directory selected. Exiting.")
        return

    files = sorted(f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f)))
    if not files:
        print("No files found.")
        return

    # Read all metadata in one exiftool call rather than spawning N subprocesses.
    file_paths = [os.path.join(directory, f) for f in files]
    metadata_by_name = get_all_metadata(file_paths)

    # Seed the ``_N`` counter from existing filenames so re-runs on a partially-renamed
    # folder don't collide. ``existing_names`` tracks names already on disk OR planned
    # in this run, both used to find the next available ``_N``.
    existing_names = set(os.listdir(directory))
    files_renamed_count = 0
    prefix = "[DRY-RUN] " if dry_run else ""

    for file in files:
        file_path = os.path.join(directory, file)
        file_metadata = metadata_by_name.get(file, {})
        date_time = extract_best_date(file_metadata, file_path)

        if not date_time:
            print(f"Unable to extract date from {file}. Skipping.")
            continue

        new_file_name_base = date_time.strftime('%Y-%m-%d %H.%M.%S')
        new_ext = normalize_extension(
            file_metadata.get('FileTypeExtension', os.path.splitext(file)[1])
        )

        counter = 1
        while True:
            new_file_name = f"{new_file_name_base}_{counter}.{new_ext}"
            if new_file_name == file:
                existing_names.add(new_file_name)
                break
            if new_file_name not in existing_names:
                if dry_run:
                    print(f"{prefix}{file} -> {new_file_name}")
                else:
                    new_path = os.path.join(directory, new_file_name)
                    os.rename(file_path, new_path)
                existing_names.discard(file)
                existing_names.add(new_file_name)
                files_renamed_count += 1
                break
            counter += 1

        if files_renamed_count and files_renamed_count % 50 == 0:
            print('Files renamed:', files_renamed_count)

    verb = "would be renamed" if dry_run else "have been renamed"
    print(f"{prefix}{files_renamed_count} files {verb}.")


def change_exif_date(directory: str, dry_run: bool = False):
    """Mode 1: read each file's date from its filename, write it back into the
    file's EXIF/QuickTime metadata.

    Use this when filenames are correct but metadata is broken (or stripped). Skips
    any file whose filename doesn't parse as a date. Writes a separate batch for
    images vs videos because the two need different tag-mode rules (see
    ``photo_lib.tag_modes``).

    With ``dry_run=True``, summarizes the planned writes without invoking exiftool.
    """
    if not directory:
        print("No directory selected. Exiting.")
        return

    files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
    file_paths = [os.path.join(directory, f) for f in files]
    metadata_by_name = get_all_metadata(file_paths)

    image_file_date_map = {}
    video_file_date_map = {}
    prefix = "[DRY-RUN] " if dry_run else ""

    # Runtime logs live in a sibling logs/ folder (gitignored) so they don't clutter
    # the script directory or get committed.
    logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, "error_log.txt")
    with open(log_path, 'w', encoding='utf-8') as error_log:
        for file in files:
            file_path = os.path.join(directory, file)
            file_metadata = metadata_by_name.get(file, {})

            date_time = parse_filename_datetime(file)
            if date_time is None:
                print(f"Error parsing datetime from filename: {file}. Skipping.")
                continue

            # Bump placeholder Jan-1-midnight dates to 1pm to avoid Dec-31-previous-year
            # rollover in UTC-respecting viewers.
            date_time = apply_placeholder_time_bump(file, date_time)
            file_tz = detect_file_tz(file_metadata, default_tz=LOCAL_TIMEZONE)

            ext = normalize_extension(file_metadata.get("FileTypeExtension", ""))
            if is_image(ext):
                image_file_date_map[file_path] = (date_time, file_tz)
            elif is_video(ext):
                video_file_date_map[file_path] = (date_time, file_tz)
            else:
                print(f"Invalid file type: {file}. Skipping.")

        if dry_run:
            _preview_exif_writes(image_file_date_map, "image")
            _preview_exif_writes(video_file_date_map, "video")
        else:
            if image_file_date_map:
                print(f"Writing metadata for {len(image_file_date_map)} image files...")
                write_exif_dates_batch(image_file_date_map, IMAGE_TAG_MODES, error_log)

            if video_file_date_map:
                print(f"Writing metadata for {len(video_file_date_map)} video files...")
                write_exif_dates_batch(video_file_date_map, VIDEO_TAG_MODES, error_log)

    total = len(image_file_date_map) + len(video_file_date_map)
    verb = "would be updated" if dry_run else "have been updated"
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
        print(f"  {os.path.basename(path)}: dt={dt.isoformat()} tz={file_tz}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Rename files from EXIF dates (mode 0) or write EXIF from filenames (mode 1)."
    )
    parser.add_argument(
        "--path",
        help="Directory to operate on. If omitted, opens the Tk folder picker."
    )
    parser.add_argument(
        "--mode", choices=["0", "1"],
        help="0 = rename from EXIF, 1 = write EXIF from filename. "
             "If omitted, prompts interactively."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the planned renames or EXIF writes without modifying anything."
    )
    args = parser.parse_args()

    directory = resolve_directory(args.path, "Select Photos Directory")
    if args.mode is not None:
        editing_exif_not_name = args.mode
    else:
        editing_exif_not_name = input(
            "Are you renaming files from date metadata (0) "
            "or writing metadata from filename (1): "
        )

    if editing_exif_not_name == '0':
        rename_photos(directory, dry_run=args.dry_run)
    else:
        change_exif_date(directory, dry_run=args.dry_run)
