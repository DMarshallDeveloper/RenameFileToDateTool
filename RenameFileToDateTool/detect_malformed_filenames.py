"""detect_malformed_filenames.py — print any filenames that drifted off-spec.

The master library convention is ``YYYY-MM-DD HH.MM.SS_N.ext`` (3- or 4-char
extension). This script walks the picked folder and prints the full path of any
file that doesn't match that exact shape — usually a file you forgot to rename
or one that came from a script that used an older naming format.

Read-only. Use the output as a worklist for ``write_exif_from_filename.py`` or
``write_exif_from_filename.py``.

Run with ``python detect_malformed_filenames.py``.
"""

import argparse
import os

from photo_lib.filename_pattern import CANONICAL_FILENAME_RE
from photo_lib.tk_picker import resolve_directory


def check_filenames(directory):
    """Walk the directory and print every file whose name doesn't match the master
    library's ``YYYY-MM-DD HH.MM.SS_N.ext`` convention."""
    for dirpath, _, filenames in os.walk(directory):
        for filename in filenames:
            if not CANONICAL_FILENAME_RE.match(filename):
                full_path = os.path.join(dirpath, filename)
                print(f"Filename does not match pattern: {full_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", help="Directory to scan. If omitted, opens the Tk folder picker.")
    args = parser.parse_args()

    directory = resolve_directory(args.path, "Select Photos Directory")
    check_filenames(directory)
