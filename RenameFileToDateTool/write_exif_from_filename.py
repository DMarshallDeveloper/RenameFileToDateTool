"""write_exif_from_filename.py — write EXIF / QuickTime dates from each file's name.

The mirror image of ``rename_files_from_exif.py``: leaves the filenames alone
but rewrites the embedded metadata so it matches what the filename already
says. Useful when filenames are correct but EXIF dates are missing or wrong
(typical after a Google Takeout round-trip, which strips most EXIF).

Recursive by default — pointed at the master library root, it sweeps every
year folder in one pass, silently skipping any file whose name doesn't parse
as a date. Pre-filtering keeps the noise down on big trees; files that don't
look like media files are skipped without warning.

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
from photo_lib.extensions import MEDIA_EXTENSIONS, normalize_extension
from photo_lib.filename_pattern import parse_filename_datetime
from photo_lib.logging_setup import configure_logging
from photo_lib.tk_picker import resolve_directory

logger = logging.getLogger("photo_lib")


def change_exif_date(directory: str, dry_run: bool = False):
    """Recursively rewrite EXIF/QuickTime dates from each file's filename.

    Pre-filters candidates to "media files with a parseable date in the name"
    so the helper isn't spammed with skip warnings for every random file in a
    big tree. The actual write work happens in
    ``photo_lib.exif_writer.write_exif_for_files``.
    """
    if not directory:
        logger.info("No directory selected. Exiting.")
        return

    candidate_file_paths = []
    for current_dir, _subdirs, filenames in os.walk(directory):
        for filename in filenames:
            if parse_filename_datetime(filename) is None:
                continue
            file_extension = normalize_extension(os.path.splitext(filename)[1])
            if file_extension not in MEDIA_EXTENSIONS:
                continue
            candidate_file_paths.append(os.path.join(current_dir, filename))

    logger.info("Reading existing EXIF for %d files...", len(candidate_file_paths))
    # Anchor relpath to the directory being processed (not cwd) so log paths
    # render cleanly AND so this works when ``directory`` is on a different
    # mount point than cwd (e.g. tests using a tmp folder on C: while cwd is D:).
    def _log_path(file_path: str) -> str:
        return os.path.relpath(file_path, directory)
    write_exif_for_files(
        candidate_file_paths,
        dry_run=dry_run,
        path_for_log=_log_path,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Recursively rewrite EXIF / QuickTime dates from each "
                    "file's canonical name."
    )
    parser.add_argument(
        "--path",
        help="Directory to operate on (recursively). If omitted, opens the Tk "
             "folder picker.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Summarize the planned EXIF writes without invoking exiftool.",
    )
    cli_arguments = parser.parse_args()

    configure_logging("write_exif_from_filename")
    target_directory = resolve_directory(cli_arguments.path, "Select Photos Directory")
    change_exif_date(target_directory, dry_run=cli_arguments.dry_run)
