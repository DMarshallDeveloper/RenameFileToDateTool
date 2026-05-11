import csv
import io
import json
import os
import subprocess
import tempfile
from datetime import datetime
from tkinter import filedialog, Tk
from dateutil import parser

# --- Config ---
IMAGE_FILE_EXTENSIONS = ["jpg", "jpeg", "png", "gif", "heic", "tiff"]
VIDEO_FILE_EXTENSIONS = ["avi", "mpg", "mp4", "mov", "mkv"]
IMAGE_FILE_DATE_ATTRIBUTES = ["DateTimeOriginal", "CreateDate", "DateCreated", "ModifyDate"]
VIDEO_FILE_DATE_ATTRIBUTES = ["MediaCreateDate", "MediaModifyDate", "TrackCreateDate",
                              "TrackModifyDate", "CreateDate", "ModifyDate"]
EXE = "exiftool.exe"
CHUNK_SIZE = 500


# --- Utilities ---
def choose_directory():
    root = Tk()
    root.withdraw()
    return filedialog.askdirectory(title="Select Photos Directory")


def chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def extract_best_date(file_metadata, file_path):
    """Extract best available datetime from metadata in priority order."""
    ext = file_metadata.get("FileTypeExtension", "").lower()

    if ext in IMAGE_FILE_EXTENSIONS:
        attribute_list = IMAGE_FILE_DATE_ATTRIBUTES
    elif ext in VIDEO_FILE_EXTENSIONS:
        attribute_list = VIDEO_FILE_DATE_ATTRIBUTES
    else:
        return None

    for attribute in attribute_list:
        date_time_string = file_metadata.get(attribute)
        if date_time_string:
            date_time_string = str(date_time_string).split("+")[0].split(".")[0].strip()
            try:
                return datetime.strptime(date_time_string, "%Y:%m:%d %H:%M:%S")
            except ValueError:
                try:
                    return parser.parse(date_time_string)
                except Exception:
                    continue

    # Fallback: file modified time
    try:
        return datetime.fromtimestamp(os.stat(file_path).st_mtime)
    except Exception:
        return None


def get_all_metadata(file_paths):
    """Run exiftool in chunks and return a dict of filename -> metadata."""
    metadata_by_name = {}
    for chunk in chunked(file_paths, CHUNK_SIZE):
        result = subprocess.run(
            [EXE, '-json'] + chunk,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True
        )
        try:
            metadata_list = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            metadata_list = []
        for m in metadata_list:
            metadata_by_name[m.get('FileName', '')] = m
    return metadata_by_name


def write_exif_dates_batch(file_date_map, attributes, error_log):
    """Write date attributes for all files in a single exiftool -csv call."""
    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(['SourceFile'] + attributes)
    for file_path, date_time in file_date_map.items():
        date_str = date_time.strftime("%Y:%m:%d %H:%M:%S")
        writer.writerow([file_path] + [date_str] * len(attributes))

    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False,
                                     encoding='utf-8', newline='') as tmp:
        tmp.write(csv_buffer.getvalue())
        tmp_path = tmp.name

    try:
        subprocess.run(
            [EXE, f'-csv={tmp_path}', '-overwrite_original'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=600
        )
    except subprocess.TimeoutExpired:
        error_log.write("Timeout during batch metadata write\n")
    finally:
        os.unlink(tmp_path)


# --- Main rename workflow ---
def rename_photos(directory):
    if not directory:
        print("No directory selected. Exiting.")
        return

    files = sorted(f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f)))
    if not files:
        print("No files found.")
        return

    file_paths = [os.path.join(directory, f) for f in files]
    metadata_by_name = get_all_metadata(file_paths)

    files_renamed_count = 0
    new_file_names = {}

    for file in files:
        file_path = os.path.join(directory, file)
        file_metadata = metadata_by_name.get(file, {})
        date_time = extract_best_date(file_metadata, file_path)

        if not date_time:
            print(f"Unable to extract date from {file}. Skipping.")
            continue

        new_file_name_base = date_time.strftime('%Y-%m-%d %H.%M.%S')
        new_file_names[new_file_name_base] = new_file_names.get(new_file_name_base, 0) + 1

        new_ext = file_metadata.get('FileTypeExtension', os.path.splitext(file)[1].replace('.', ''))
        new_file_name = f"{new_file_name_base}_{new_file_names[new_file_name_base]}.{new_ext.lower()}"
        new_path = os.path.join(directory, new_file_name)

        os.rename(file_path, new_path)
        files_renamed_count += 1

        if files_renamed_count % 50 == 0:
            print('Files renamed:', files_renamed_count)

    print(f"{files_renamed_count} files have been renamed.")


# --- Write EXIF dates from filename ---
def change_exif_date(directory: str):
    if not directory:
        print("No directory selected. Exiting.")
        return

    files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
    file_paths = [os.path.join(directory, f) for f in files]
    metadata_by_name = get_all_metadata(file_paths)

    image_file_date_map = {}
    video_file_date_map = {}

    with open("error_log.txt", 'w') as error_log:
        for file in files:
            file_path = os.path.join(directory, file)
            file_metadata = metadata_by_name.get(file, {})

            try:
                date_time = datetime.strptime(file[:16], '%Y-%m-%d %H.%M')
            except Exception:
                print(f"Error parsing datetime from filename: {file}. Skipping.")
                continue

            ext = file_metadata.get("FileTypeExtension", "").lower()
            if ext in IMAGE_FILE_EXTENSIONS:
                image_file_date_map[file_path] = date_time
            elif ext in VIDEO_FILE_EXTENSIONS:
                video_file_date_map[file_path] = date_time
            else:
                print(f"Invalid file type: {file}. Skipping.")

        if image_file_date_map:
            print(f"Writing metadata for {len(image_file_date_map)} image files...")
            write_exif_dates_batch(image_file_date_map, IMAGE_FILE_DATE_ATTRIBUTES, error_log)

        if video_file_date_map:
            print(f"Writing metadata for {len(video_file_date_map)} video files...")
            write_exif_dates_batch(video_file_date_map, VIDEO_FILE_DATE_ATTRIBUTES, error_log)

    total = len(image_file_date_map) + len(video_file_date_map)
    print(f"{total} files have been updated.")


# --- Entry point ---
if __name__ == "__main__":
    directory = choose_directory()
    editing_exif_not_name = input("Are you renaming files from date metadata (0) "
                                  "or writing metadata from filename (1): ")

    if editing_exif_not_name == '0':
        rename_photos(directory)
    else:
        change_exif_date(directory)
