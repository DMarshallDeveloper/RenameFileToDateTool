import os
import re
import shutil
import logging
import tkinter as tk
from tkinter import filedialog

SPLIT_FOLDER_RE = re.compile(r'_\d{2}$')

MEDIA_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.heic', '.heif',
    '.mp4', '.mov', '.m4v', '.avi', '.mpg', '.mpeg',
    '.3gp', '.mkv', '.wmv'
}

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def is_media_file(file_path):
    return os.path.splitext(file_path)[1].lower() in MEDIA_EXTENSIONS

# Function to move files to a target directory
def move_file(file_path, target_dir):
    target_path = os.path.join(target_dir, os.path.basename(file_path))
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
    try:
        if os.path.exists(file_path):  # Double-check the file exists before moving
            shutil.move(file_path, target_path)
            logging.info(f"Moved: {file_path} -> {target_path}")
        else:
            logging.warning(f"File not found during move: {file_path}")
    except Exception as e:
        logging.error(f"Error moving file {file_path} to {target_dir}: {e}")

# Function to split media files into subfolders (up to 100 files per folder)
def split_media_files(media_files, parent_folder):
    folder_count = 1
    base_folder_name = os.path.basename(parent_folder)
    current_folder = os.path.join(parent_folder, f"{base_folder_name}_{folder_count:02}")
    os.makedirs(current_folder, exist_ok=True)

    current_file_count = 0

    for media_file in media_files:
        if current_file_count >= 100:
            folder_count += 1
            current_folder = os.path.join(parent_folder, f"{base_folder_name}_{folder_count:02}")
            os.makedirs(current_folder, exist_ok=True)
            current_file_count = 0

        move_file(media_file, current_folder)
        current_file_count += 1


# Function to process all subfolders
def process_folder_structure(root_folder):
    for root, dirs, files in os.walk(root_folder):
        # Filter out subfolders that match the split naming pattern
        dirs[:] = [d for d in dirs if not SPLIT_FOLDER_RE.search(d)]

        for dir_name in dirs:
            subfolder_path = os.path.join(root, dir_name)
            try:
                media_files = []
                non_media_files = []

                for file_name in os.listdir(subfolder_path):
                    file_path = os.path.join(subfolder_path, file_name)
                    if os.path.isfile(file_path):
                        if is_media_file(file_path):
                            media_files.append(file_path)
                        else:
                            non_media_files.append(file_path)

                if non_media_files:
                    non_media_folder = os.path.join(subfolder_path, 'non_media')
                    os.makedirs(non_media_folder, exist_ok=True)
                    for non_media_file in non_media_files:
                        move_file(non_media_file, non_media_folder)

                if media_files:
                    split_media_files(media_files, subfolder_path)

                logging.info(f"Processed folder: {subfolder_path}")

            except FileNotFoundError:
                logging.warning(f"Subfolder not found during processing: {subfolder_path}")
            except Exception as e:
                logging.error(f"Error processing subfolder {subfolder_path}: {e}")


# Main function to select folder and process it
def main():
    root = tk.Tk()
    root.withdraw()  # Hide the main Tkinter window

    root_folder = filedialog.askdirectory(title="Select the root folder to organize")

    if not root_folder:
        logging.warning("No folder selected. Exiting program.")
        return

    if not os.path.exists(root_folder):
        logging.error(f"Selected folder does not exist: {root_folder}")
        return

    try:
        logging.info(f"Selected folder: {root_folder}")
        process_folder_structure(root_folder)
        logging.info("Processing completed.")
    except Exception as e:
        logging.error(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
