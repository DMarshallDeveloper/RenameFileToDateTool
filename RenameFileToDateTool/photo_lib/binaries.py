"""Absolute paths to the bundled ``exiftool.exe`` / ``ffmpeg.exe`` / ``ffprobe.exe``.

Binaries live in ``RenameFileToDateTool/bin/``. Centralizing the path here means
callers don't need to know the layout — moving the bin/ folder is a one-line edit.
"""

import os

# photo_lib lives at RenameFileToDateTool/photo_lib — siblings of the bin/ folder
_PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_DIR = os.path.join(_PACKAGE_PARENT, "bin")

EXIFTOOL = os.path.join(BIN_DIR, "exiftool.exe")
FFMPEG = os.path.join(BIN_DIR, "ffmpeg.exe")
FFPROBE = os.path.join(BIN_DIR, "ffprobe.exe")
