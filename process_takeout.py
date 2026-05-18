"""process_takeout.py — one-command Takeout-to-Inbox pipeline.

A Google Takeout download arrives as a folder of ``.zip`` files with nested
``Takeout/Google Photos/<album>/<year>/<file>`` structure inside. To get those
files into the master library you need to:

  1. Unzip the chunks and flatten the nested directories.
  2. Pair each media file with its ``.json`` sidecar (which holds the real
     capture time), convert that UTC timestamp to local time at the photo's
     GPS coordinates (so an overseas photo lands with its on-camera time),
     and copy the file to a destination with a canonical
     ``YYYY-MM-DD HH.MM.SS_N.ext`` name.
  3. Drop the destination into ``_Inbox/`` so the regular ingest flow
     (``ingest_inbox_to_master.py``) can fan it out into year folders.

This script runs steps 1–3 in a single command. After it finishes, the
destination folder is ready for either a manual drag into a year folder
or a run of ``ingest_inbox_to_master.py``.

Run:
    python process_takeout.py --takeout <download-folder> [--dst <staging>]

If ``--dst`` is omitted, defaults to
``<MASTER_ROOT>/_Inbox/<takeout-folder-basename>/`` so each Takeout batch lands
in its own staging subfolder you can review independently.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.join(REPO_ROOT, "RenameFileToDateTool")
sys.path.insert(0, SCRIPT_DIR)

from photo_lib.config import INBOX_FOLDER_NAME, MASTER_ROOT  # noqa: E402
from photo_lib.logging_setup import configure_logging  # noqa: E402
from photo_lib.tk_picker import resolve_directory  # noqa: E402

# Orchestrating the two existing scripts. The functions imported here are
# the pure work entry points — they don't pop dialogs or prompts.
from extract_and_flatten_takeout import flatten_takeout  # noqa: E402
from update_filename_to_date_from_google_takeout_json_metadata import (  # noqa: E402
    process_and_copy_media_files,
)

logger = logging.getLogger("photo_lib")


def default_destination(takeout_path: str) -> str:
    """Default destination: ``<MASTER_ROOT>/_Inbox/<takeout-folder-basename>/``."""
    name = os.path.basename(os.path.normpath(takeout_path))
    return os.path.join(MASTER_ROOT, INBOX_FOLDER_NAME, name)


def process(takeout_path: str, destination: str, dry_run: bool = False) -> None:
    """End-to-end Takeout → Inbox pipeline.

    Step 1 (flatten) is always destructive in the takeout source folder — it
    moves files out of the nested album structure. Step 2 (rename + copy)
    respects ``dry_run``.
    """
    takeout = Path(takeout_path)
    if not takeout.is_dir():
        logger.error("Takeout folder does not exist: %s", takeout_path)
        return

    logger.info("=" * 72)
    logger.info("STEP 1: Extract zips and flatten %s", takeout_path)
    logger.info("=" * 72)
    extracted_dir = flatten_takeout(takeout)
    logger.info("Flattened into: %s", extracted_dir)

    logger.info("")
    logger.info("=" * 72)
    logger.info("STEP 2: Pair JSON sidecars, rename, copy to %s", destination)
    logger.info("=" * 72)
    os.makedirs(destination, exist_ok=True)
    process_and_copy_media_files(str(extracted_dir), destination, dry_run=dry_run)

    logger.info("")
    logger.info("=" * 72)
    if dry_run:
        logger.info("DRY-RUN COMPLETE — no files copied.")
    else:
        logger.info("PIPELINE COMPLETE. Staged files: %s", destination)
        logger.info("Next: review the staging folder, then run")
        logger.info("      python RenameFileToDateTool/ingest_inbox_to_master.py "
                    "--master \"%s\" --inbox \"%s\"", MASTER_ROOT, destination)
    logger.info("=" * 72)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the full Takeout-to-Inbox pipeline in one command."
    )
    parser.add_argument(
        "--takeout",
        help="Folder containing Google Takeout .zip files. "
             "If omitted, opens the Tk folder picker.",
    )
    parser.add_argument(
        "--dst",
        help="Destination staging folder for the canonical-named files. "
             "Defaults to <MASTER_ROOT>/_Inbox/<takeout-folder-basename>/.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Skip the file copy in step 2 — but step 1 (extract + flatten) "
             "always runs since it's the prerequisite for step 2's matching.",
    )
    args = parser.parse_args()

    configure_logging("process_takeout")

    takeout_path = resolve_directory(args.takeout, "Select Takeout download folder")
    if not takeout_path:
        logger.error("No Takeout folder selected. Aborting.")
        sys.exit(1)

    destination = args.dst or default_destination(takeout_path)
    process(takeout_path, destination, dry_run=args.dry_run)
