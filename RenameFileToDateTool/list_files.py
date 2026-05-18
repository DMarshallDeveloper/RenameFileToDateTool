"""list_files.py — print every filename under a folder (recursive).

Trivial helper for piping into other tools or eyeballing what's in a tree.
Prints just filenames, no paths. Recurses through all subfolders.

Run with ``python list_files.py``.
"""

import argparse
import os

from photo_lib.tk_picker import resolve_directory


def list_all_file_names(directory):
    for _root, _dirs, files in os.walk(directory):
        for file in files:
            print(file)


def main():
    parser = argparse.ArgumentParser(description="Print every filename under a folder (recursive).")
    parser.add_argument("--path", help="Folder to list. If omitted, opens the Tk folder picker.")
    args = parser.parse_args()

    folder_path = resolve_directory(args.path, "Select a folder")
    if folder_path:
        list_all_file_names(folder_path)
    else:
        print("No folder was selected.")


if __name__ == "__main__":
    main()
