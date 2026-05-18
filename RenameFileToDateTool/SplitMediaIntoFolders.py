"""SplitMediaIntoFolders.py — break a big folder into 100-file batches.

iOS' Photos app won't reliably accept large bulk uploads from Google Drive. The
user's workaround is to slice a year folder into batches of 100, drop each batch
into Drive, and download them to the phone one at a time.

For each immediate subfolder of the picked root:
  - Non-media files get pushed into a ``non_media/`` sibling so they don't muddle
    the batch counts.
  - Media files get distributed across new subfolders named
    ``<parent>_01``, ``<parent>_02``, … each holding up to 100 files.

The recursive walk skips folders we've already created (``_NN`` split folders and
``non_media``) so a second run doesn't re-process them — see the regression test
``test_split_media_into_folders.test_non_media_files_go_to_non_media_folder``.

Run with ``python SplitMediaIntoFolders.py``.
"""

import argparse
import os
import re
import shutil
import logging

from photo_lib.extensions import is_media
from photo_lib.logging_setup import configure_logging
from photo_lib.tk_picker import resolve_directory

logger = logging.getLogger("photo_lib")

SPLIT_FOLDER_RE = re.compile(r'_\d{2}$')

# The non-media spillover folder name. Excluded from recursive descent so we don't
# re-process the files we just moved into it (which used to cause infinite nesting:
# year2024/non_media/non_media/non_media/... until the path-length limit killed it).
NON_MEDIA_FOLDER_NAME = 'non_media'

# Logging is configured via photo_lib.logging_setup.configure_logging() from __main__.


def is_media_file(file_path):
    return is_media(os.path.splitext(file_path)[1])

# Function to move files to a target directory
def move_file(file_path, target_dir):
    target_path = os.path.join(target_dir, os.path.basename(file_path))
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
    try:
        if os.path.exists(file_path):  # Double-check the file exists before moving
            shutil.move(file_path, target_path)
            logger.info(f"Moved: {file_path} -> {target_path}")
        else:
            logger.warning(f"File not found during move: {file_path}")
    except Exception as e:
        logger.error(f"Error moving file {file_path} to {target_dir}: {e}")

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


def process_folder_structure(root_folder):
    for root, dirs, _files in os.walk(root_folder):
        # Skip subfolders we created ourselves: split-suffix folders (e.g. "year2024_01")
        # and the non_media spillover. Without the non_media exclusion, the recursive walk
        # finds notes.txt inside year2024/non_media/, treats it as non-media again, and
        # moves it to year2024/non_media/non_media/, repeating until Windows hits the
        # path-length limit. See test_split_media_into_folders.py for the regression test.
        dirs[:] = [
            d for d in dirs
            if not SPLIT_FOLDER_RE.search(d) and d != NON_MEDIA_FOLDER_NAME
        ]

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
                    non_media_folder = os.path.join(subfolder_path, NON_MEDIA_FOLDER_NAME)
                    os.makedirs(non_media_folder, exist_ok=True)
                    for non_media_file in non_media_files:
                        move_file(non_media_file, non_media_folder)

                if media_files:
                    split_media_files(media_files, subfolder_path)

                logger.info(f"Processed folder: {subfolder_path}")

            except FileNotFoundError:
                logger.warning(f"Subfolder not found during processing: {subfolder_path}")
            except Exception as e:
                logger.error(f"Error processing subfolder {subfolder_path}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Split each subfolder of the picked root into 100-file batches.")
    parser.add_argument("--path", help="Root folder to organize. If omitted, opens the Tk folder picker.")
    args = parser.parse_args()

    configure_logging("split_media_into_folders")
    root_folder = resolve_directory(args.path, "Select the root folder to organize")
    if not root_folder:
        logger.warning("No folder selected. Exiting program.")
        return

    if not os.path.exists(root_folder):
        logger.error(f"Selected folder does not exist: {root_folder}")
        return

    try:
        logger.info(f"Selected folder: {root_folder}")
        process_folder_structure(root_folder)
        logger.info("Processing completed.")
    except Exception as e:
        logger.error(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
