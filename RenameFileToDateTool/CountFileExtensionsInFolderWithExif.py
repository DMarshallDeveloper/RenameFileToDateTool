"""CountFileExtensionsInFolderWithExif.py — like CountFileExtensionsInFolder,
but uses exiftool to ask "what format is this *really*?" instead of just trusting
the filename extension.

Different from the sibling script because a file named ``photo.jpg`` might
actually be HEIC bytes (common after iOS exports), and exiftool will tell you so.

Read-only.

Run with ``python CountFileExtensionsInFolderWithExif.py``.
"""

import collections
import subprocess

from photo_lib.binaries import EXIFTOOL
from photo_lib.tk_picker import choose_directory


def get_file_extensions(folder):
    cmd = [EXIFTOOL, '-ext', '*', '-FileTypeExtension', '-r', folder]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            encoding='utf-8', errors='replace')

    extensions = []
    for line in result.stdout.split("\n"):
        if line.startswith("File Type Extension"):
            ext = line.split(":")[-1].strip()
            if ext:
                extensions.append(ext)

    return extensions


def count_extensions():
    folder = choose_directory("Select Folder")
    if not folder:
        print("No folder selected. Exiting.")
        return

    extensions = get_file_extensions(folder)
    counter = collections.Counter(extensions)

    print(f"\nMost Common File Extensions in: {folder}")
    for ext, count in counter.most_common():
        print(f"{ext}: {count}")


if __name__ == "__main__":
    count_extensions()
