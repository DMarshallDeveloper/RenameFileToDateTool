"""ListAllFilesInFolder.py — print every filename under a folder (recursive).

Trivial helper for piping into other tools or eyeballing what's in a tree.
Prints just filenames, no paths. Recurses through all subfolders.

Run with ``python ListAllFilesInFolder.py``.
"""

import os

from photo_lib.tk_picker import choose_directory


def list_all_file_names(directory):
    for _root, _dirs, files in os.walk(directory):
        for file in files:
            print(file)


def main():
    folder_path = choose_directory("Select a folder")
    if folder_path:
        list_all_file_names(folder_path)
    else:
        print("No folder was selected.")


if __name__ == "__main__":
    main()
