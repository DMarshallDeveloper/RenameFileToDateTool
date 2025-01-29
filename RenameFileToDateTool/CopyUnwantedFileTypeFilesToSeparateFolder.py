import os
import shutil
import tkinter as tk
from tkinter import filedialog

# File extensions to search for
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
    # Open file dialog to select folder
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory(title="Select the Root Folder to Search")

    if folder:
        copy_files_to_root(folder)
    else:
        print("No folder selected.")
