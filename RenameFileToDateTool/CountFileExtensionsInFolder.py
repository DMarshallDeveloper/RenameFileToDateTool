import os
import collections
import tkinter as tk
from tkinter import filedialog


def get_file_extensions(folder):
    extensions = []

    # Walk through the directory and collect file extensions
    for root, _, files in os.walk(folder):
        for file in files:
            _, ext = os.path.splitext(file)
            if ext:
                extensions.append(ext.lstrip('.'))

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
