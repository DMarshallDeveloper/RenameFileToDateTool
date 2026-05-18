"""FindFolderDifferences.py — print which files are in folder A but not B (and vice versa).

Quick sanity-check tool: pick two folders, get a list of files unique to each.
Compares filenames only (not contents) and only one level deep — doesn't recurse.

Useful for "did all my files copy across?" checks.

Run with ``python FindFolderDifferences.py``.
"""

import os

from photo_lib.tk_picker import choose_directory


def get_files(folder):
    """Returns a set of file names in the given folder."""
    return set(os.listdir(folder))


def compare_folders(folder1, folder2):
    """Compares two folders and prints files unique to each."""
    files1 = get_files(folder1)
    files2 = get_files(folder2)

    only_in_folder1 = files1 - files2
    only_in_folder2 = files2 - files1

    if only_in_folder1:
        print(f"Files only in {folder1}:")
        for file in only_in_folder1:
            print(f"  {file}")
    else:
        print(f"No unique files in {folder1}.")

    if only_in_folder2:
        print(f"Files only in {folder2}:")
        for file in only_in_folder2:
            print(f"  {file}")
    else:
        print(f"No unique files in {folder2}.")


if __name__ == "__main__":
    folder1 = choose_directory("Select the first folder")
    folder2 = choose_directory("Select the second folder")

    if folder1 and folder2 and os.path.isdir(folder1) and os.path.isdir(folder2):
        compare_folders(folder1, folder2)
    else:
        print("One or both paths are not valid directories.")
