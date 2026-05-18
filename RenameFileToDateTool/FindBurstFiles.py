"""FindBurstFiles.py — list any iOS burst-mode photos in a folder.

iOS' burst mode tags each photo with a shared ``BurstUUID`` so the iPhone can
group them later. This script just lists every file in the picked folder that
has that tag set — useful when reviewing whether to trim down a burst.

Read-only diagnostic.

Run with ``python FindBurstFiles.py``.
"""

import argparse
import json
import subprocess

from photo_lib.binaries import EXIFTOOL
from photo_lib.tk_picker import resolve_directory

EXIFTOOL_PATH = EXIFTOOL  # back-compat alias
BURST_TAG = "BurstUUID"


def scan_folder(directory):
    """Scans a folder for files with the BurstUUID tag using a single exiftool call."""
    if not directory:
        print("No folder selected. Exiting.")
        return

    print(f"\nScanning for BurstUUID in: {directory}\n")

    result = subprocess.run(
        [EXIFTOOL_PATH, f'-{BURST_TAG}', '-json', '-r', directory],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding='utf-8', errors='replace'
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
    parser = argparse.ArgumentParser(description="List files with iOS BurstUUID metadata.")
    parser.add_argument("--path", help="Folder to scan. If omitted, opens the Tk folder picker.")
    args = parser.parse_args()

    directory = resolve_directory(args.path, "Select Folder to Scan")
    scan_folder(directory)
