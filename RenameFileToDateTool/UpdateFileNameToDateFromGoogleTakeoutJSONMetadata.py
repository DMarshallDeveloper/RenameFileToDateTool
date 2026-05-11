import json
import os
import shutil
import re
from datetime import datetime
from tkinter import Tk, filedialog
from zoneinfo import ZoneInfo

# Use a descriptive timezone name for clarity
NEW_ZEALAND_TIMEZONE = ZoneInfo("Pacific/Auckland")


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


def generate_unique_filename(output_folder: str, base_name: str, extension: str) -> str:
    """Return a filename (base_name + extension) that doesn't already exist in output_folder.

    If a file already exists, append _1, _2, ... until an unused name is found.
    """
    candidate_name = f"{base_name}{extension}"
    counter = 1
    while os.path.exists(os.path.join(output_folder, candidate_name)):
        candidate_name = f"{base_name}_{counter}{extension}"
        counter += 1
    return candidate_name


# Recognised media file extensions (add more if you need them)
MEDIA_EXTENSIONS = (
    "jpg|jpeg|png|gif|heic|heif|mov|mp4|m4v|avi|mpg|mpeg"
)

# Pattern matching for Google duplicate tokens such as _(1) or (1)
DUPLICATE_SUFFIX_RE = re.compile(r"(?:\((\d+)\)|_(\d+))$", re.IGNORECASE)

# Capture everything up to and including a recognised real extension
REAL_EXTENSION_RE = re.compile(rf"^(.*?\.({MEDIA_EXTENSIONS}))", re.IGNORECASE)


def infer_media_filename_from_json(json_filename: str) -> str | None:
    """Given a Google-Takeout supplemental JSON filename, return the media filename it most likely belongs to.

    Returns a string like "IMG_2770(1).heic" or None if no clear match can be inferred.
    """
    if not json_filename.lower().endswith(".json"):
        return None
    stem = json_filename[:-5]  # remove the trailing ".json"

    # Remove trailing duplicate marker (if present)
    duplicate_match = DUPLICATE_SUFFIX_RE.search(stem)
    duplicate_token = duplicate_match.group(0) if duplicate_match else ""
    if duplicate_match:
        stem = stem[:duplicate_match.start()]

    # Try to find a real media extension inside the stem
    base_match = REAL_EXTENSION_RE.match(stem)
    if not base_match:
        return None

    media_part = base_match.group(1)  # e.g. "IMG_2770.heic"

    # If there was a duplicate token removed earlier, re-insert it before the extension
    if duplicate_token:
        root, ext = os.path.splitext(media_part)
        media_part = f"{root}{duplicate_token}{ext}"

    print(f"Best guess at file name: {media_part}")
    return media_part


def find_matching_media_for_json(json_filename: str, available_media_filenames: list[str]) -> tuple[str | None, str]:
    """Attempt to find the correct media filename for a given JSON filename.

    Returns a tuple (matched_media_filename_or_None, method_string).
    """
    inferred_media_name = infer_media_filename_from_json(json_filename)
    if inferred_media_name:
        for candidate in available_media_filenames:
            if candidate.lower().startswith(inferred_media_name.lower()):
                return candidate, "exact_inferred"

    stem = json_filename[:-5]  # remove ".json"
    dup_match = DUPLICATE_SUFFIX_RE.search(stem)
    if dup_match:
        stem = stem[:dup_match.start()]
    if stem:
        for candidate in available_media_filenames:
            if candidate.lower().startswith(stem.lower()):
                return candidate, "startswith_fallback"

    def common_prefix_length(a: str, b: str) -> int:
        a_root = os.path.splitext(a)[0].lower()
        b_root = os.path.splitext(b)[0].lower()
        return len(os.path.commonprefix([a_root, b_root]))

    best_candidate = None
    best_len = 0
    for candidate in available_media_filenames:
        length = common_prefix_length(stem, candidate)
        if length > best_len:
            best_len = length
            best_candidate = candidate

    stem_length = len(stem)
    if best_candidate and stem_length > 0 and best_len >= stem_length:
        return best_candidate, "fuzzy_fallback"

    return None, "no_match"


def process_and_copy_media_files(source_folder: str, destination_folder: str, dry_run: bool = False) -> None:
    """Copy media files from source_folder to destination_folder using timestamps from each JSON's metadata.

    When dry_run is True, only prints what would be done.
    """
    if not source_folder or not destination_folder:
        print("Source or destination folder not selected.")
        return

    os.makedirs(destination_folder, exist_ok=True)

    entries_in_source_folder = os.listdir(source_folder)
    metadata_json_filenames = [f for f in entries_in_source_folder if f.lower().endswith(".json")]
    media_filenames = [f for f in entries_in_source_folder if not f.lower().endswith(".json")]

    matched_media_files = set()
    unmatched_json_file_list = []
    unmatched_media_file_set = set(media_filenames)

    match_report = {}

    for metadata_filename in metadata_json_filenames:
        json_file_path = os.path.join(source_folder, metadata_filename)
        print(f"Processing JSON file: {metadata_filename}")

        try:
            with open(json_file_path, "r") as json_file:
                metadata = json.load(json_file)

            if "photoTakenTime" not in metadata:
                print(f"⚠️ No 'photoTakenTime' found in {metadata_filename}")
                unmatched_json_file_list.append(metadata_filename)
                match_report[metadata_filename] = (None, "no_timestamp")
                continue

            matched_media_filename, method_used = find_matching_media_for_json(metadata_filename, media_filenames)

            if matched_media_filename:
                timestamp_seconds = int(metadata["photoTakenTime"]["timestamp"])
                local_datetime = datetime.fromtimestamp(timestamp_seconds, tz=NEW_ZEALAND_TIMEZONE)
                timestamped_base_name = local_datetime.strftime("%Y-%m-%d_%H-%M-%S")
                extension = os.path.splitext(matched_media_filename)[1].lower()

                unique_new_filename = generate_unique_filename(destination_folder, timestamped_base_name, extension)

                source_media_path = os.path.join(source_folder, matched_media_filename)
                destination_media_path = os.path.join(destination_folder, unique_new_filename)

                if dry_run:
                    print(f"[DRY RUN] Would copy: {matched_media_filename} -> {unique_new_filename} (method: {method_used})")
                else:
                    shutil.copy2(source_media_path, destination_media_path)
                    print(f"✅ Copied and renamed: {matched_media_filename} → {unique_new_filename} (method: {method_used})")

                matched_media_files.add(matched_media_filename)
                unmatched_media_file_set.discard(matched_media_filename)
                match_report[metadata_filename] = (matched_media_filename, method_used)

            else:
                print(f"⚠️ No matching media file found for {metadata_filename}")
                unmatched_json_file_list.append(metadata_filename)
                match_report[metadata_filename] = (None, "no_match")

        except Exception as error:
            print(f"❌ Error processing {metadata_filename}: {error}")
            unmatched_json_file_list.append(metadata_filename)
            match_report[metadata_filename] = (None, f"error: {error}")

    report_path = os.path.join(destination_folder, "match_report.json")
    with open(report_path, "w") as report_file:
        json.dump(match_report, report_file, indent=2)
    print(f"📝 Detailed match report written to: {report_path}")

    if unmatched_json_file_list:
        log_path = os.path.join(destination_folder, "unmatched_json_files.txt")
        with open(log_path, "w") as log_file:
            for filename in unmatched_json_file_list:
                log_file.write(filename + "\n")
        print(f"📝 Logged {len(unmatched_json_file_list)} unmatched JSON files to: {log_path}")

    if unmatched_media_file_set:
        log_path = os.path.join(destination_folder, "unmatched_media_files.txt")
        with open(log_path, "w") as log_file:
            for filename in unmatched_media_file_set:
                log_file.write(filename + "\n")
        print(f"📝 Logged {len(unmatched_media_file_set)} unmatched media files to: {log_path}")
    else:
        print("✅ All media files had matching metadata.")

    total_processed = len(metadata_json_filenames)
    total_matched = len(matched_media_files)
    print(f"Summary: processed {total_processed} JSON metadata files, matched {total_matched} media files.")
    if dry_run:
        print("Note: dry-run mode — no files were actually copied.")


if __name__ == "__main__":
    print("Select the folder containing Google Photos files (including JSONs).")
    source_folder_selected = select_folder_dialog("Select Source Folder")
    print("Select the destination folder where renamed files will be saved.")
    destination_folder_selected = select_folder_dialog("Select Destination Folder")

    if source_folder_selected and destination_folder_selected:
        response = input("Run a dry-run first (no files will be copied)? [y/N]: ").strip().lower()
        dry_run_mode = response == 'y'

        process_and_copy_media_files(source_folder_selected, destination_folder_selected, dry_run=dry_run_mode)
        print("✅ File copying and renaming completed successfully!")
    else:
        print("❌ Operation canceled.")
