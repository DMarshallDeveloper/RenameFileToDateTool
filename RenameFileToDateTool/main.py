import os
from datetime import datetime
from tkinter import filedialog
from tkinter import Tk
from PIL import Image
from PIL import ExifTags
from PIL.ExifTags import TAGS
import subprocess

def choose_directory():
    root = Tk()
    root.withdraw()  # Hide the main window

    # Ask the user to select a directory
    directory = filedialog.askdirectory(title="Select Photos Directory")
    return directory

# def get_date_taken_from_exif(file_path):
#     try:
#         with Image.open(file_path) as img:
#             exif_data = img._getexif()
#             if exif_data is not None:
#                 for tag, value in exif_data.items():
#                     tag_name = TAGS.get(tag, tag)
#                     if tag_name == 'DateTimeOriginal':
#                         return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
#     except Exception as e:
#         print(f"Error extracting Date Taken from {file_path}: {e}")
#     return None
#
# def get_media_created_from_video(file_path):
#     try:
#         print(f"Getting media created from {file_path}")
#         properties = propsys.SHGetPropertyStoreFromParsingName(file_path)
#         print('properties: ', properties)
#         dt = properties.GetValue(pscon.PKEY_Media_DateEncoded).GetValue()
#         media_created = datetime.fromtimestamp(dt)
#         return media_created
#     except Exception as e:
#         print(f"Error extracting Media Created from {file_path}: {e}")
#     return None

def rename_photos(directory, date_option):
    if not directory:
        print("No directory selected. Exiting.")
        return

    # Get all files in the directory
    files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]

    # Counter for the number of files renamed
    files_renamed_count = 0

    for file in files:


        exe = ""
        process = subprocess.Popen([exe, file], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        for output in process.STDOUT:
            print(output.strip())




        print('file:', file)
        print('dateoption:', date_option)
        if date_option == 'dt' and file.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):  # Date Taken for photo files
            #date_time = get_date_taken_from_exif(os.path.join(directory, file))
            if date_time is None:
                print(f"Unable to extract Date Taken from {file}. Skipping.")
                continue
        elif date_option == 'dm':  # Date Modified for all files
            date_time = datetime.fromtimestamp(os.path.getmtime(os.path.join(directory, file)))
            print('Date Modified:', date_time)
        elif date_option == 'mc' and file.lower().endswith(('.mp4', '.avi', '.mov')):  # Media Created for video files
            #media_created = get_media_created_from_video(os.path.join(directory, file))
            if media_created is None:
                print(f"Unable to extract Media Created from {file}. Skipping.")
                continue
            date_time = media_created
            print('Media Created:', date_time)
        elif date_option == 'ofp':  # Original Filename Parsing
            # Placeholder for your custom parsing logic based on the variations in the filename format
            # For now, it assumes that the datetime information is in the first 19 characters of the filename
            datetime_str = file[:19]
            try:
                date_time = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                print(f"Error parsing datetime from filename: {file}. Skipping.")
                continue
        else:
            print("Invalid option or not applicable. Skipping.")
            continue

        # Create a new filename with the desired format (without seconds)
        new_file_name_base = date_time.strftime('%Y-%m-%d %H.%M')

        # Append the count and file extension to the new filename
        new_file_name = f"{new_file_name_base}_{files_renamed_count + 1}{os.path.splitext(file)[1]}"

        # Create the full path for the new file
        new_path = os.path.join(directory, new_file_name)

        # Rename the file
        os.rename(os.path.join(directory, file), new_path)

        # Increment the counter for files renamed
        files_renamed_count += 1

    print(f"{files_renamed_count} files have been renamed.")

if __name__ == "__main__":
    directory = choose_directory()

    date_option = input("Choose a date option for renaming "
                        "(dt: Date Taken / dm: Date Modified / mc: Media Created / ofp: Original Filename Parsing / of: Original Filename): ").lower()

    rename_photos(directory, date_option)
