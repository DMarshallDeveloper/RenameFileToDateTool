import json
import os
import shutil
import re
from datetime import datetime
from tkinter import Tk, filedialog
from zoneinfo import ZoneInfo

nz_tz = ZoneInfo("Pacific/Auckland")

# Open a folder selection dialog
def select_folder(title):
    root = Tk()
    root.withdraw()
    folder = filedialog.askdirectory(title=title)
    if folder:
        print(f"Selected folder: {folder}")
    else:
        print("No folder selected.")
    return folder

def unique_path(dst_folder: str, base: str, ext: str) -> str:
    """Return a filename that doesn’t yet exist in dst_folder."""
    candidate = f"{base}{ext}"
    counter   = 1
    while os.path.exists(os.path.join(dst_folder, candidate)):
        candidate = f"{base}_{counter}{ext}"
        counter  += 1
    return candidate

# Extracts base name and index (if any) from a supplemental JSON filename
# 1️⃣  recognised media extensions – add more if you need them
MEDIA_EXTS = (
    "jpg|jpeg|png|gif|heic|heif|mov|mp4|m4v|avi|mpg|mpeg"
)

# 2️⃣  duplicate tokens that Google sometimes appends *after* the metadata tag
DUP_RE = re.compile(r"(?:\((\d+)\)|_(\d+))$", re.IGNORECASE)

# 3️⃣  capture everything up-to-and-including the real extension
BASE_RE = re.compile(
    rf"^(.*?\.({MEDIA_EXTS}))",        # shortest match up to .ext
    re.IGNORECASE
)

def extract_media_match_name(json_name: str) -> str | None:
    """
    Given any Google-Takeout metadata file name, return the exact
    media file it belongs to, e.g.

        IMG_2770.heic.supplemental-metadata(1).json
                         └───────────────┬─────────┘
                    ->  IMG_2770(1).heic
    """
    if not json_name.lower().endswith(".json"):
        return None
    stem = json_name[:-5]                         # strip ".json"

    # --- grab & remove trailing duplicate marker ---------------------------
    dup_match = DUP_RE.search(stem)
    dup_token = dup_match.group(0) if dup_match else ""
    if dup_match:
        stem = stem[:dup_match.start()]

    # --- take everything up to the real media extension --------------------
    base_match = BASE_RE.match(stem)
    if not base_match:
        return None                               # no valid extension found
    media_part = base_match.group(1)              # e.g. "IMG_2770.heic"

    # --- re-insert duplicate token *before* the extension ------------------
    if dup_token:
        root, ext = os.path.splitext(media_part)
        media_part = f"{root}{dup_token}{ext}"

    print(f"Best guess at file name: {media_part}")
    return media_part

def copy_and_rename_files(source_folder, destination_folder):
    if not source_folder or not destination_folder:
        print("Source or destination folder not selected.")
        return

    os.makedirs(destination_folder, exist_ok=True)

    files_in_dir = os.listdir(source_folder)
    json_files = [f for f in files_in_dir if f.endswith(".json")]
    media_files = [f for f in files_in_dir if not f.endswith(".json")]

    matched_files = set()
    unmatched_jsons = []
    unmatched_media = set(media_files)

    for json_file in json_files:
        json_path = os.path.join(source_folder, json_file)
        print(f"Processing JSON file: {json_file}")

        try:
            with open(json_path, "r") as f:
                data = json.load(f)

            if "photoTakenTime" not in data:
                print(f"⚠️ No 'photoTakenTime' found in {json_file}")
                unmatched_jsons.append(json_file)
                continue

            # Extract base name with index
            media_base = extract_media_match_name(json_file)
            if not media_base:
                print(f"⚠️ Could not infer media file name from {json_file}")
                unmatched_jsons.append(json_file)
                continue

            media_file = next((m for m in media_files if m.startswith(media_base)), None)

            if media_file:
                timestamp = int(data["photoTakenTime"]["timestamp"])

                local_dt = datetime.fromtimestamp(timestamp, tz=nz_tz)
                base_stamp = local_dt.strftime("%Y-%m-%d_%H-%M-%S")
                ext = os.path.splitext(media_file)[1].lower()

                new_file_name = unique_path(destination_folder, base_stamp, ext)

                src_media_path = os.path.join(source_folder, media_file)
                dst_media_path = os.path.join(destination_folder, new_file_name)

                shutil.copy2(src_media_path, dst_media_path)
                matched_files.add(media_file)
                unmatched_media.discard(media_file)

                print(f"✅ Copied and renamed: {media_file} → {new_file_name}")
            else:
                print(f"⚠️ No matching media file found for {json_file}")
                unmatched_jsons.append(json_file)

        except Exception as e:
            print(f"❌ Error processing {json_file}: {e}")
            unmatched_jsons.append(json_file)

    # Log unmatched JSON files
    if unmatched_jsons:
        log_path = os.path.join(destination_folder, "unmatched_json_files.txt")
        with open(log_path, "w") as log_file:
            for f in unmatched_jsons:
                log_file.write(f + "\n")
        print(f"\n📝 Logged {len(unmatched_jsons)} unmatched JSON files to: {log_path}")

    # Log unmatched media files
    if unmatched_media:
        log_path = os.path.join(destination_folder, "unmatched_media_files.txt")
        with open(log_path, "w") as log_file:
            for f in unmatched_media:
                log_file.write(f + "\n")
        print(f"📝 Logged {len(unmatched_media)} unmatched media files to: {log_path}")
    else:
        print("\n✅ All media files had matching metadata.")

if __name__ == "__main__":
    print("Select the folder containing Google Photos files (including JSONs).")
    source_folder = select_folder("Select Source Folder")
    print("Select the destination folder where renamed files will be saved.")
    destination_folder = select_folder("Select Destination Folder")

    if source_folder and destination_folder:
        copy_and_rename_files(source_folder, destination_folder)
        print("\n✅ File copying and renaming completed successfully!")
    else:
        print("\n❌ Operation canceled.")
