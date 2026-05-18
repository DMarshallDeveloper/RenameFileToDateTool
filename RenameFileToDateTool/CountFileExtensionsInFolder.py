"""CountFileExtensionsInFolder.py — quick "how many of each format do I have?"

Recursively walks the picked folder and prints a count of each file extension,
sorted by frequency. Useful for spotting weird formats lurking in the library
(``.aee``, ``.heic``, ``.HEIC`` vs ``.heic`` casing, etc).

Uses just the filename's extension — see ``CountFileExtensionsInFolderWithExif.py``
for the more accurate version that asks exiftool what the file *actually* is.

Run with ``python CountFileExtensionsInFolder.py``.
"""

import argparse
import collections
import os

from photo_lib.tk_picker import resolve_directory


def get_file_extensions(folder):
    extensions = []
    for root, _, files in os.walk(folder):
        for file in files:
            _, ext = os.path.splitext(file)
            if ext:
                extensions.append(ext.lstrip('.'))
    return extensions


def count_extensions(folder=None):
    if folder is None:
        folder = resolve_directory(None, "Select Folder")
    if not folder:
        print("No folder selected. Exiting.")
        return

    extensions = get_file_extensions(folder)
    counter = collections.Counter(extensions)

    print(f"\nMost Common File Extensions in: {folder}")
    for ext, count in counter.most_common():
        print(f"{ext}: {count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Count file extensions in a folder (recursive).")
    parser.add_argument("--path", help="Folder to scan. If omitted, opens the Tk folder picker.")
    args = parser.parse_args()

    count_extensions(folder=resolve_directory(args.path, "Select Folder"))
