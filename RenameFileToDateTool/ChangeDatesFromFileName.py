import os
from datetime import datetime
from tkinter import filedialog, Tk
import subprocess
from dateutil import parser
import re

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

def process_exif_tool_command(tag, file_path, date_time, logging_file):
    exif_tool_argument = f'-{tag}="{date_time.strftime("%Y:%m:%d %H:%M:%S")}"'
    change_process = subprocess.Popen(
        [EXE, exif_tool_argument, file_path, "-overwrite_original"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True
    )
    try:
        change_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logging_file.write(f"Timeout when changing metadata {tag} of {file_path}\n")
        print(f"Timeout when changing metadata {tag} of {file_path}\n")

def extract_date_from_filename(filename):
    """Extracts a valid date from the filename by cleaning and parsing."""
    filename = filename.replace("_", " ")  # Replace underscores with spaces

    # Use regex to capture only the first valid datetime-like pattern before "_"
    match = re.search(r'\d{4}-\d{2}-\d{2} \d{2}.\d{2}.\d{2}', filename)

    if match:
        date_string = match.group(0).replace(".", ":")  # Convert "01.01.00" to "01:01:00"
        try:
            return parser.parse(date_string)
        except (ValueError, TypeError):
            return None
    return None  # Return None if no valid date is found

def change_exif_date(directory: str):
    if not directory:
        print("No directory selected. Exiting.")
        return

    files_updated = 0
    with open("logging_file.txt", 'w') as logging_file:
        for root, _, files in os.walk(directory):  # Recursively walk through directories
            for file in files:
                file_path = os.path.join(root, file)

                process = subprocess.Popen([EXE, file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                file_metadata = {}

                try:
                    for output in process.stdout:
                        line = output.strip().split(":", 1)
                        if len(line) == 2:
                            file_metadata[line[0].strip()] = line[1].strip()

                    date_time = extract_date_from_filename(file)

                    if not date_time:
                        logging_file.write(f"Error parsing datetime from filename: {file}. Skipping.\n")
                        print(f"Error parsing datetime from filename: {file}. Skipping.")
                        continue

                except Exception as e:
                    logging_file.write(f"Unexpected error with {file}: {str(e)}\n")
                    print(f"Unexpected error with {file}: {str(e)}")
                    continue

                file_extension = file_metadata.get('File Type Extension', '').lower()

                if file_extension in VALID_FILE_EXTENSIONS:
                    for tag in TAGS_TO_UPDATE:
                        process_exif_tool_command(tag, file_path, date_time, logging_file)
                else:
                    logging_file.write(f"Invalid file type: {file}. Skipping.\n")
                    print(f"Invalid file type: {file}. Skipping.")
                    continue

                files_updated += 1
                logging_file.write(f"Successfully processed: {file}.\n")
                print(f"Successfully processed: {file}.\n")
                if files_updated % 50 == 0:
                    logging_file.write(f"Files updated: {files_updated}\n")
                    print(f"Files updated: {files_updated}")

        logging_file.write(f"{files_updated} files have been updated.\n")
        print(f"{files_updated} files have been updated.")

if __name__ == "__main__":
    directory = choose_directory()
    change_exif_date(directory)
