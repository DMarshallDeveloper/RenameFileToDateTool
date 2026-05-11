import csv
import io
import os
import re
import subprocess
import tempfile
from datetime import datetime
from tkinter import filedialog, Tk
from dateutil import parser

IMAGE_FILE_EXTENSIONS = ["jpg", "jpeg", "png", "gif", "heic"]
VIDEO_FILE_EXTENSIONS = ["avi", "mpg", "mp4", "mov", "3gp"]
TAGS_TO_UPDATE = [
    "AllDates", "FileCreateDate", "FileModifyDate",
    "TrackCreateDate", "TrackModifyDate",
    "MediaCreateDate", "MediaModifyDate",
    "CreateDate", "ModifyDate", "DateTimeOriginal",
    "RecordingTime", "ExifCreateDate", "ExifModifyDate",
    "EncodedDate", "GPSDateStamp", "TimeCreated", "DateTime"
]

VALID_FILE_EXTENSIONS = IMAGE_FILE_EXTENSIONS + VIDEO_FILE_EXTENSIONS
EXE = "exiftool.exe"


def choose_directory():
    root = Tk()
    root.withdraw()
    return filedialog.askdirectory(title="Select Photos Directory")


def write_exif_dates_batch(file_date_map, logging_file):
    """Write all date tags for all files in a single exiftool -csv call."""
    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(['SourceFile'] + TAGS_TO_UPDATE)
    for file_path, date_time in file_date_map.items():
        date_str = date_time.strftime("%Y:%m:%d %H:%M:%S")
        writer.writerow([file_path] + [date_str] * len(TAGS_TO_UPDATE))

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
        logging_file.write("Timeout during batch metadata write\n")
        print("Timeout during batch metadata write")
    finally:
        os.unlink(tmp_path)


def extract_date_from_filename(filename):
    """Extracts a valid date from the filename by cleaning and parsing."""
    filename = filename.replace("_", " ")
    match = re.search(r'\d{4}-\d{2}-\d{2} \d{2}.\d{2}.\d{2}', filename)
    if match:
        date_string = match.group(0).replace(".", ":")
        try:
            return parser.parse(date_string)
        except (ValueError, TypeError):
            return None
    return None


def change_exif_date(directory: str):
    if not directory:
        print("No directory selected. Exiting.")
        return

    file_date_map = {}
    with open("logging_file.txt", 'w') as logging_file:
        for root, _, files in os.walk(directory):
            for file in files:
                file_path = os.path.join(root, file)

                date_time = extract_date_from_filename(file)
                if not date_time:
                    logging_file.write(f"Error parsing datetime from filename: {file}. Skipping.\n")
                    print(f"Error parsing datetime from filename: {file}. Skipping.")
                    continue

                file_extension = os.path.splitext(file)[1].lower().lstrip('.')
                if file_extension not in VALID_FILE_EXTENSIONS:
                    logging_file.write(f"Invalid file type: {file}. Skipping.\n")
                    print(f"Invalid file type: {file}. Skipping.")
                    continue

                file_date_map[file_path] = date_time

        if file_date_map:
            print(f"Writing metadata for {len(file_date_map)} files...")
            write_exif_dates_batch(file_date_map, logging_file)
            for file_path in file_date_map:
                logging_file.write(f"Successfully processed: {os.path.basename(file_path)}.\n")

        logging_file.write(f"{len(file_date_map)} files have been updated.\n")
        print(f"{len(file_date_map)} files have been updated.")


if __name__ == "__main__":
    directory = choose_directory()
    change_exif_date(directory)
