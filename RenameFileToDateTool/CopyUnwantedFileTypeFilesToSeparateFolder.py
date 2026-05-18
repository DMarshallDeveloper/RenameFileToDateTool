"""CopyUnwantedFileTypeFilesToSeparateFolder.py — gather legacy-format files for review.

Walks the picked tree and *copies* (not moves) every ``.avi``, ``.3gp``, ``.gif``,
or ``.png`` into a ``Collected_Files/`` subfolder at the root. Useful as a
"show me everything I should consider converting" pass before running
``ConvertUnwantedFileTypesToDifferentFormat.py``.

Originals are untouched.

Run with ``python CopyUnwantedFileTypeFilesToSeparateFolder.py``.
"""

import os
import shutil

from photo_lib.tk_picker import choose_directory

# Narrowly scoped: legacy formats user wants collected for conversion. Don't reuse
# the canonical extension sets — this is intentionally a different list.
TARGET_EXTENSIONS = (".avi", ".3gp", ".gif", ".png")

def copy_files_to_root(folder):
    if not os.path.isdir(folder):
        print("Invalid folder path!")
        return

    # Create a new folder at the root level
    destination_folder = os.path.join(folder, "Collected_Files")
    os.makedirs(destination_folder, exist_ok=True)

    # Walk through all subdirectories
    for root, _, files in os.walk(folder):
        for file in files:
            if file.lower().endswith(TARGET_EXTENSIONS):
                source_path = os.path.join(root, file)
                destination_path = os.path.join(destination_folder, file)

                # Copy the file only if it doesn't already exist in the destination
                if not os.path.exists(destination_path):
                    shutil.copy2(source_path, destination_path)
                    print(f"Copied: {source_path} → {destination_path}")
                else:
                    print(f"Skipped (already exists): {destination_path}")

    print("All matching files have been copied!")

if __name__ == "__main__":
    folder = choose_directory("Select the Root Folder to Search")
    if folder:
        copy_files_to_root(folder)
    else:
        print("No folder selected.")
