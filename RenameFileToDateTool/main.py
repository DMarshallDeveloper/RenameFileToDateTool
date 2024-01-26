import os
from datetime import datetime
from tkinter import filedialog
from tkinter import Tk
import subprocess
from dateutil import parser

def choose_directory():
    root = Tk()
    root.withdraw()  # Hide the main window

    # Ask the user to select a directory
    directory = filedialog.askdirectory(title="Select Photos Directory")
    return directory

def rename_photos(directory, date_option, date_option_dict, exe, custom_filename):
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
        print(file)
        print(directory)
        print(file_path)
        process = subprocess.Popen([exe, file_path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=True)
        file_metadata = {}
        for output in process.stdout:
            print(output)
            info = {}
            line = (output.strip().split(":", 1))
            info[line[0].strip()] = line[1].strip()
            file_metadata.update(info)

        if (date_option == 'dt' and file.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')))\
                or (date_option == 'mc' and file.lower().endswith(('.mp4', '.avi', '.mov')))\
                or date_option == 'dm':  # If reading file metadata

            date_time_string : str = file_metadata[date_option_dict[date_option]]
            date_time_string = date_time_string.strip().split("+")[0]
            date_time = datetime.strptime(date_time_string, '%Y:%m:%d %H:%M:%S')

            if date_time is None:
                print(f"Unable to extract {date_option} from {file}. Skipping.")
                continue

        elif date_option == 'ofp':  # Original Filename Parsing
            date_time_string = file_metadata['File Name'].strip().rsplit('.')[0]
            try:
                date_time = parser.parse(date_time_string)
            except ValueError:
                print(f"Error parsing datetime from filename: {file}. Skipping.")
                continue
        elif date_option != 'cf':
            print("Invalid option or not applicable. Skipping.")
            continue

        # Create a new filename with the desired format (without seconds)

        if date_option == 'cf':
            new_file_name_base = custom_filename
        else:
            new_file_name_base = date_time.strftime('%Y-%m-%d %H.%M')

        if new_file_name_base not in new_file_names:
            new_file_names[new_file_name_base] = 0
        new_file_names[new_file_name_base] += 1

        # Append the count and file extension to the new filename
        new_file_name = f"{new_file_name_base}_{new_file_names[new_file_name_base]}.{file_metadata['File Type Extension']}"

        # Create the full path for the new file
        new_path = os.path.join(directory, new_file_name)

        # Rename the file
        os.rename(os.path.join(directory, file), new_path)

        # Increment the counter for files renamed
        files_renamed_count += 1

    print(f"{files_renamed_count} files have been renamed.")

if __name__ == "__main__":
    directory = choose_directory()
    exe = "exiftool.exe"
    custom_filename = ''
    date_option_dict = {
        "dt": "Date/Time Original",
        "dm": "File Modification Date/Time",
        "mc": "Media Create Date",
        "ofn": "Original Filename"
    }
    date_option = input("Choose a date option for renaming "
                        "(dt: Date Taken / dm: Date Modified / mc: Media Created / ofn: Parsed from Original Filename / cf: New Custom Filename (all files named 'custom filename - 1, -2' etc.) : ").lower()


    if date_option == "cf":
        custom_filename = input("Choose a custom filename for renaming: ")


    # filename = "2019-03-30 17-41-02.jpeg"
    # filename = "20200216_074818_IMG_9669.JPG"
    # date_time_string = filename.strip().rsplit('.')[0]
    # print('date_time_string:', date_time_string)
    # try:
    #     date_time = parser.parse(date_time_string)
    # except ValueError:
    #     print(f"Error parsing datetime from filename: {filename}. Skipping.")
    #
    # print('date_time:', date_time)
    #
    # print(date_time.strftime('%Y-%m-%d %H.%M'))

    # file = "C:/Users/hunua/Downloads/gpt trial/by media created tag/2024-01-26 00.07_4.MOV"
    #file = "C:/Users/hunua/Downloads/gpt trial/2000 - 2010 - 1.jpg"
    # file = "C:/Users/hunua/Downloads/gpt trial/date taken/IMG_1593 (3).JPG"
    # process = subprocess.Popen([exe, file], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    #                            universal_newlines=True)
    # file_metadata = {}
    # for output in process.stdout:
    #     print(output)
    #     info = {}
    #     line = (output.strip().split(":", 1))
    #     info[line[0].strip()] = line[1].strip()
    #     file_metadata.update(info)
    #
    # for each in file_metadata:
    #     print(each)
    #
    # print("end")
    #
    # print(info)
    # print(file_metadata['Media Create Date'])

    rename_photos(directory, date_option, date_option_dict, exe, custom_filename)
