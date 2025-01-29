import os
import subprocess
from tkinter import filedialog, Tk

EXIFTOOL_PATH = "exiftool.exe"  # Ensure this is in the same directory or system PATH
BURST_TAG = "BurstUUID"
BURST_TAG_SENTENCE = "Burst UUID"


def choose_directory():
    """Opens a dialog box for the user to select a folder."""
    root = Tk()
    root.withdraw()
    return filedialog.askdirectory(title="Select Folder to Scan")


def has_burst_uuid(file_path):
    """Checks if a file has the BurstUUID EXIF tag."""
    try:
        process = subprocess.Popen(
            [EXIFTOOL_PATH, "-{}".format(BURST_TAG), file_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True
        )
        output, _ = process.communicate()
        return BURST_TAG_SENTENCE in output  # If BurstUUID exists in the output, return True
    except Exception as e:
        print(f"Error checking {file_path}: {e}")
        return False


def scan_folder(directory):
    """Recursively scans a folder for files with the BurstUUID tag."""
    if not directory:
        print("No folder selected. Exiting.")
        return

    print(f"\n🔍 Scanning for BurstUUID in: {directory}\n")
    burst_files = []

    for root, _, files in os.walk(directory):  # Recursively traverse folders
        for file in files:
            file_path = os.path.join(root, file)

            if has_burst_uuid(file_path):
                print(f"✅ BurstUUID found: {file_path}")
                burst_files.append(file_path)

    if not burst_files:
        print("❌ No files with BurstUUID found.")


if __name__ == "__main__":
    directory = choose_directory()
    scan_folder(directory)
