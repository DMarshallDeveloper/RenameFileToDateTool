"""UpdateFileNameToDateFromGoogleTakeoutJSONMetadata.py — Takeout dump ingester.

Google Takeout downloads photos as a folder full of media files (jpg, mov, heic, …)
each accompanied by a ``.json`` sidecar that holds the *real* date the photo was
taken (Google strips and re-encodes EXIF on upload, so the date in the JSON is
often the only reliable source). This script:

  1. Walks the source folder and matches each ``.json`` to its media file. The
     matching is fuzzy because Takeout's filename conventions are inconsistent —
     suffixes like ``(1)`` get moved, long stems get truncated, the JSON tail can
     be ``.json`` OR ``.supplemental-metadata.json``.
  2. Reads ``photoTakenTime.timestamp`` (UNIX seconds, always UTC) from each JSON.
  3. Converts the UTC instant to *local time at the photo's GPS coordinates* using
     timezonefinder — so an overseas photo lands with its on-camera local time,
     not a NZ-shifted version. Falls back to NZ when no GPS is present.
  4. Copies the media file to the destination with a clean
     ``YYYY-MM-DD HH.MM.SS_N.ext`` name. Originals stay in the source folder.
  5. Second pass: any orphan media files (Live Photo videos without their own JSON)
     inherit the timestamp of their companion still image.

Run with ``python UpdateFileNameToDateFromGoogleTakeoutJSONMetadata.py``.
You'll be prompted to pick a source folder, a destination folder, and whether
to dry-run first.

After this finishes, the destination folder is ready to drop into the master
library's ``_Inbox`` for ingestion via ``IngestInboxToMaster.py``.
"""

import bisect
import json
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor

from photo_lib.extensions import MEDIA_EXTENSIONS as _MEDIA_EXT_SET
from photo_lib.takeout_geo import (
    DEFAULT_TIMEZONE as NEW_ZEALAND_TIMEZONE,  # back-compat alias for tests
    local_datetime_from_metadata,
    resolve_timezone_from_geo,
)
from photo_lib.tk_picker import choose_directory, resolve_directory

# The regex needs a pipe-separated string of extensions (case-insensitive matching).
MEDIA_EXTENSIONS = "|".join(sorted(_MEDIA_EXT_SET))

DUPLICATE_SUFFIX_RE = re.compile(r"(?:\((\d+)\)|_(\d+))$", re.IGNORECASE)
REAL_EXTENSION_RE = re.compile(rf"^(.*?\.({MEDIA_EXTENSIONS}))", re.IGNORECASE)


# Back-compat wrapper retained because callers used to pass a separate ``title``
# argument; the new tk_picker takes the title positionally.
def select_folder_dialog(title: str) -> str | None:
    return choose_directory(title)


def select_folder_dialog(title: str) -> str | None:
    """Open a folder-selection dialog and return the selected path (or None)."""
    root = Tk()
    root.withdraw()
    selected_folder = filedialog.askdirectory(title=title)
    if selected_folder:
        print(f"Selected folder: {selected_folder}")
    else:
        print("No folder selected.")
    return selected_folder


def generate_unique_filename(output_folder: str, base_name: str, extension: str, planned_names: set) -> str:
    """Return a filename in the master library's format: '<base_name>_<N><extension>'.

    Always includes an _N suffix starting at 1, matching main.py's convention, so every file
    in the master library has the same shape regardless of whether the timestamp is unique.
    """
    counter = 1
    while True:
        candidate_name = f"{base_name}_{counter}{extension}"
        if not os.path.exists(os.path.join(output_folder, candidate_name)) and candidate_name not in planned_names:
            planned_names.add(candidate_name)
            return candidate_name
        counter += 1


def infer_media_filename_from_json(json_filename: str) -> str | None:
    """Given a Google-Takeout JSON filename, return the media filename it most likely belongs to."""
    if not json_filename.lower().endswith(".json"):
        return None
    stem = json_filename[:-5]

    duplicate_match = DUPLICATE_SUFFIX_RE.search(stem)
    duplicate_token = duplicate_match.group(0) if duplicate_match else ""
    if duplicate_match:
        stem = stem[:duplicate_match.start()]

    base_match = REAL_EXTENSION_RE.match(stem)
    if not base_match:
        return None

    media_part = base_match.group(1)

    if duplicate_token:
        root, ext = os.path.splitext(media_part)
        media_part = f"{root}{duplicate_token}{ext}"

    print(f"Best guess at file name: {media_part}")
    return media_part


def common_prefix_length(a: str, b: str) -> int:
    a_root = os.path.splitext(a)[0].lower()
    b_root = os.path.splitext(b)[0].lower()
    return len(os.path.commonprefix([a_root, b_root]))


def find_prefix_match(prefix: str, sorted_lower: list[str], media_lower_map: dict[str, str]) -> str | None:
    """Find the first media filename whose lowercased name starts with prefix. O(log n)."""
    idx = bisect.bisect_left(sorted_lower, prefix)
    if idx < len(sorted_lower) and sorted_lower[idx].startswith(prefix):
        return media_lower_map[sorted_lower[idx]]
    return None


def find_matching_media_for_json(
    json_filename: str,
    media_lower_map: dict[str, str],
    sorted_lower: list[str]
) -> tuple[str | None, str]:
    """Attempt to find the correct media filename for a given JSON filename.

    Returns a tuple (matched_media_filename_or_None, method_string).
    media_lower_map maps lowercased filename -> original filename.
    sorted_lower is sorted(media_lower_map.keys()) for bisect lookups.
    """
    inferred_media_name = infer_media_filename_from_json(json_filename)
    if inferred_media_name:
        match = find_prefix_match(inferred_media_name.lower(), sorted_lower, media_lower_map)
        if match:
            return match, "exact_inferred"

    stem = json_filename[:-5]
    dup_match = DUPLICATE_SUFFIX_RE.search(stem)
    if dup_match:
        stem = stem[:dup_match.start()]
    if stem:
        match = find_prefix_match(stem.lower(), sorted_lower, media_lower_map)
        if match:
            return match, "startswith_fallback"

    # Fuzzy fallback: most characters in common (still O(n) but rarely reached)
    best_candidate = None
    best_len = 0
    for original in media_lower_map.values():
        length = common_prefix_length(stem, original)
        if length > best_len:
            best_len = length
            best_candidate = original

    stem_length = len(stem)
    if best_candidate and stem_length > 0 and best_len >= stem_length:
        return best_candidate, "fuzzy_fallback"

    return None, "no_match"


def process_and_copy_media_files(source_folder: str, destination_folder: str, dry_run: bool = False) -> None:
    """Copy media files from source_folder to destination_folder using timestamps from JSON metadata.

    Matching and planning is done sequentially, then all copies run in parallel.
    When dry_run is True, only prints what would be done.
    """
    if not source_folder or not destination_folder:
        print("Source or destination folder not selected.")
        return

    os.makedirs(destination_folder, exist_ok=True)

    entries = os.listdir(source_folder)
    metadata_json_filenames = [f for f in entries if f.lower().endswith(".json")]
    media_filenames = [f for f in entries if not f.lower().endswith(".json")]

    # Pre-build lookup structures once
    media_lower_map = {f.lower(): f for f in media_filenames}
    sorted_lower = sorted(media_lower_map.keys())

    matched_media_files = set()
    unmatched_json_file_list = []
    unmatched_media_file_set = set(media_filenames)
    match_report = {}
    copy_ops = []  # (source_path, destination_path) collected for parallel execution
    base_name_to_datetime = {}  # lowercase stem -> datetime, for Live Photo pairing fallback
    planned_destination_names = set()  # tracks names already allocated to prevent same-timestamp collisions

    for metadata_filename in metadata_json_filenames:
        json_file_path = os.path.join(source_folder, metadata_filename)
        print(f"Processing JSON file: {metadata_filename}")

        try:
            with open(json_file_path, "r") as json_file:
                metadata = json.load(json_file)

            if "photoTakenTime" not in metadata:
                print(f"No 'photoTakenTime' found in {metadata_filename}")
                unmatched_json_file_list.append(metadata_filename)
                match_report[metadata_filename] = (None, "no_timestamp")
                continue

            matched_media_filename, method_used = find_matching_media_for_json(
                metadata_filename, media_lower_map, sorted_lower
            )

            if matched_media_filename:
                local_datetime = local_datetime_from_metadata(metadata)
                timestamped_base_name = local_datetime.strftime("%Y-%m-%d %H.%M.%S")
                extension = os.path.splitext(matched_media_filename)[1].lower()

                unique_new_filename = generate_unique_filename(destination_folder, timestamped_base_name, extension, planned_destination_names)
                source_media_path = os.path.join(source_folder, matched_media_filename)
                destination_media_path = os.path.join(destination_folder, unique_new_filename)

                if dry_run:
                    print(f"[DRY RUN] Would copy: {matched_media_filename} -> {unique_new_filename} (method: {method_used})")
                else:
                    copy_ops.append((source_media_path, destination_media_path))
                    print(f"Matched: {matched_media_filename} -> {unique_new_filename} (method: {method_used})")

                matched_media_files.add(matched_media_filename)
                unmatched_media_file_set.discard(matched_media_filename)
                match_report[metadata_filename] = (matched_media_filename, method_used)
                base_name_to_datetime[os.path.splitext(matched_media_filename)[0].lower()] = local_datetime

            else:
                print(f"No matching media file found for {metadata_filename}")
                unmatched_json_file_list.append(metadata_filename)
                match_report[metadata_filename] = (None, "no_match")

        except Exception as error:
            print(f"Error processing {metadata_filename}: {error}")
            unmatched_json_file_list.append(metadata_filename)
            match_report[metadata_filename] = (None, f"error: {error}")

    # Second pass: match orphaned Live Photo videos (e.g. IMG_3118.MP4) using their
    # companion photo's already-matched timestamp (e.g. from IMG_3118.HEIC.supplemental-metadata.json)
    live_photo_matched = 0
    for media_filename in list(unmatched_media_file_set):
        base = os.path.splitext(media_filename)[0].lower()
        if base in base_name_to_datetime:
            local_datetime = base_name_to_datetime[base]
            timestamped_base_name = local_datetime.strftime("%Y-%m-%d %H.%M.%S")
            extension = os.path.splitext(media_filename)[1].lower()
            unique_new_filename = generate_unique_filename(destination_folder, timestamped_base_name, extension, planned_destination_names)
            source_media_path = os.path.join(source_folder, media_filename)
            destination_media_path = os.path.join(destination_folder, unique_new_filename)
            if dry_run:
                print(f"[DRY RUN] Would copy: {media_filename} -> {unique_new_filename} (method: live_photo_pairing)")
            else:
                copy_ops.append((source_media_path, destination_media_path))
                print(f"Matched: {media_filename} -> {unique_new_filename} (method: live_photo_pairing)")
            matched_media_files.add(media_filename)
            unmatched_media_file_set.discard(media_filename)
            match_report[media_filename] = (media_filename, "live_photo_pairing")
            live_photo_matched += 1

    if live_photo_matched:
        print(f"Live Photo pairing matched {live_photo_matched} additional media files.")

    # Execute all copies in parallel
    if copy_ops:
        print(f"\nCopying {len(copy_ops)} files...")

        def copy_file(op):
            src, dst = op
            try:
                shutil.copy2(src, dst)
            except Exception as e:
                print(f"Error copying {os.path.basename(src)}: {e}")

        with ThreadPoolExecutor(max_workers=8) as executor:
            executor.map(copy_file, copy_ops)

        print("All copies complete.")

    report_path = os.path.join(destination_folder, "match_report.json")
    with open(report_path, "w") as report_file:
        json.dump(match_report, report_file, indent=2)
    print(f"Match report written to: {report_path}")

    if unmatched_json_file_list:
        log_path = os.path.join(destination_folder, "unmatched_json_files.txt")
        with open(log_path, "w") as log_file:
            for filename in unmatched_json_file_list:
                log_file.write(filename + "\n")
        print(f"Logged {len(unmatched_json_file_list)} unmatched JSON files to: {log_path}")

    if unmatched_media_file_set:
        log_path = os.path.join(destination_folder, "unmatched_media_files.txt")
        with open(log_path, "w") as log_file:
            for filename in unmatched_media_file_set:
                log_file.write(filename + "\n")
        print(f"Logged {len(unmatched_media_file_set)} unmatched media files to: {log_path}")
    else:
        print("All media files had matching metadata.")

    total_processed = len(metadata_json_filenames)
    total_matched = len(matched_media_files)
    print(f"Summary: processed {total_processed} JSON metadata files, matched {total_matched} media files.")
    if dry_run:
        print("Note: dry-run mode - no files were actually copied.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Pair Google Takeout JSON sidecars to media files and copy them with canonical names."
    )
    parser.add_argument(
        "--src",
        help="Source folder containing Google Photos files (including JSONs). "
             "If omitted, opens the Tk folder picker."
    )
    parser.add_argument(
        "--dst",
        help="Destination folder where renamed files will be saved. "
             "If omitted, opens the Tk folder picker."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Match files and print the plan without copying anything."
    )
    args = parser.parse_args()

    if not args.src:
        print("Select the folder containing Google Photos files (including JSONs).")
    source_folder_selected = resolve_directory(args.src, "Select Source Folder")

    if not args.dst:
        print("Select the destination folder where renamed files will be saved.")
    destination_folder_selected = resolve_directory(args.dst, "Select Destination Folder", must_exist=False)

    if source_folder_selected and destination_folder_selected:
        process_and_copy_media_files(source_folder_selected, destination_folder_selected, dry_run=args.dry_run)
        print("File copying and renaming completed successfully!")
    else:
        print("Operation canceled.")
