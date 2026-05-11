import json
import subprocess
from tkinter import filedialog, Tk

EXIFTOOL_PATH = "exiftool.exe"
BURST_TAG = "BurstUUID"


def choose_directory():
    """Opens a dialog box for the user to select a folder."""
    root = Tk()
    root.withdraw()
    return filedialog.askdirectory(title="Select Folder to Scan")


def scan_folder(directory):
    """Scans a folder for files with the BurstUUID tag using a single exiftool call."""
    if not directory:
        print("No folder selected. Exiting.")
        return

    print(f"\nScanning for BurstUUID in: {directory}\n")

    result = subprocess.run(
        [EXIFTOOL_PATH, f'-{BURST_TAG}', '-json', '-r', directory],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True
    )

    try:
        metadata_list = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        metadata_list = []

    burst_files = [m['SourceFile'] for m in metadata_list if m.get(BURST_TAG)]

    if burst_files:
        for f in burst_files:
            print(f"BurstUUID found: {f}")
    else:
        print("No files with BurstUUID found.")


if __name__ == "__main__":
    directory = choose_directory()
    scan_folder(directory)
