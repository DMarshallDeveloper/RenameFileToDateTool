import os
import subprocess
from datetime import datetime
from tkinter import filedialog, Tk
from dateutil import parser

# --- Config ---
IMAGE_FILE_EXTENSIONS = ["jpg", "jpeg", "png", "gif", "heic", "tiff"]
VIDEO_FILE_EXTENSIONS = ["avi", "mpg", "mp4", "mov", "mkv"]
IMAGE_FILE_DATE_ATTRIBUTES = ["DateTimeOriginal", "CreateDate", "DateCreated", "ModifyDate"]
VIDEO_FILE_DATE_ATTRIBUTES = ["MediaCreateDate", "MediaModifyDate", "TrackCreateDate",
                              "TrackModifyDate", "CreateDate", "ModifyDate"]
ATTRIBUTE_TO_EXIF_NAME_DICT = {
    "DateTimeOriginal": "Date/Time Original",
    "CreateDate": "Create Date",
    "DateCreated": "Date Created",
    "ModifyDate": "Modify Date",
    "TrackCreateDate": "Track Create Date",
    "TrackModifyDate": "Track Modify Date",
    "MediaCreateDate": "Media Create Date",
    "MediaModifyDate": "Media Modify Date"
}
EXE = "exiftool.exe"


# --- Utilities ---
def choose_directory():
    root = Tk()
    root.withdraw()
    return filedialog.askdirectory(title="Select Photos Directory")


def extract_best_date(file_metadata, file_path):
    """Extract best available datetime from metadata in priority order."""
    ext = file_metadata.get("File Type Extension", "").lower()

    if ext in IMAGE_FILE_EXTENSIONS:
        attribute_list = IMAGE_FILE_DATE_ATTRIBUTES
    elif ext in VIDEO_FILE_EXTENSIONS:
        attribute_list = VIDEO_FILE_DATE_ATTRIBUTES
    else:
        return None

    for attribute in attribute_list:
        exif_name = ATTRIBUTE_TO_EXIF_NAME_DICT.get(attribute)
        if exif_name and exif_name in file_metadata:
            date_time_string = file_metadata[exif_name]
            date_time_string = date_time_string.split("+")[0].split(".")[0].strip()
            try:
                return datetime.strptime(date_time_string, "%Y:%m:%d %H:%M:%S")
            except ValueError:
                try:
                    return parser.parse(date_time_string)
                except Exception:
                    continue

    # Fallback: file modified time
    try:
        stat = os.stat(file_path)
        return datetime.fromtimestamp(stat.st_mtime)
    except Exception:
        return None


def get_metadata(file_path):
    """Run exiftool and return metadata as dict."""
    process = subprocess.Popen([EXE, file_path],
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,
                               universal_newlines=True)
    file_metadata = {}
    for output in process.stdout:
        if ":" not in output:
            continue
        line = output.strip().split(":", 1)
        key = line[0].strip()
        value = line[1].strip()
        file_metadata[key] = value
    return file_metadata


# --- Main rename workflow ---
def rename_photos(directory):
    if not directory:
        print("No directory selected. Exiting.")
        return

    files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
    files_renamed_count = 0
    new_file_names = {}

    for file in files:
        file_path = os.path.join(directory, file)
        file_metadata = get_metadata(file_path)
        date_time = extract_best_date(file_metadata, file_path)

        if not date_time:
            print(f"Unable to extract date from {file}. Skipping.")
            continue

        # Format new filename
        new_file_name_base = date_time.strftime('%Y-%m-%d %H.%M.%S')
        if new_file_name_base not in new_file_names:
            new_file_names[new_file_name_base] = 0
        new_file_names[new_file_name_base] += 1

        new_ext = file_metadata.get('File Type Extension', os.path.splitext(file)[1].replace('.', ''))
        new_file_name = f"{new_file_name_base}_{new_file_names[new_file_name_base]}.{new_ext.lower()}"
        new_path = os.path.join(directory, new_file_name)

        os.rename(file_path, new_path)
        files_renamed_count += 1

        if files_renamed_count % 50 == 0:
            print('Files renamed:', files_renamed_count)

    print(f"{files_renamed_count} files have been renamed.")


# --- Write EXIF dates from filename ---
def process_exif_tool_command(attribute, old_file_metadata, file_path, date_time, error_log):
    exif_tool_argument = f'-{attribute}="{date_time.strftime("%Y:%m:%d %H:%M:%S")}"'
    try:
        print('old ' + ATTRIBUTE_TO_EXIF_NAME_DICT[attribute] + ' value is: ' +
              old_file_metadata[ATTRIBUTE_TO_EXIF_NAME_DICT[attribute]])
    except KeyError:
        print("No old value of " + attribute + " was found")

    change_process = subprocess.Popen([EXE, exif_tool_argument, file_path, "-overwrite_original"],
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT,
                                      universal_newlines=True)
    try:
        change_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        error_log.write("Timeout when changing metadata " + attribute + " of " + file_path + "\n")


def change_exif_date(directory: str):
    if not directory:
        print("No directory selected. Exiting.")
        return

    files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
    files_updated = 0
    error_log = open("error_log.txt", 'w')

    for file in files:
        file_path = os.path.join(directory, file)
        file_metadata = get_metadata(file_path)

        # Parse datetime from start of filename
        try:
            date_time_string = file_metadata['File Name'][0:16]
            date_time = datetime.strptime(date_time_string, '%Y-%m-%d %H.%M')
        except Exception:
            print(f"Error parsing datetime from filename: {file}. Skipping.")
            continue

        ext = file_metadata.get("File Type Extension", "").lower()
        if ext in IMAGE_FILE_EXTENSIONS:
            for attribute in IMAGE_FILE_DATE_ATTRIBUTES:
                process_exif_tool_command(attribute, file_metadata, file_path, date_time, error_log)
        elif ext in VIDEO_FILE_EXTENSIONS:
            for attribute in VIDEO_FILE_DATE_ATTRIBUTES:
                process_exif_tool_command(attribute, file_metadata, file_path, date_time, error_log)
        else:
            print(f"Invalid file type: {file}. Skipping.")
            continue

        files_updated += 1
        if files_updated % 50 == 0:
            print('Files updated:', files_updated)

    print(f"{files_updated} files have been updated.")


# --- Entry point ---
if __name__ == "__main__":
    directory = choose_directory()
    editing_exif_not_name = input("Are you renaming files from date metadata (0) "
                                  "or writing metadata from filename (1): ")

    if editing_exif_not_name == '0':
        rename_photos(directory)
    else:
        change_exif_date(directory)
