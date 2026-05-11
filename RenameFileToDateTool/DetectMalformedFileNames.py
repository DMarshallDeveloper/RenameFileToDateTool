import os
import re
from tkinter import filedialog, Tk


def choose_directory():
    root = Tk()
    root.withdraw()  # Hide the main window

    # Ask the user to select a directory
    directory = filedialog.askdirectory(title="Select Photos Directory")
    return directory


def check_filenames(directory):
    # Define the regex pattern to match
    pattern = r'^\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2}_\d+\.[a-zA-Z0-9]{3,4}$'
    # Traverse the folder and subfolders
    for dirpath, _, filenames in os.walk(directory):
        for filename in filenames:
            # Check if the filename matches the regex pattern
            if not re.match(pattern, filename):
                # Print the full path of the file if it does not match
                full_path = os.path.join(dirpath, filename)
                print(f"Filename does not match pattern: {full_path}")

# This program finds any filenames not in the datetime format YYYY-MM-dd HH.mm.ss_{number}.{extension}
if __name__ == "__main__":
    directory = choose_directory()
    check_filenames(directory)
