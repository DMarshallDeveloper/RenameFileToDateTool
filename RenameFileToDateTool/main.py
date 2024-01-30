import os
from datetime import datetime
from tkinter import filedialog, Tk
import subprocess
from dateutil import parser

IMAGE_FILE_EXTENSIONS = ["jpg", "jpeg", "png", "gif", "heic"]
VIDEO_FILE_EXTENSIONS = ["avi", "mpg", "mp4", "mov"]
IMAGE_FILE_DATE_ATTRIBUTES = ["DateTimeOriginal", "CreateDate", "DateCreated", "ModifyDate"]
VIDEO_FILE_DATE_ATTRIBUTES = ["CreateDate", "ModifyDate", "TrackCreateDate", "TrackModifyDate", "MediaCreateDate",
                              "MediaModifyDate"]
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


def choose_directory():
    root = Tk()
    root.withdraw()  # Hide the main window

    # Ask the user to select a directory
    directory = filedialog.askdirectory(title="Select Photos Directory")
    return directory


def rename_file_setup(directory):
    custom_date = ''
    current_date_format = ''
    date_option_dict = {
        "dt": "Date/Time Original",
        "dm": "File Modification Date/Time",
        "mc": "Media Create Date",
        "ofn": "Original Filename"
    }
    date_option = input("Choose a date option for renaming "
                        "(dt: Date Taken / dm: Date Modified / mc: Media Created / ofn: Parsed from Original Filename "
                        "/ cd: New default date: 01/01/year) : ").lower()

    if date_option == "cd":
        custom_date = input("Choose a year: ")

    if date_option == "ofn" and input("Do you have the format for the date in the filename? (y/n): ").lower() == 'y':
        current_date_format = input(
            "Please enter the format for the date, see https://docs.python.org/3/library/datetime.html#format-codes "
            "for details")

    rename_photos(directory, date_option, date_option_dict, EXE, custom_date, current_date_format)


def rename_photos(directory, date_option, date_option_dict, exe, custom_date, current_date_format):
    if not directory:
        print("No directory selected. Exiting.")
        return

    # Get all files in the directory
    files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]

    # Counter for the number of files renamed
    files_renamed_count = 0

    new_file_names = {}

    for file in files:
        file_path = directory + "/" + file
        process = subprocess.Popen([exe, file_path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=True)
        file_metadata = {}
        for output in process.stdout:
            info = {}
            line = (output.strip().split(":", 1))
            info[line[0].strip()] = line[1].strip()
            file_metadata.update(info)

        if (date_option == 'dt' and file.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.heic'))) \
                or (date_option == 'mc' and file.lower().endswith(('.mp4', '.avi', '.mov'))) \
                or date_option == 'dm':  # If reading file metadata

            date_time_string: str = file_metadata[date_option_dict[date_option]]
            date_time_string = date_time_string.strip().split("+")[0]
            date_time_string = date_time_string.strip().split(".")[0]
            try:
                date_time = datetime.strptime(date_time_string, '%Y:%m:%d %H:%M:%S')
            except ValueError:
                print(f"Error parsing datetime from : {file}. Skipping.")
                continue

            if date_time is None:
                print(f"Unable to extract {date_option} from {file}. Skipping.")
                continue

        elif date_option == 'ofp':  # Original Filename Parsing
            # format 1
            # date_time_string = file_metadata['File Name'].strip().rsplit('.')[0][0:13]
            # format 2
            # date_time_string = file_metadata['File Name'].strip().rsplit('.')[0][0:16]
            # format 3
            # date_time_string = file_metadata['File Name'].strip().rsplit('.')[0][11:24]
            # format 4
            # date_time_string = file_metadata['File Name'].strip().rsplit('.')[0][4:17]
            print(date_time_string)
            try:
                # format 1
                # date_time = datetime.strptime(date_time_string, '%Y%m%d_%H%M')
                # format 2
                # date_time = datetime.strptime(date_time_string, '%Y-%m-%d %H-%M')
                # format 3
                # date_time = datetime.strptime(date_time_string, '%Y%m%d-%H%M')
                # format 4
                # date_time = datetime.strptime(date_time_string, '%Y%m%d_%H%M')
                # format custom
                date_time = datetime.strptime(date_time_string, current_date_format)
                print(date_time)
            except ValueError:
                print(f"Error parsing datetime from filename: {file}. Skipping.")
                continue
        elif date_option != 'cd':
            print("Invalid option or not applicable. Skipping.")
            continue

        # Create a new filename with the desired format (without seconds)

        if date_option == 'cd':
            new_file_name_base = datetime(int(custom_date), 1, 1).strftime('%Y-%m-%d %H.%M')
        elif date_time is not None:
            new_file_name_base = date_time.strftime('%Y-%m-%d %H.%M')
        else:
            print(f"Error finding datetime in file: {file}. Skipping.")
            continue

        if new_file_name_base not in new_file_names:
            new_file_names[new_file_name_base] = 0
        new_file_names[new_file_name_base] += 1

        # Append the count and file extension to the new filename
        new_file_name = f"{new_file_name_base}_{new_file_names[new_file_name_base]}.{file_metadata['File Type Extension']}"

        print(file_metadata['File Type Extension'])

        # Create the full path for the new file
        new_path = os.path.join(directory, new_file_name)

        # Rename the file
        os.rename(os.path.join(directory, file), new_path)

        # Increment the counter for files renamed
        files_renamed_count += 1

        if files_renamed_count % 50 == 0:
            print('files renamed: ', files_renamed_count)

    print(f"{files_renamed_count} files have been renamed.")


def process_exif_tool_command(attribute, old_file_metadata, file_path, date_time, error_log):
    exif_tool_argument = '-' + attribute + '="' + date_time.strftime("%Y:%m:%d %H:%M:%S") + '+12:00"'
    try:
        print('old ' + ATTRIBUTE_TO_EXIF_NAME_DICT[attribute] + ' value is: ' + old_file_metadata[
            ATTRIBUTE_TO_EXIF_NAME_DICT[attribute]])
    except KeyError:
        print("No old value of " + attribute + "was found")
    change_process = subprocess.Popen([EXE, exif_tool_argument, file_path, "-overwrite_original"],
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT,
                                      universal_newlines=True)
    try:
        change_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        error_log.write("Timeout when changing metadata " + attribute + " of " + file_path + "\n")

    data_process = subprocess.Popen([EXE, file_path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    universal_newlines=True)

    try:
        data_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        error_log.write("Timeout when reading updated metadata from " + file_path + "\n")

    updated_file_metadata = {}
    for output in data_process.stdout:
        info = {}
        line = (output.strip().split(":", 1))
        info[line[0].strip()] = line[1].strip()
        updated_file_metadata.update(info)

    try:
        print('new ' + ATTRIBUTE_TO_EXIF_NAME_DICT[attribute] + ' value is: ' + updated_file_metadata[
            ATTRIBUTE_TO_EXIF_NAME_DICT[attribute]])
    except KeyError:
        print("No new value of " + attribute + "was found")


def change_exif_date(directory: str):
    if not directory:
        print("No directory selected. Exiting.")
        return

    # Get all files in the directory
    files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]

    # Counter for the number of files updated
    files_updated = 0
    error_log = open("C:/Users/hunua/Downloads/error_log.txt", 'w')
    for file in files:
        file_path = directory + "/" + file
        # print('\n' + file_path)
        process = subprocess.Popen([EXE, file_path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=True)

        file_metadata = {}
        for output in process.stdout:
            info = {}
            line = (output.strip().split(":", 1))
            info[line[0].strip()] = line[1].strip()
            file_metadata.update(info)
        date_time_string = file_metadata['File Name'][0:16]
        try:
            date_time = datetime.strptime(date_time_string, '%Y-%m-%d %H.%M')
        except ValueError:
            print(f"Error parsing datetime from filename: {file}. Skipping.")
            continue

        # if file_metadata['File Type Extension'].lower() in IMAGE_FILE_EXTENSIONS:
        #     for attribute in IMAGE_FILE_DATE_ATTRIBUTES:
        #         process_exif_tool_command(attribute, file_metadata, file_path, date_time, error_log)
        #
        # elif file_metadata['File Type Extension'].lower() in VIDEO_FILE_EXTENSIONS:
        #     for attribute in VIDEO_FILE_DATE_ATTRIBUTES:
        #         process_exif_tool_command(attribute, file_metadata, file_path, date_time, error_log)
        # else:
        #     print(f"Invalid file type: {file}. Skipping.")
        #     continue

        exif_tool_arguments = 'exiftool.exe -time:all="' + date_time.strftime("%Y:%m:%d %H:%M:%S") + '" -wm w -overwrite_original "' + file_path + '"'
        change_process = subprocess.Popen(exif_tool_arguments,
                                          stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT,
                                          universal_newlines=True)

        # Increment the counter for files renamed
        files_updated += 1

        if files_updated % 50 == 0:
            print('files updated: ', files_updated)

    print(f"{files_updated} files have been updated.")


if __name__ == "__main__":
    directory = choose_directory()
    editing_exif_not_name = input("Are you editing the name of the file from the date (0) "
                                  "or saving the date from the name (1): ")

    if editing_exif_not_name == '0':
        rename_file_setup(directory)
    else:
        change_exif_date(directory)
