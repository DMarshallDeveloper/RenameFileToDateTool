import subprocess
import os
import collections
import tkinter as tk
from tkinter import filedialog


def get_file_extensions(folder):
    # Run exiftool to get file types
    cmd = ['exiftool', '-ext', '*', '-FileTypeExtension', '-r', folder]
    result = subprocess.run(cmd, capture_output=True, text=True)

    # Process the output
    extensions = []
    for line in result.stdout.split("\n"):
        if line.startswith("File Type Extension"):
            ext = line.split(":")[-1].strip()  # Remove .lower() to keep case sensitivity
            if ext:
                extensions.append(ext)

    return extensions


def count_extensions():
    # Open file dialog to select folder
    root = tk.Tk()
    root.withdraw()  # Hide main Tkinter window
    folder = filedialog.askdirectory(title="Select Folder")

    if not folder:
        print("No folder selected. Exiting.")
        return

    extensions = get_file_extensions(folder)
    counter = collections.Counter(extensions)

    # Print results sorted by most common
    print(f"\nMost Common File Extensions in: {folder}")
    for ext, count in counter.most_common():
        print(f"{ext}: {count}")


if __name__ == "__main__":
    count_extensions()
