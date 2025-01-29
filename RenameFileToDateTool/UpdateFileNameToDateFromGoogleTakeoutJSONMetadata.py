import json
import os
import shutil
import re
from datetime import datetime
from tkinter import Tk, filedialog

# Open a folder selection dialog
def select_folder(title):
    root = Tk()
    root.withdraw()  # Hide the root window
    folder = filedialog.askdirectory(title=title)
    if folder:
        print(f"Selected folder: {folder}")
    else:
        print("No folder selected.")
    return folder

# Clean filename by removing Google Photos suffixes (_1, _2, .supplemental-metadata.json)
def clean_filename(filename):
    print(f"Cleaning filename: {filename}")
    filename = re.sub(r"(\.supplemental-[^.]+\.json)$", "", filename)
    filename = re.sub(r"\.json$", "", filename)  # Fallback in case ".json" remains
    filename = re.sub(r"(_\d+)?$", "", filename)  # Remove trailing _1, _2, etc.
    print(f"Cleaned filename: {filename}")
    return filename

# Copy and rename files based on JSON metadata
def copy_and_rename_files(source_folder, destination_folder):
    if not source_folder or not destination_folder:
        print("Source or destination folder not selected.")
        return

    os.makedirs(destination_folder, exist_ok=True)  # Ensure destination folder exists

    json_files = [f for f in os.listdir(source_folder) if f.endswith(".json")]
    if not json_files:
        print(f"No JSON files found in the source folder: {source_folder}")

    for json_file in json_files:
        json_path = os.path.join(source_folder, json_file)
        print(f"Processing JSON file: {json_file}")

        try:
            # Read JSON metadata file
            with open(json_path, "r") as f:
                data = json.load(f)

            if "photoTakenTime" in data:
                timestamp = int(data["photoTakenTime"]["timestamp"])
                date = datetime.utcfromtimestamp(timestamp)
                formatted_date = date.strftime("%Y-%m-%d_%H-%M-%S")

                # Get the cleaned filename before searching for a media file
                original_filename = clean_filename(json_file)
                print(f"Looking for media file with base name: {original_filename}")

                # Now, we just use the cleaned filename directly
                media_file = os.path.join(source_folder, original_filename)

                if os.path.exists(media_file):
                    # Get new file name and path
                    new_file_name = f"{formatted_date}{os.path.splitext(media_file)[1]}"
                    new_file_path = os.path.join(destination_folder, new_file_name)

                    # Copy and rename the file
                    shutil.copy2(media_file, new_file_path)
                    print(f"✅ Copied and renamed: {media_file} → {new_file_path}")
                else:
                    print(f"⚠️ No matching media file found for {json_file}")
            else:
                print(f"⚠️ No 'photoTakenTime' found in {json_file}")
        except Exception as e:
            print(f"❌ Error processing {json_file}: {e}")

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
