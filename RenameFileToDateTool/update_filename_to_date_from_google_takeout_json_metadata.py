"""update_filename_to_date_from_google_takeout_json_metadata.py — Takeout dump ingester.

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

Run with ``python update_filename_to_date_from_google_takeout_json_metadata.py``.
You'll be prompted to pick a source folder, a destination folder, and whether
to dry-run first.

After this finishes, the destination folder is ready to drop into the master
library's ``_Inbox`` for ingestion via ``ingest_inbox_to_master.py``.
"""

import bisect
import hashlib
import json
import logging
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor

from photo_lib.extensions import MEDIA_EXTENSIONS as _MEDIA_EXT_SET
from photo_lib.logging_setup import configure_logging
from photo_lib.takeout_geo import (
    DEFAULT_TIMEZONE as NEW_ZEALAND_TIMEZONE,  # back-compat alias for tests
    local_datetime_from_metadata,
    resolve_timezone_from_geo,
)
from photo_lib.tk_picker import resolve_directory

logger = logging.getLogger("photo_lib")

# The regex needs a pipe-separated string of extensions (case-insensitive matching).
MEDIA_EXTENSIONS = "|".join(sorted(_MEDIA_EXT_SET))

DUPLICATE_SUFFIX_RE = re.compile(r"(?:\((\d+)\)|_(\d+))$", re.IGNORECASE)
REAL_EXTENSION_RE = re.compile(rf"^(.*?\.({MEDIA_EXTENSIONS}))", re.IGNORECASE)
CANONICAL_FILENAME_RE = re.compile(
    r"^(?P<base>\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2})_\d+(?P<ext>\.[A-Za-z0-9]+)$"
)


def hash_file(file_path: str, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 hex digest of a file, streamed in chunks."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def build_destination_index(destination_folder: str) -> dict[tuple[str, str], list[tuple[str, int]]]:
    """Group existing destination files by (base_timestamp, lowercase_extension).

    Used by plan_destination_filename to find candidates that might be content-duplicates
    of a fresh source file. Returns a dict keyed by (base, ext) -> list of (filename, size),
    so the planner can size-filter cheaply before hashing.
    """
    index: dict[tuple[str, str], list[tuple[str, int]]] = {}
    if not os.path.isdir(destination_folder):
        return index
    for entry in os.scandir(destination_folder):
        if not entry.is_file():
            continue
        match = CANONICAL_FILENAME_RE.match(entry.name)
        if not match:
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            continue
        key = (match.group("base"), match.group("ext").lower())
        index.setdefault(key, []).append((entry.name, size))
    return index


def plan_destination_filename(
    output_folder: str,
    base_name: str,
    extension: str,
    source_path: str,
    planned_names: set,
    existing_destination_index: dict[tuple[str, str], list[tuple[str, int]]],
    planned_hashes_by_base: dict[tuple[str, str], dict[str, str]],
) -> tuple[str | None, str]:
    """Choose a destination filename for source_path, or return None if it's already there.

    Returns (filename, status). status is one of:
      - "allocated"                — fresh slot; filename is '<base>_<N><ext>' and the
                                     caller should perform the copy.
      - "skipped_existing:<name>"  — destination already contains a file with the same
                                     base timestamp and identical content.
      - "skipped_planned:<name>"   — another source file in THIS run with the same base
                                     timestamp had identical content and is already planned.

    Skip detection is content-based: same base timestamp + same byte size, then SHA-256
    hash comparison. This makes re-running the ingest idempotent — previously the script
    only checked filenames, so a re-run silently allocated _3, _4, … alongside the original
    _1, _2.
    """
    key = (base_name, extension.lower())
    source_hash: str | None = None

    planned_for_key = planned_hashes_by_base.get(key)
    if planned_for_key:
        source_hash = hash_file(source_path)
        existing_planned_name = planned_for_key.get(source_hash)
        if existing_planned_name is not None:
            return None, f"skipped_planned:{existing_planned_name}"

    try:
        source_size = os.path.getsize(source_path)
    except OSError:
        source_size = -1

    for existing_filename, existing_size in existing_destination_index.get(key, []):
        if existing_size != source_size:
            continue
        if source_hash is None:
            source_hash = hash_file(source_path)
        existing_path = os.path.join(output_folder, existing_filename)
        try:
            existing_hash = hash_file(existing_path)
        except OSError:
            continue
        if existing_hash == source_hash:
            return None, f"skipped_existing:{existing_filename}"

    if source_hash is None:
        source_hash = hash_file(source_path)

    counter = 1
    while True:
        candidate_name = f"{base_name}_{counter}{extension}"
        if not os.path.exists(os.path.join(output_folder, candidate_name)) and candidate_name not in planned_names:
            planned_names.add(candidate_name)
            planned_hashes_by_base.setdefault(key, {})[source_hash] = candidate_name
            existing_destination_index.setdefault(key, []).append((candidate_name, source_size))
            return candidate_name, "allocated"
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

    logger.debug("Best guess at file name: %s", media_part)
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


def _plan_copies_for_source(source_folder: str, destination_folder: str, dry_run: bool, shared: dict) -> None:
    """Plan copies for one source folder, mutating ``shared`` planning state.

    Shared state is built once by ``process_and_copy_media_files`` so dedup,
    counter allocation, and copy queueing span every source folder in the run.
    Live-Photo pairing stays per-folder because the HEIC/MP4 pair is always
    co-located in the same Takeout subfolder.
    """
    source_label = os.path.basename(source_folder.rstrip(os.sep)) or source_folder

    entries = os.listdir(source_folder)
    metadata_json_filenames = [f for f in entries if f.lower().endswith(".json")]
    media_filenames = [f for f in entries if not f.lower().endswith(".json")]

    media_lower_map = {f.lower(): f for f in media_filenames}
    sorted_lower = sorted(media_lower_map.keys())

    unmatched_media_file_set = set(media_filenames)
    base_name_to_datetime = {}  # lowercase stem -> datetime, for Live Photo pairing fallback (per-folder)

    match_report = shared["match_report"]
    unmatched_json_file_list = shared["unmatched_json_file_list"]
    copy_ops = shared["copy_ops"]
    planned_destination_names = shared["planned_destination_names"]
    existing_destination_index = shared["existing_destination_index"]
    planned_hashes_by_base = shared["planned_hashes_by_base"]

    shared["total_metadata_processed"] += len(metadata_json_filenames)

    for metadata_filename in metadata_json_filenames:
        json_file_path = os.path.join(source_folder, metadata_filename)
        report_key = f"{source_label}/{metadata_filename}"
        logger.debug("Processing JSON file: %s", report_key)

        try:
            with open(json_file_path, "r") as json_file:
                metadata = json.load(json_file)

            if "photoTakenTime" not in metadata:
                logger.warning("No 'photoTakenTime' found in %s", report_key)
                unmatched_json_file_list.append(report_key)
                match_report[report_key] = (None, "no_timestamp")
                continue

            matched_media_filename, method_used = find_matching_media_for_json(
                metadata_filename, media_lower_map, sorted_lower
            )

            if matched_media_filename:
                local_datetime = local_datetime_from_metadata(metadata)
                timestamped_base_name = local_datetime.strftime("%Y-%m-%d %H.%M.%S")
                extension = os.path.splitext(matched_media_filename)[1].lower()
                source_media_path = os.path.join(source_folder, matched_media_filename)

                unique_new_filename, plan_status = plan_destination_filename(
                    destination_folder,
                    timestamped_base_name,
                    extension,
                    source_media_path,
                    planned_destination_names,
                    existing_destination_index,
                    planned_hashes_by_base,
                )

                if unique_new_filename is None:
                    shared["skipped_duplicate_count"] += 1
                    logger.info("Skipping duplicate: %s already at destination as %s (method: %s)",
                                f"{source_label}/{matched_media_filename}",
                                plan_status.split(":", 1)[1], method_used)
                    shared["matched_media_files"].add(f"{source_label}/{matched_media_filename}")
                    unmatched_media_file_set.discard(matched_media_filename)
                    match_report[report_key] = (matched_media_filename, f"{method_used}|{plan_status}")
                    base_name_to_datetime[os.path.splitext(matched_media_filename)[0].lower()] = local_datetime
                    continue

                destination_media_path = os.path.join(destination_folder, unique_new_filename)

                if dry_run:
                    logger.info("[DRY RUN] Would copy: %s -> %s (method: %s)",
                                f"{source_label}/{matched_media_filename}",
                                unique_new_filename, method_used)
                else:
                    copy_ops.append((source_media_path, destination_media_path))
                    logger.info("Matched: %s -> %s (method: %s)",
                                f"{source_label}/{matched_media_filename}",
                                unique_new_filename, method_used)

                shared["matched_media_files"].add(f"{source_label}/{matched_media_filename}")
                unmatched_media_file_set.discard(matched_media_filename)
                match_report[report_key] = (matched_media_filename, method_used)
                base_name_to_datetime[os.path.splitext(matched_media_filename)[0].lower()] = local_datetime

            else:
                logger.warning("No matching media file found for %s", report_key)
                unmatched_json_file_list.append(report_key)
                match_report[report_key] = (None, "no_match")

        except Exception as error:
            logger.error("Error processing %s: %s", report_key, error)
            unmatched_json_file_list.append(report_key)
            match_report[report_key] = (None, f"error: {error}")

    # Second pass: match orphaned Live Photo videos (e.g. IMG_3118.MP4) using their
    # companion photo's already-matched timestamp (e.g. from IMG_3118.HEIC.supplemental-metadata.json)
    for media_filename in list(unmatched_media_file_set):
        base = os.path.splitext(media_filename)[0].lower()
        if base in base_name_to_datetime:
            local_datetime = base_name_to_datetime[base]
            timestamped_base_name = local_datetime.strftime("%Y-%m-%d %H.%M.%S")
            extension = os.path.splitext(media_filename)[1].lower()
            source_media_path = os.path.join(source_folder, media_filename)
            tagged_media_label = f"{source_label}/{media_filename}"

            unique_new_filename, plan_status = plan_destination_filename(
                destination_folder,
                timestamped_base_name,
                extension,
                source_media_path,
                planned_destination_names,
                existing_destination_index,
                planned_hashes_by_base,
            )

            if unique_new_filename is None:
                shared["skipped_duplicate_count"] += 1
                logger.info("Skipping duplicate: %s already at destination as %s (method: live_photo_pairing)",
                            tagged_media_label, plan_status.split(":", 1)[1])
                shared["matched_media_files"].add(tagged_media_label)
                unmatched_media_file_set.discard(media_filename)
                match_report[tagged_media_label] = (media_filename, f"live_photo_pairing|{plan_status}")
                shared["live_photo_matched"] += 1
                continue

            destination_media_path = os.path.join(destination_folder, unique_new_filename)
            if dry_run:
                logger.info("[DRY RUN] Would copy: %s -> %s (method: live_photo_pairing)",
                            tagged_media_label, unique_new_filename)
            else:
                copy_ops.append((source_media_path, destination_media_path))
                logger.info("Matched: %s -> %s (method: live_photo_pairing)",
                            tagged_media_label, unique_new_filename)
            shared["matched_media_files"].add(tagged_media_label)
            unmatched_media_file_set.discard(media_filename)
            match_report[tagged_media_label] = (media_filename, "live_photo_pairing")
            shared["live_photo_matched"] += 1

    # Hand any still-unmatched media filenames (per this source) up to shared state, tagged.
    for media_filename in unmatched_media_file_set:
        shared["unmatched_media_file_list"].append(f"{source_label}/{media_filename}")


def process_and_copy_media_files(source_folders, destination_folder: str, dry_run: bool = False) -> None:
    """Copy media files from one or more source folders into one destination.

    ``source_folders`` accepts either a single folder string (back-compat) or a list of
    folder strings. The dedup index, counter allocation, and copy queue are shared across
    every source, so a file repeated across folders is copied only once.

    Matching and planning are done sequentially per source; all copies then run in parallel.
    """
    if isinstance(source_folders, str):
        source_folders = [source_folders]
    source_folders = [f for f in (source_folders or []) if f]

    if not source_folders or not destination_folder:
        logger.error("Source or destination folder not selected.")
        return

    os.makedirs(destination_folder, exist_ok=True)

    shared = {
        "match_report": {},
        "unmatched_json_file_list": [],
        "unmatched_media_file_list": [],
        "matched_media_files": set(),
        "copy_ops": [],
        "planned_destination_names": set(),
        "existing_destination_index": build_destination_index(destination_folder),
        "planned_hashes_by_base": {},
        "skipped_duplicate_count": 0,
        "live_photo_matched": 0,
        "total_metadata_processed": 0,
    }

    for source_folder in source_folders:
        if not os.path.isdir(source_folder):
            logger.warning("Source folder does not exist, skipping: %s", source_folder)
            continue
        logger.info("Processing source folder: %s", source_folder)
        _plan_copies_for_source(source_folder, destination_folder, dry_run, shared)

    match_report = shared["match_report"]
    unmatched_json_file_list = shared["unmatched_json_file_list"]
    unmatched_media_file_list = shared["unmatched_media_file_list"]
    copy_ops = shared["copy_ops"]

    if shared["live_photo_matched"]:
        logger.info("Live Photo pairing matched %d additional media files (across all sources).",
                    shared["live_photo_matched"])

    # Execute all copies in parallel
    if copy_ops:
        logger.info("Copying %d files...", len(copy_ops))

        def copy_file(op):
            src, dst = op
            try:
                shutil.copy2(src, dst)
            except Exception as e:
                logger.error("Error copying %s: %s", os.path.basename(src), e)

        with ThreadPoolExecutor(max_workers=8) as executor:
            executor.map(copy_file, copy_ops)

        logger.info("All copies complete.")

    report_path = os.path.join(destination_folder, "match_report.json")
    with open(report_path, "w") as report_file:
        json.dump(match_report, report_file, indent=2)
    logger.info("Match report written to: %s", report_path)

    if unmatched_json_file_list:
        log_path = os.path.join(destination_folder, "unmatched_json_files.txt")
        with open(log_path, "w") as log_file:
            for filename in unmatched_json_file_list:
                log_file.write(filename + "\n")
        logger.info("Logged %d unmatched JSON files to: %s", len(unmatched_json_file_list), log_path)

    if unmatched_media_file_list:
        log_path = os.path.join(destination_folder, "unmatched_media_files.txt")
        with open(log_path, "w") as log_file:
            for filename in unmatched_media_file_list:
                log_file.write(filename + "\n")
        logger.info("Logged %d unmatched media files to: %s", len(unmatched_media_file_list), log_path)
    else:
        logger.info("All media files had matching metadata.")

    total_processed = shared["total_metadata_processed"]
    total_matched = len(shared["matched_media_files"])
    logger.info("Summary: processed %d JSON metadata files across %d source folder(s), matched %d media files.",
                total_processed, len(source_folders), total_matched)
    if shared["skipped_duplicate_count"]:
        logger.info("Skipped %d media files whose content was already at the destination.",
                    shared["skipped_duplicate_count"])
    if dry_run:
        logger.info("Note: dry-run mode - no files were actually copied.")


def _pick_source_folders_via_dialog() -> list[str]:
    """Open the Tk folder picker repeatedly until the user cancels, collecting source folders."""
    from photo_lib.tk_picker import choose_directory

    chosen_source_folders: list[str] = []
    while True:
        ordinal_label = "first" if not chosen_source_folders else f"another (#{len(chosen_source_folders) + 1})"
        next_folder = choose_directory(f"Select {ordinal_label} source folder (cancel when done)")
        if not next_folder:
            break
        chosen_source_folders.append(next_folder)
        logger.info("Added source folder: %s", next_folder)
    return chosen_source_folders


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Pair Google Takeout JSON sidecars to media files and copy them with canonical names."
    )
    parser.add_argument(
        "--src", nargs="+",
        help="One or more source folders containing Google Photos files (including JSONs). "
             "Repeat the flag or pass multiple paths after one --src. If omitted, the Tk folder "
             "picker opens repeatedly until you cancel."
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

    configure_logging("takeout_json_to_filename")

    if args.src:
        selected_source_folders = args.src
    else:
        logger.info("Select one or more source folders (cancel the picker when done).")
        selected_source_folders = _pick_source_folders_via_dialog()

    if not args.dst:
        logger.info("Select the destination folder where renamed files will be saved.")
    selected_destination_folder = resolve_directory(args.dst, "Select Destination Folder", must_exist=False)

    if selected_source_folders and selected_destination_folder:
        logger.info("Ingesting %d source folder(s) into %s",
                    len(selected_source_folders), selected_destination_folder)
        process_and_copy_media_files(selected_source_folders, selected_destination_folder, dry_run=args.dry_run)
        logger.info("File copying and renaming completed successfully!")
    else:
        logger.info("Operation canceled.")
