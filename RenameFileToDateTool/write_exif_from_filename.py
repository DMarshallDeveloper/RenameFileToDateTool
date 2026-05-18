"""write_exif_from_filename.py — write EXIF / QuickTime dates from each file's name.

The mirror image of ``rename_files_from_exif.py``: leaves the filenames alone
but rewrites the embedded metadata so it matches what the filename already
says. Useful when filenames are correct but EXIF dates are missing or wrong
(typical after a Google Takeout round-trip, which strips most EXIF).

This operates on the immediate contents of the picked folder. For a recursive
version that walks every subfolder of the master library, see
``ChangeDatesFromFileName.py`` — both share the same writer implementation in
``photo_lib.exif_writer``.

Run with::

    python write_exif_from_filename.py --path <folder>
    python write_exif_from_filename.py --path <folder> --dry-run

If ``--path`` is omitted, a Tk folder picker opens.

The writer also handles two subtleties:

  - **Placeholder filenames**: files named ``YYYY-01-01 00.00.00_N.ext``
    represent "year known, date/time unknown" placeholders. Their EXIF is
    bumped to ``13:00:00`` (which is 00:00 UTC in NZDT) to avoid the
    Dec-31-previous-year rollover in UTC-respecting viewers — and the file
    is renamed to match the bumped time so filename ≡ EXIF.

  - **In-sync skip**: files whose EXIF already matches what the writer would
    produce are skipped, saving Google Drive resync churn on re-runs.
"""

import argparse
import logging
import os

from photo_lib.exif_writer import write_exif_for_files
from photo_lib.logging_setup import configure_logging
from photo_lib.tk_picker import resolve_directory

logger = logging.getLogger("photo_lib")


def change_exif_date(directory: str, dry_run: bool = False):
    """Rewrite each file's EXIF/QuickTime dates from its filename.

    Operates on the immediate contents of ``directory`` only (non-recursive);
    see ``ChangeDatesFromFileName.py`` for the recursive variant. Both share
    ``photo_lib.exif_writer.write_exif_for_files`` so behavior stays in lockstep.
    """
    if not directory:
        logger.info("No directory selected. Exiting.")
        return

    file_paths = [
        os.path.join(directory, filename)
        for filename in os.listdir(directory)
        if os.path.isfile(os.path.join(directory, filename))
    ]
    write_exif_for_files(file_paths, dry_run=dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Write EXIF / QuickTime dates derived from each file's "
                    "canonical name."
    )
    parser.add_argument(
        "--path",
        help="Directory to operate on. If omitted, opens the Tk folder picker.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Summarize the planned EXIF writes without invoking exiftool.",
    )
    cli_arguments = parser.parse_args()

    configure_logging("write_exif_from_filename")
    target_directory = resolve_directory(cli_arguments.path, "Select Photos Directory")
    change_exif_date(target_directory, dry_run=cli_arguments.dry_run)
